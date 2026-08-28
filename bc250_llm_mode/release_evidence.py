"""C1 §4.3 + G2 §4.1 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): release
evidence records (pure).

G2 replaces the permissive schema-v1 envelope with the schema-v2 envelope:
every record carries 18 mandatory fields (identity, candidate binding, policy
+ inventory digests, issuer, environment, measurements, attachments, and a
VERIFICATION block), unknown fields are refused, secrets are detected by VALUE
patterns as well as key names, records are bounded (string length, nesting
depth, list sizes, total bytes), and every kind enforces a semantic
measurement contract.

Validation order is PINNED (so rejection codes are deterministic): schema
version, unknown fields, mandatory-field presence, types, empty content, kind
vocabulary, result, candidate version, commit binding, policy-digest binding,
timestamps, secret scan, bounds, attachment containment, subject digest
format, issuer/environment/verification structure, kind contract.

G2 §G2.3: parsing/validating a record is NOT verification. Only a
``VerifiedEvidenceRecord`` produced through the module-private verifier
sentinel (by ``verify_evidence_attestation`` or an explicitly test-only
fixture) can satisfy a release gate; the evaluator refuses raw dicts with
NOT_VERIFIED. Pure — no I/O and no trust-root fabrication: the attestation
adapter verifies the bundle's integrity and subject binding, never invents a
cryptographic trust root.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .release_policy import (
    APPROVED_VERIFICATION_MECHANISMS,
    EVIDENCE_KINDS,
    EvidenceKind,
)

EVIDENCE_SCHEMA_VERSION = 2

# --- bounds (§G2.5) ----------------------------------------------------------
_MAX_STRING_LEN = 32_768
_MAX_NESTING_DEPTH = 16
_MAX_MEASUREMENT_KEYS = 64
_MAX_SUBJECTS = 64
_MAX_ATTACHMENTS = 32
_MAX_RECORD_BYTES = 1_000_000

# The 18 mandatory schema-v2 envelope fields (§G2.1). ``expires_at`` and
# ``supersedes_evidence_id`` may be explicit null, but the KEYS must exist.
MANDATORY_FIELDS: tuple[str, ...] = (
    "evidence_schema_version", "evidence_id", "kind",
    "release_candidate_version", "source_repository", "source_commit",
    "policy_digest", "artifact_inventory_digest", "artifact_subjects",
    "issuer", "issued_at", "expires_at", "environment", "result",
    "measurements", "attachments", "verification", "supersedes_evidence_id",
)

# Kinds whose evidence must name at least one artifact subject (§G2.1).
ARTIFACT_BOUND_KINDS: frozenset[str] = frozenset({
    EvidenceKind.CLEAN_WHEEL_SMOKE.value,
    EvidenceKind.SBOM.value,
    EvidenceKind.BUILD_PROVENANCE.value,
    EvidenceKind.ARTIFACT_ATTESTATION.value,
    EvidenceKind.RELEASE_APPROVAL.value,
})

_ISSUER_TYPES: frozenset[str] = frozenset({"ci", "human", "tool", "service"})
_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "os", "architecture", "python", "runner")
_VERIFICATION_FIELDS: tuple[str, ...] = (
    "mechanism", "subject", "verifier", "verified_at", "bundle_digest")

# Field-name fragments that must never appear in an evidence record.
_FORBIDDEN_FRAGMENTS = (
    "secret", "token", "password", "passphrase", "api_key", "apikey",
    "credential", "private_key", "prompt", "completion",
)

# G2 §G2.5: credential patterns detected in string VALUES, regardless of the
# key that carries them (audit finding 7).
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"hf_[A-Za-z0-9]{20,}"),                       # Hugging Face
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),                      # GitHub PAT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),        # PEM key
    re.compile(r"\bBearer\s+[A-Za-z0-9\-_.]{8,}"),            # bearer token
    re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^@\s]+@"),   # URL userinfo
)

_HEX64 = "0123456789abcdef"


class EvidenceResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceRejectionCode(str, Enum):
    """Stable reasons an evidence record is rejected (never satisfies a gate).

    G2 extends the vocabulary with the schema-v2 envelope, bounds, structure,
    verification-boundary, and set-level codes.
    """

    MALFORMED = "MALFORMED"
    UNKNOWN_KIND = "UNKNOWN_KIND"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    RESULT_NOT_PASS = "RESULT_NOT_PASS"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    SOURCE_COMMIT_MISMATCH = "SOURCE_COMMIT_MISMATCH"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    DUPLICATE = "DUPLICATE"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    SECRET_MATERIAL = "SECRET_MATERIAL"
    BAD_DIGEST = "BAD_DIGEST"
    BAD_TIMESTAMP = "BAD_TIMESTAMP"
    # G2: schema-v2 envelope + verification boundary.
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    EMPTY_FIELD = "EMPTY_FIELD"
    BAD_FIELD_TYPE = "BAD_FIELD_TYPE"
    RECORD_OVERSIZE = "RECORD_OVERSIZE"
    BAD_ISSUER = "BAD_ISSUER"
    BAD_ENVIRONMENT = "BAD_ENVIRONMENT"
    BAD_VERIFICATION = "BAD_VERIFICATION"
    UNVERIFIED_ATTESTATION = "UNVERIFIED_ATTESTATION"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    INVENTORY_DIGEST_MISMATCH = "INVENTORY_DIGEST_MISMATCH"
    POLICY_DIGEST_MISMATCH = "POLICY_DIGEST_MISMATCH"
    SUPERSEDED_UNKNOWN_TARGET = "SUPERSEDED_UNKNOWN_TARGET"
    SUPERSESSION_CYCLE = "SUPERSESSION_CYCLE"
    SUPERSESSION_INVALID = "SUPERSESSION_INVALID"
    DUPLICATE_COVERAGE = "DUPLICATE_COVERAGE"
    KIND_CONTRACT_UNMET = "KIND_CONTRACT_UNMET"
    NOT_VERIFIED = "NOT_VERIFIED"


def _is_hex64(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in _HEX64 for c in value))


def _is_full_sha256(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    v = value[7:] if value.startswith("sha256:") else value
    return len(v) == 64 and all(c in _HEX64 for c in v.lower())


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # must be timezone-aware
    return parsed


# Cheap substrings that must be present before the (potentially expensive)
# credential regexes run; strings longer than the scan cap are refused by the
# bounds check immediately afterwards, so the cap stays fail-closed.
_SECRET_TRIGGERS: tuple[str, ...] = (
    "hf_", "ghp_", "-----BEGIN", "Bearer", "://")
_SECRET_SCAN_CAP = 65_536


def _contains_secret(obj: Any, path: str = "$") -> str | None:
    """Secret scan over keys AND string values (§G2.5)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            for frag in _FORBIDDEN_FRAGMENTS:
                if frag in lowered:
                    return f"secret-like field {key!r} at {path}"
            found = _contains_secret(value, f"{path}.{key}")
            if found:
                return found
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found = _contains_secret(value, f"{path}[{i}]")
            if found:
                return found
    elif isinstance(obj, str):
        if any(trigger in obj for trigger in _SECRET_TRIGGERS):
            sample = obj[:_SECRET_SCAN_CAP]
            for pattern in _SECRET_VALUE_PATTERNS:
                if pattern.search(sample):
                    return f"credential pattern in value at {path}"
    return None


def _bounds_violation(obj: Any, depth: int = 0) -> str | None:
    """Bounded records: string length, nesting depth, list sizes (§G2.5)."""
    if depth > _MAX_NESTING_DEPTH:
        return f"nesting deeper than {_MAX_NESTING_DEPTH}"
    if isinstance(obj, str):
        if len(obj) > _MAX_STRING_LEN:
            return f"string longer than {_MAX_STRING_LEN}"
    elif isinstance(obj, dict):
        for key, value in obj.items():
            found = _bounds_violation(key, depth + 1) or \
                _bounds_violation(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _bounds_violation(value, depth + 1)
            if found:
                return found
    return None


def _contained_attachment_paths(record: dict[str, Any]) -> str | None:
    for att in record.get("attachments") or []:
        if not isinstance(att, dict):
            return "attachment must be an object"
        loc = str(att.get("location_hint") or att.get("name") or "")
        if not loc:
            continue
        if len(loc) > 512:
            return f"attachment path too long: {loc[:40]}…"
        if loc.startswith("/") or loc.startswith("\\"):
            return f"absolute attachment path: {loc!r}"
        if ".." in loc.replace("\\", "/").split("/"):
            return f"path-traversing attachment: {loc!r}"
    return None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# --- kind-specific measurement contracts (§G2.2) ------------------------------

def _test_suite_contract(m: dict[str, Any]) -> bool:
    collected, passed = m.get("collected"), m.get("passed")
    return (_is_count(collected) and collected >= 1
            and _is_count(passed) and 0 <= passed <= collected)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


KIND_MEASUREMENT_CONTRACTS: dict[str, Any] = {
    EvidenceKind.DEFAULT_TEST_SUITE.value: _test_suite_contract,
    EvidenceKind.SLOW_SECURITY_STRESS.value: _test_suite_contract,
    EvidenceKind.UPGRADE_MATRIX.value: _test_suite_contract,
    EvidenceKind.CLEAN_WHEEL_SMOKE.value:
        lambda m: m.get("smoke_result") == "PASS",
    EvidenceKind.SBOM.value:
        lambda m: _is_count(m.get("component_count"))
        and m["component_count"] >= 1,
    EvidenceKind.BUILD_PROVENANCE.value:
        lambda m: _non_empty_str(m.get("builder")),
    EvidenceKind.ARTIFACT_ATTESTATION.value:
        lambda m: _non_empty_str(m.get("attestation_format")),
    EvidenceKind.PACKAGE_PUBLISH_ATTESTATION.value:
        lambda m: _non_empty_str(m.get("attestation_format")),
    EvidenceKind.CONTAINER_IDENTITY.value:
        lambda m: _non_empty_str(m.get("image_ref")),
    EvidenceKind.BACKUP_RESTORE_HARDWARE.value:
        lambda m: m.get("round_trip_result") == "PASS",
    EvidenceKind.HARDWARE_QUALIFICATION.value:
        lambda m: _non_empty_str(m.get("hardware_model")),
    EvidenceKind.SOAK_TEST.value:
        lambda m: isinstance(m.get("duration_hours"), (int, float))
        and not isinstance(m.get("duration_hours"), bool)
        and m["duration_hours"] >= 1,
    EvidenceKind.SECURITY_REVIEW.value:
        lambda m: _non_empty_str(m.get("review_scope")),
    EvidenceKind.HUMAN_ACCEPTANCE.value:
        lambda m: _non_empty_str(m.get("acceptance_scope")),
    EvidenceKind.KNOWN_LIMITATION_ACCEPTANCE.value:
        lambda m: _non_empty_str(m.get("capability")),
    EvidenceKind.DOCUMENTATION_RECONCILIATION.value:
        lambda m: _is_count(m.get("documents_checked"))
        and m["documents_checked"] >= 1,
    EvidenceKind.SOURCE_CHECKOUT.value:
        lambda m: m.get("checkout_clean") is True,
    EvidenceKind.RELEASE_APPROVAL.value:
        lambda m: _non_empty_str(m.get("approver_role")),
}


def validate_evidence_record(
    record: dict[str, Any],
    *,
    candidate_version: str,
    source_commit: str,
    policy_digest: str,
    now: datetime | None = None,
    supersedes: set[str] | None = None,
    seen_ids: set[str] | None = None,
    approved_mechanisms: frozenset[str] | None = None,
) -> tuple[bool, str | None]:
    """Validate one schema-v2 evidence record against the candidate.

    G2: ``source_commit`` and ``policy_digest`` are MANDATORY binding inputs —
    there is no unbound validation path. Returns ``(ok, rejection_code)``;
    ``ok`` False always carries a stable code. Order is pinned (see module
    docstring) so codes are deterministic.
    """
    if not isinstance(record, dict):
        return False, EvidenceRejectionCode.MALFORMED.value

    # 1. schema version (an absent key is a missing field, not a mismatch)
    if "evidence_schema_version" not in record:
        return False, EvidenceRejectionCode.MISSING_FIELD.value
    if record["evidence_schema_version"] != EVIDENCE_SCHEMA_VERSION:
        return False, EvidenceRejectionCode.SCHEMA_VERSION_MISMATCH.value

    # 2. unknown fields (never silently accepted)
    for key in record:
        if key not in MANDATORY_FIELDS:
            return False, EvidenceRejectionCode.UNKNOWN_FIELD.value

    # 3. mandatory presence (explicit null allowed only where the schema says)
    for key in MANDATORY_FIELDS:
        if key not in record:
            return False, EvidenceRejectionCode.MISSING_FIELD.value

    # 4. types
    expires_ok = (record["expires_at"] is None
                  or isinstance(record["expires_at"], str))
    supersedes_ok = (record["supersedes_evidence_id"] is None
                     or isinstance(record["supersedes_evidence_id"], str))
    types_ok = (
        isinstance(record["evidence_schema_version"], int)
        and isinstance(record["evidence_id"], str)
        and isinstance(record["kind"], str)
        and isinstance(record["release_candidate_version"], str)
        and isinstance(record["source_repository"], str)
        and isinstance(record["source_commit"], str)
        and isinstance(record["policy_digest"], str)
        and isinstance(record["artifact_inventory_digest"], str)
        and isinstance(record["artifact_subjects"], list)
        and isinstance(record["issuer"], dict)
        and isinstance(record["issued_at"], str)
        and expires_ok
        and isinstance(record["environment"], dict)
        and isinstance(record["result"], str)
        and isinstance(record["measurements"], dict)
        and isinstance(record["attachments"], list)
        and isinstance(record["verification"], dict)
        and supersedes_ok
    )
    if not types_ok:
        return False, EvidenceRejectionCode.BAD_FIELD_TYPE.value

    # 5. empty content
    if record["evidence_id"].strip() == "":
        return False, EvidenceRejectionCode.EMPTY_FIELD.value
    if (record["kind"] in ARTIFACT_BOUND_KINDS
            and len(record["artifact_subjects"]) == 0):
        return False, EvidenceRejectionCode.EMPTY_FIELD.value

    # 6. kind vocabulary
    if record["kind"] not in EVIDENCE_KINDS:
        return False, EvidenceRejectionCode.UNKNOWN_KIND.value

    # 7. result
    if record["result"] != EvidenceResult.PASS.value:
        return False, EvidenceRejectionCode.RESULT_NOT_PASS.value

    # 8. candidate version binding
    if record["release_candidate_version"] != candidate_version:
        return False, EvidenceRejectionCode.VERSION_MISMATCH.value

    # 9. commit binding (mandatory after G2)
    if record["source_commit"] != source_commit:
        return False, EvidenceRejectionCode.SOURCE_COMMIT_MISMATCH.value

    # 10. policy-digest binding (mandatory after G2)
    if record["policy_digest"] != policy_digest:
        return False, EvidenceRejectionCode.POLICY_DIGEST_MISMATCH.value

    # 11. timestamps
    reference = now or datetime.now(timezone.utc)
    issued = _parse_ts(record["issued_at"])
    if issued is None or issued > reference:
        return False, EvidenceRejectionCode.BAD_TIMESTAMP.value
    expires_raw = record["expires_at"]
    if expires_raw is not None:
        expires = _parse_ts(expires_raw)
        if expires is None:
            return False, EvidenceRejectionCode.BAD_TIMESTAMP.value
        if expires <= reference:
            return False, EvidenceRejectionCode.EXPIRED.value

    evidence_id = record["evidence_id"]
    if seen_ids is not None and evidence_id in seen_ids:
        return False, EvidenceRejectionCode.DUPLICATE.value
    if supersedes and evidence_id in supersedes:
        return False, EvidenceRejectionCode.SUPERSEDED.value

    # 12. secret scan (keys AND values)
    if _contains_secret(record):
        return False, EvidenceRejectionCode.SECRET_MATERIAL.value

    # 13. bounds
    if _bounds_violation(record):
        return False, EvidenceRejectionCode.RECORD_OVERSIZE.value
    if len(record["measurements"]) > _MAX_MEASUREMENT_KEYS:
        return False, EvidenceRejectionCode.RECORD_OVERSIZE.value
    if len(record["artifact_subjects"]) > _MAX_SUBJECTS:
        return False, EvidenceRejectionCode.RECORD_OVERSIZE.value
    if len(record["attachments"]) > _MAX_ATTACHMENTS:
        return False, EvidenceRejectionCode.RECORD_OVERSIZE.value
    try:
        serialized = json.dumps(record, sort_keys=True)
    except (TypeError, ValueError):
        return False, EvidenceRejectionCode.BAD_FIELD_TYPE.value
    if len(serialized.encode("utf-8")) > _MAX_RECORD_BYTES:
        return False, EvidenceRejectionCode.RECORD_OVERSIZE.value

    # 14. attachment containment
    traversal = _contained_attachment_paths(record)
    if traversal:
        return False, EvidenceRejectionCode.PATH_TRAVERSAL.value

    # 15. subject digest format
    for subject in record["artifact_subjects"]:
        if not isinstance(subject, dict):
            return False, EvidenceRejectionCode.BAD_DIGEST.value
        if not _is_full_sha256(subject.get("sha256")):
            return False, EvidenceRejectionCode.BAD_DIGEST.value

    # 16. issuer / environment / verification structure
    issuer = record["issuer"]
    if (set(issuer) != {"type", "identity"}
            or issuer.get("type") not in _ISSUER_TYPES
            or not _non_empty_str(issuer.get("identity"))):
        return False, EvidenceRejectionCode.BAD_ISSUER.value

    environment = record["environment"]
    if (set(environment) != set(_ENVIRONMENT_FIELDS)
            or not all(_non_empty_str(environment.get(f))
                       for f in _ENVIRONMENT_FIELDS)):
        return False, EvidenceRejectionCode.BAD_ENVIRONMENT.value

    mechanisms = (approved_mechanisms
                  if approved_mechanisms is not None
                  else APPROVED_VERIFICATION_MECHANISMS)
    verification = record["verification"]
    if set(verification) != set(_VERIFICATION_FIELDS):
        return False, EvidenceRejectionCode.BAD_VERIFICATION.value
    mechanism = verification.get("mechanism")
    if not _non_empty_str(mechanism) or mechanism not in mechanisms:
        return False, EvidenceRejectionCode.BAD_VERIFICATION.value
    if not _is_full_sha256(verification.get("subject")):
        return False, EvidenceRejectionCode.BAD_VERIFICATION.value
    if not _non_empty_str(verification.get("verifier")):
        return False, EvidenceRejectionCode.BAD_VERIFICATION.value
    verified_at = _parse_ts(verification.get("verified_at"))
    if verified_at is None or verified_at > reference:
        return False, EvidenceRejectionCode.BAD_VERIFICATION.value
    if not _is_full_sha256(verification.get("bundle_digest")):
        return False, EvidenceRejectionCode.BAD_VERIFICATION.value

    # 17. kind-specific measurement contract
    contract = KIND_MEASUREMENT_CONTRACTS.get(record["kind"])
    if contract is not None and not contract(record["measurements"]):
        return False, EvidenceRejectionCode.KIND_CONTRACT_UNMET.value

    return True, None


# --- G2 §G2.3: the verified-evidence boundary --------------------------------

_VERIFIER_KEY = object()


@dataclass(frozen=True)
class VerifiedEvidenceRecord:
    """Evidence whose verification block was ACTUALLY verified (§G2.3).

    Parsing or validating a dict is not verification. Only
    ``verify_evidence_attestation`` (or an explicitly test-only fixture
    holding the module-private sentinel) may produce this type; the evaluator
    refuses anything else with NOT_VERIFIED.
    """

    record: dict[str, Any]
    mechanism: str
    verifier: str
    verified_at: str
    bundle_digest: str
    _verifier_key: object = None

    def __post_init__(self) -> None:
        if self._verifier_key is not _VERIFIER_KEY:
            raise ValueError(
                "a VerifiedEvidenceRecord can only be produced by "
                "verify_evidence_attestation, not constructed directly")


class EvidenceVerificationError(ValueError):
    """Fail-closed attestation verification failure (carries a stable code)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def bundle_digest_of(payload: dict[str, Any]) -> str:
    """Canonical sha256 digest of an attestation bundle payload."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def verify_evidence_attestation(
    record: dict[str, Any],
    *,
    bundle: dict[str, Any],
    candidate_version: str,
    source_commit: str,
    policy_digest: str,
    now: datetime | None = None,
    approved_mechanisms: frozenset[str] | None = None,
) -> VerifiedEvidenceRecord:
    """G2 §G2.4: verify a record's attestation bundle and promote it to a
    ``VerifiedEvidenceRecord``.

    The adapter verifies what a repository-local verifier CAN verify without
    inventing a trust root: the record passes full schema-v2 validation; the
    bundle is structurally complete; the bundle's mechanism matches an
    approved mechanism named in the record; the bundle subject equals the
    record's verification subject AND one of the record's artifact subjects;
    and the claimed bundle digest equals the canonical digest of the bundle
    payload. Anything else raises ``EvidenceVerificationError``. Remote
    transparency-log verification stays CI/owner-gated (never faked here).
    """
    ok, code = validate_evidence_record(
        record, candidate_version=candidate_version,
        source_commit=source_commit, policy_digest=policy_digest, now=now,
        approved_mechanisms=approved_mechanisms)
    if not ok:
        raise EvidenceVerificationError(
            code or EvidenceRejectionCode.MALFORMED.value,
            f"record failed validation: {code}")

    verification = record["verification"]
    if not isinstance(bundle, dict):
        raise EvidenceVerificationError(
            EvidenceRejectionCode.UNVERIFIED_ATTESTATION.value,
            "attestation bundle must be an object")
    if bundle.get("mechanism") != verification["mechanism"]:
        raise EvidenceVerificationError(
            EvidenceRejectionCode.BAD_VERIFICATION.value,
            "bundle mechanism does not match the record's mechanism")
    subject = verification["subject"]
    subject_bare = subject[7:] if subject.startswith("sha256:") else subject
    if bundle.get("subject") not in (subject, subject_bare):
        raise EvidenceVerificationError(
            EvidenceRejectionCode.SUBJECT_MISMATCH.value,
            "bundle subject does not match the verification subject")
    artifact_shas = set()
    for art in record["artifact_subjects"]:
        sha = str(art.get("sha256") or "")
        artifact_shas.add(sha[7:] if sha.startswith("sha256:") else sha)
    if subject_bare not in artifact_shas:
        raise EvidenceVerificationError(
            EvidenceRejectionCode.SUBJECT_MISMATCH.value,
            "verification subject is not one of the record's artifact subjects")
    payload = bundle.get("payload")
    if not isinstance(payload, dict):
        raise EvidenceVerificationError(
            EvidenceRejectionCode.UNVERIFIED_ATTESTATION.value,
            "attestation bundle must carry a verifiable payload")
    if bundle.get("bundle_digest") != bundle_digest_of(payload):
        raise EvidenceVerificationError(
            EvidenceRejectionCode.UNVERIFIED_ATTESTATION.value,
            "claimed bundle digest does not match the bundle payload")

    return VerifiedEvidenceRecord(
        record=record,
        mechanism=verification["mechanism"],
        verifier=verification["verifier"],
        verified_at=verification["verified_at"],
        bundle_digest=verification["bundle_digest"],
        _verifier_key=_VERIFIER_KEY,
    )


# --- G2 §G2.6: set-level validation ------------------------------------------

def _unwrap(item: Any) -> Any:
    return item.record if isinstance(item, VerifiedEvidenceRecord) else item


def collect_superseded_ids(records: list[Any]) -> set[str]:
    """The set of evidence_ids that some record explicitly supersedes."""
    superseded: set[str] = set()
    for item in records:
        record = _unwrap(item)
        if isinstance(record, dict):
            target = record.get("supersedes_evidence_id")
            if target:
                superseded.add(target)
    return superseded


def validate_evidence_set(
    records: list[Any],
    *,
    candidate_version: str | None = None,
    source_commit: str | None = None,
    policy_digest: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    """G2 §G2.6: set-level evidence rules.

    Refuses duplicate evidence ids, supersession targets that do not exist in
    the set, cross-kind supersession, supersession cycles, and duplicate
    coverage (two records of one kind with no supersession relation). When the
    candidate binding inputs are supplied, every record is additionally
    validated against the full schema-v2 envelope first. Returns
    ``(ok, problems)`` where problems are stable ``CODE:subject`` strings.
    """
    problems: list[str] = []
    parsed: list[dict[str, Any]] = []
    bound = (candidate_version is not None and source_commit is not None
             and policy_digest is not None)

    for index, item in enumerate(records):
        record = _unwrap(item)
        if not isinstance(record, dict):
            problems.append(f"{EvidenceRejectionCode.MALFORMED.value}:record[{index}]")
            continue
        if bound:
            ok, code = validate_evidence_record(
                record, candidate_version=candidate_version,
                source_commit=source_commit, policy_digest=policy_digest,
                now=now)
            if not ok:
                rid = record.get("evidence_id") or f"record[{index}]"
                problems.append(f"{code}:{rid}")
                continue
        parsed.append(record)

    # duplicate evidence ids
    seen: dict[str, int] = {}
    for record in parsed:
        rid = record.get("evidence_id")
        if rid:
            seen[rid] = seen.get(rid, 0) + 1
    for rid in sorted(r for r, count in seen.items() if count > 1):
        problems.append(f"DUPLICATE_EVIDENCE_ID:{rid}")

    by_id = {r["evidence_id"]: r for r in parsed if r.get("evidence_id")}

    # supersession targets must exist and share the kind
    for record in parsed:
        target = record.get("supersedes_evidence_id")
        if not target:
            continue
        rid = record.get("evidence_id") or "<missing-id>"
        if target not in by_id:
            problems.append(
                f"{EvidenceRejectionCode.SUPERSEDED_UNKNOWN_TARGET.value}:{rid}")
        elif by_id[target].get("kind") != record.get("kind"):
            problems.append(
                f"{EvidenceRejectionCode.SUPERSESSION_INVALID.value}:{rid}")

    # supersession cycles
    for record in parsed:
        rid = record.get("evidence_id")
        if not rid:
            continue
        chain: set[str] = set()
        current: str | None = rid
        while current and current in by_id:
            if current in chain:
                problems.append(
                    f"{EvidenceRejectionCode.SUPERSESSION_CYCLE.value}:{rid}")
                break
            chain.add(current)
            current = by_id[current].get("supersedes_evidence_id")

    # duplicate coverage: one kind, several unrelated records
    superseded_ids = {
        r["supersedes_evidence_id"] for r in parsed
        if r.get("supersedes_evidence_id")}
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for record in parsed:
        kind = record.get("kind")
        if kind:
            by_kind.setdefault(kind, []).append(record)
    for kind in sorted(by_kind):
        group = by_kind[kind]
        latest = [r for r in group
                  if r.get("evidence_id") and r["evidence_id"] not in superseded_ids]
        if len(group) > 1 and len(latest) > 1:
            problems.append(
                f"{EvidenceRejectionCode.DUPLICATE_COVERAGE.value}:{kind}")

    return (not problems), problems
