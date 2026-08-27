"""C1 §4.3 (V1_0_RELEASE_CLOSURE plan): release evidence records (pure).

Immutable, validated evidence records bound to the exact release candidate
version and source commit. Validation is fail-closed: unknown kinds, non-PASS
results, expired/superseded/duplicated records, wrong version/commit, non-
contained attachment paths, secret-like fields, and malformed digests are all
rejected with a stable reason code. Pure — no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .release_policy import EVIDENCE_KINDS

EVIDENCE_SCHEMA_VERSION = 1

_MAX_ATTACHMENT_PATH_LEN = 512
_MAX_MEASUREMENT_KEYS = 64

# Field-name fragments that must never appear in an evidence record.
_FORBIDDEN_FRAGMENTS = (
    "secret", "token", "password", "passphrase", "api_key", "apikey",
    "credential", "private_key", "prompt", "completion",
)


class EvidenceResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceRejectionCode(str, Enum):
    """Stable reasons an evidence record is rejected (never satisfies a gate)."""

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


def _is_full_sha256(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    v = value[7:] if value.startswith("sha256:") else value
    return len(v) == 64 and all(c in "0123456789abcdef" for c in v.lower())


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


def _contains_secret(obj: Any, path: str = "$") -> str | None:
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
    return None


def _contained_attachment_paths(record: dict[str, Any]) -> str | None:
    for att in record.get("attachments") or []:
        loc = str(att.get("location_hint") or att.get("name") or "")
        if not loc:
            continue
        if len(loc) > _MAX_ATTACHMENT_PATH_LEN:
            return f"attachment path too long: {loc[:40]}…"
        if loc.startswith("/") or loc.startswith("\\"):
            return f"absolute attachment path: {loc!r}"
        if ".." in loc.replace("\\", "/").split("/"):
            return f"path-traversing attachment: {loc!r}"
    return None


def validate_evidence_record(
    record: dict[str, Any],
    *,
    candidate_version: str,
    source_commit: str | None = None,
    now: datetime | None = None,
    supersedes: set[str] | None = None,
    seen_ids: set[str] | None = None,
) -> tuple[bool, str | None]:
    """Validate one evidence record against the candidate. Returns
    ``(ok, rejection_code)``; ``ok`` False always carries a stable code."""
    if not isinstance(record, dict):
        return False, EvidenceRejectionCode.MALFORMED.value

    if record.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION:
        return False, EvidenceRejectionCode.SCHEMA_VERSION_MISMATCH.value

    kind = record.get("kind")
    if kind not in EVIDENCE_KINDS:
        return False, EvidenceRejectionCode.UNKNOWN_KIND.value

    result = record.get("result")
    if result != EvidenceResult.PASS.value:
        return False, EvidenceRejectionCode.RESULT_NOT_PASS.value

    if record.get("release_candidate_version") != candidate_version:
        return False, EvidenceRejectionCode.VERSION_MISMATCH.value

    if source_commit is not None and record.get("source_commit") != source_commit:
        return False, EvidenceRejectionCode.SOURCE_COMMIT_MISMATCH.value

    # Timestamps: issued_at required + aware; expires_at honored.
    issued = _parse_ts(record.get("issued_at"))
    if issued is None:
        return False, EvidenceRejectionCode.BAD_TIMESTAMP.value
    reference = now or datetime.now(timezone.utc)
    expires_raw = record.get("expires_at")
    if expires_raw not in (None, ""):
        expires = _parse_ts(expires_raw)
        if expires is None or expires <= reference:
            return False, EvidenceRejectionCode.EXPIRED.value

    evidence_id = record.get("evidence_id")
    if seen_ids is not None and evidence_id in seen_ids:
        return False, EvidenceRejectionCode.DUPLICATE.value
    if supersedes and evidence_id in supersedes:
        return False, EvidenceRejectionCode.SUPERSEDED.value

    # Digests must be full lowercase sha256.
    for subject in record.get("artifact_subjects") or []:
        if not _is_full_sha256(subject.get("sha256")):
            return False, EvidenceRejectionCode.BAD_DIGEST.value

    traversal = _contained_attachment_paths(record)
    if traversal:
        return False, EvidenceRejectionCode.PATH_TRAVERSAL.value

    secret = _contains_secret(record)
    if secret:
        return False, EvidenceRejectionCode.SECRET_MATERIAL.value

    measurements = record.get("measurements")
    if isinstance(measurements, dict) and len(measurements) > _MAX_MEASUREMENT_KEYS:
        return False, EvidenceRejectionCode.MALFORMED.value

    return True, None


def collect_superseded_ids(records: list[dict[str, Any]]) -> set[str]:
    """The set of evidence_ids that some record explicitly supersedes."""
    superseded: set[str] = set()
    for record in records:
        if isinstance(record, dict):
            target = record.get("supersedes_evidence_id")
            if target:
                superseded.add(target)
    return superseded
