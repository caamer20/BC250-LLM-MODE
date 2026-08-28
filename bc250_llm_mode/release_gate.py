"""C1 §4.5 (V1_0_RELEASE_CLOSURE plan): fail-closed release evaluator (pure).

``evaluate_release`` derives the release decision ONLY from validated,
candidate-bound evidence records — never from caller-supplied approval
booleans. It is deterministic (independent of evidence input order) and
fail-closed: any missing required evidence, unavailable mandatory capability,
or unaccepted limitation yields stable blocking codes. ``check_release_checkout``
is a read-only strict-checkout gate that rejects untracked build inputs without
deleting the developer's files.

G1 (§4/§7, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): the evaluator is the
SOLE eligibility authority.

* Every evaluation requires an immutable ``CandidateIdentity`` (full 40-char
  lowercase commit, full ref, repository identity, policy digest) and an
  ``ArtifactInventory``. There is NO sourceless evaluation path: the legacy
  optional ``source_commit=None`` / ``candidate_version=`` surface is deleted.
* The candidate's policy digest must equal the evaluating policy's digest
  (POLICY_DIGEST_MISMATCH blocks), so a caller-supplied weaker policy can
  never qualify a candidate bound to the reviewed digest.
* A FINAL release version must ride exactly its protected tag
  ``refs/tags/v<version>`` (CANDIDATE_REF_MISMATCH blocks); a moving branch
  name is never sufficient identity for a final release.
* The unavailable-capability set is derived from the reviewed product state
  (``release_state.KNOWN_UNAVAILABLE_CAPABILITIES``), never a caller default.
* The decision carries the candidate identity + inventory digest and never
  serializes the private evaluator marker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import release_state
from .release_artifacts import ArtifactInventory
from .release_evidence import (
    EvidenceRejectionCode,
    VerifiedEvidenceRecord,
    collect_superseded_ids,
    validate_evidence_record,
)
from .release_policy import (
    EvidenceKind,
    ReleaseGateCode,
    ReleasePolicyV1,
    default_release_policy,
)

RELEASE_DECISION_SCHEMA_VERSION = 3

# Module-private sentinel: only evaluate_release may produce an eligible
# decision (§4.5 — no caller instantiates "eligible" directly).
_EVALUATOR_KEY = object()

_FULL_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
# A FINAL release version is pure dotted numerics (no rc/dev/a/b/post suffix).
_FINAL_VERSION_RE = re.compile(r"[0-9]+(\.[0-9]+)*")


def is_final_release_version(version: str) -> bool:
    """True when the version names a final release (e.g. ``1.0.0``), not a
    pre-release such as ``1.0.0rc1`` or ``0.9.0.dev0``."""
    return bool(isinstance(version, str) and _FINAL_VERSION_RE.fullmatch(version))


def final_tag_ref(version: str) -> str:
    """The exact protected ref a final release must run from."""
    return f"refs/tags/v{version}"


@dataclass(frozen=True)
class CandidateIdentity:
    """The immutable identity of one release candidate (plan §4.2).

    Construction is fail-closed: abbreviated/malformed/uppercase/all-zero
    commit hashes, refs that are not full ``refs/…`` names, empty version or
    repository identities, and malformed policy digests are refused with
    ``ValueError``. A FINAL release version must additionally ride its exact
    protected tag (``final_ref_violation``; enforced by the evaluator).
    """

    version: str
    source_commit: str
    source_ref: str
    repository: str
    policy_digest: str

    def __post_init__(self) -> None:
        if (not isinstance(self.version, str) or not self.version
                or len(self.version) > 64
                or any(c.isspace() for c in self.version)):
            raise ValueError(
                "candidate version must be a non-empty bounded string "
                "without whitespace")
        if (not isinstance(self.source_commit, str)
                or not _FULL_GIT_SHA_RE.fullmatch(self.source_commit)):
            raise ValueError(
                "candidate source_commit must be a full 40-character "
                "lowercase git SHA")
        if set(self.source_commit) == {"0"}:
            raise ValueError("candidate source_commit must not be all zeros")
        if (not isinstance(self.source_ref, str)
                or not self.source_ref.startswith("refs/")
                or len(self.source_ref) > 256
                or ".." in self.source_ref.split("/")):
            raise ValueError(
                "candidate source_ref must be a full ref under refs/")
        if (not isinstance(self.repository, str) or not self.repository
                or len(self.repository) > 256):
            raise ValueError("candidate repository identity is required")
        if (not isinstance(self.policy_digest, str)
                or not self.policy_digest.startswith("sha256:")
                or not _HEX64_RE.fullmatch(self.policy_digest[7:])):
            raise ValueError(
                "candidate policy_digest must be sha256:<64 lowercase hex>")

    def final_ref_violation(self) -> bool:
        """True when a FINAL version does not ride its exact protected tag."""
        return (is_final_release_version(self.version)
                and self.source_ref != final_tag_ref(self.version))


@dataclass(frozen=True)
class ReleaseDecision:
    """The evaluator's verdict. Eligible decisions cannot be constructed
    directly — only returned by ``evaluate_release``."""

    eligible_for_rc: bool
    eligible_for_1_0_0: bool
    blocking_codes: tuple[str, ...] = field(default_factory=tuple)
    blocking_explanations: tuple[str, ...] = field(default_factory=tuple)
    accepted_limitations: tuple[str, ...] = field(default_factory=tuple)
    candidate_version: str = ""
    source_commit: str | None = None
    source_ref: str = ""
    repository: str = ""
    inventory_digest: str = ""
    evidence_used: tuple[str, ...] = field(default_factory=tuple)
    evidence_rejected: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    policy_digest: str = ""
    decision_schema_version: int = RELEASE_DECISION_SCHEMA_VERSION
    _evaluator_key: object = None

    def __post_init__(self) -> None:
        if (self.eligible_for_rc or self.eligible_for_1_0_0) and \
                self._evaluator_key is not _EVALUATOR_KEY:
            raise ValueError(
                "an eligible ReleaseDecision can only be produced by "
                "evaluate_release, not constructed directly")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_schema_version": self.decision_schema_version,
            "eligible_for_rc": self.eligible_for_rc,
            "eligible_for_1_0_0": self.eligible_for_1_0_0,
            "blocking_codes": list(self.blocking_codes),
            "blocking_explanations": list(self.blocking_explanations),
            "accepted_limitations": list(self.accepted_limitations),
            "candidate_version": self.candidate_version,
            "source_commit": self.source_commit,
            "source_ref": self.source_ref,
            "repository": self.repository,
            "inventory_digest": self.inventory_digest,
            "evidence_used": list(self.evidence_used),
            "evidence_rejected": [list(pair) for pair in self.evidence_rejected],
            "policy_digest": self.policy_digest,
        }


_EXPLANATIONS: dict[str, str] = {
    ReleaseGateCode.MILESTONE_EVIDENCE_MISSING.value: "release approval/milestone evidence is missing",
    ReleaseGateCode.TEST_EVIDENCE_MISSING.value: "default/slow/upgrade test evidence is missing",
    ReleaseGateCode.CLEAN_WHEEL_EVIDENCE_MISSING.value: "clean-wheel smoke evidence is missing",
    ReleaseGateCode.SBOM_MISSING_OR_MISMATCHED.value: "SBOM evidence is missing or does not match the artifacts",
    ReleaseGateCode.PROVENANCE_MISSING_OR_MISMATCHED.value: "build provenance evidence is missing or mismatched",
    ReleaseGateCode.SIGNATURE_MISSING_OR_INVALID.value: "artifact/publish attestation or signature is missing or invalid",
    ReleaseGateCode.BACKUP_RESTORE_EVIDENCE_MISSING.value: "backup/restore hardware evidence is missing",
    ReleaseGateCode.HARDWARE_QUALIFICATION_MISSING.value: "hardware qualification evidence is missing",
    ReleaseGateCode.SOAK_EVIDENCE_MISSING.value: "soak-test evidence is missing",
    ReleaseGateCode.SECURITY_REVIEW_MISSING.value: "security review evidence is missing",
    ReleaseGateCode.HUMAN_ACCEPTANCE_MISSING.value: "human acceptance evidence is missing",
    ReleaseGateCode.CAPABILITY_UNAVAILABLE.value: "a mandatory capability is still unavailable",
    ReleaseGateCode.LIMITATION_ACCEPTANCE_MISSING.value: "a declared limitation lacks a reviewed acceptance record",
    ReleaseGateCode.DOCUMENTATION_DRIFT.value: "documentation reconciliation evidence is missing",
    ReleaseGateCode.REPOSITORY_NOT_PUBLISHED.value: "source-checkout/published-repository evidence is missing",
    ReleaseGateCode.CANDIDATE_REF_MISMATCH.value: "a final release must run from its exact protected tag refs/tags/v<version>",
    ReleaseGateCode.POLICY_DIGEST_MISMATCH.value: "the candidate's policy digest does not match the evaluating policy",
}


def evaluate_release(
    *,
    evidence: list[Any],
    candidate: CandidateIdentity,
    artifacts: ArtifactInventory,
    policy: ReleasePolicyV1 | None = None,
) -> ReleaseDecision:
    """Pure, deterministic, fail-closed release evaluation.

    G1: the SOLE eligibility authority. Requires a full ``CandidateIdentity``
    and an ``ArtifactInventory`` — there is no sourceless or artifact-less
    qualifying path. The candidate's policy digest must match the evaluating
    policy, and a final release must ride its exact protected tag.

    G2 §G2.3: only verifier-produced ``VerifiedEvidenceRecord`` items can
    satisfy a gate; raw/validated dict records are refused with NOT_VERIFIED.
    """
    if not isinstance(candidate, CandidateIdentity):
        raise TypeError(
            "evaluate_release requires a CandidateIdentity — sourceless "
            "evaluation no longer exists")
    if not isinstance(artifacts, ArtifactInventory):
        raise TypeError("evaluate_release requires an ArtifactInventory")
    policy = policy or default_release_policy()

    blocking: set[str] = set()

    # Candidate/policy binding (G1.3): a weaker caller policy can never
    # qualify a candidate bound to the reviewed digest.
    if candidate.policy_digest != policy.policy_digest():
        blocking.add(ReleaseGateCode.POLICY_DIGEST_MISMATCH.value)

    # Final-tag rule (G1.2): a final release rides exactly its protected tag.
    if candidate.final_ref_violation():
        blocking.add(ReleaseGateCode.CANDIDATE_REF_MISMATCH.value)

    supersedes = collect_superseded_ids(evidence)
    seen_ids: set[str] = set()
    accepted_kinds: set[str] = set()
    accepted_ids: list[str] = []
    rejected: list[tuple[str, str]] = []
    limitation_acceptances: set[str] = set()

    for item in evidence:
        # G2 §G2.3: only verifier-produced VerifiedEvidenceRecord can satisfy
        # a gate. Raw/validated dict records are refused with NOT_VERIFIED.
        if not isinstance(item, VerifiedEvidenceRecord):
            rid = "<malformed>"
            if isinstance(item, dict):
                rid = str(item.get("evidence_id") or "<missing-id>")
            rejected.append((rid, EvidenceRejectionCode.NOT_VERIFIED.value))
            continue
        record = item.record
        ok, code = validate_evidence_record(
            record, candidate_version=candidate.version,
            source_commit=candidate.source_commit,
            policy_digest=candidate.policy_digest,
            supersedes=supersedes, seen_ids=seen_ids,
            approved_mechanisms=policy.approved_verification_mechanisms)
        rid = str(record.get("evidence_id") or "<missing-id>") if isinstance(record, dict) else "<malformed>"
        if not ok:
            rejected.append((rid, code or EvidenceRejectionCode.MALFORMED.value))
            continue
        # G3 §G3.2 (audit finding 8): evidence must be bound to THIS exact
        # inventory — a record verified against different artifacts is refused,
        # and every subject digest must exist in the candidate inventory.
        if record.get("artifact_inventory_digest") != artifacts.inventory_digest():
            rejected.append((
                rid, EvidenceRejectionCode.INVENTORY_DIGEST_MISMATCH.value))
            continue
        inventory_shas = {a.sha256 for a in artifacts.artifacts}
        subject_shas: set[str] = set()
        for subject in record.get("artifact_subjects") or []:
            sha = str(subject.get("sha256") or "") if isinstance(subject, dict) else ""
            subject_shas.add(sha[7:] if sha.startswith("sha256:") else sha)
        if subject_shas and not subject_shas <= inventory_shas:
            rejected.append((
                rid, EvidenceRejectionCode.ARTIFACT_SUBJECT_MISMATCH.value))
            continue
        seen_ids.add(rid)
        kind = record["kind"]
        accepted_kinds.add(kind)
        accepted_ids.append(rid)
        if kind == EvidenceKind.KNOWN_LIMITATION_ACCEPTANCE.value:
            # The limitation this acceptance covers is named in measurements.
            covered = (record.get("measurements") or {}).get("capability")
            if covered:
                limitation_acceptances.add(str(covered))

    # Required evidence kinds for 1.0.
    for kind in policy.one_zero_required_kinds:
        if kind not in accepted_kinds:
            gate = policy.gate_code_for(kind)
            if gate:
                blocking.add(gate)

    # Mandatory capabilities that remain unavailable block the release.
    # G1.3: derived from the reviewed product state, never a caller default.
    unavailable = {c["capability"]
                   for c in release_state.KNOWN_UNAVAILABLE_CAPABILITIES}
    mandatory = policy.mandatory_capabilities()
    if mandatory & unavailable:
        blocking.add(ReleaseGateCode.CAPABILITY_UNAVAILABLE.value)

    # Limitations come from the reviewed policy and need acceptance records.
    effective_limitations = policy.limitation_capabilities()
    accepted_limitations: list[str] = []
    for limitation in sorted(effective_limitations):
        if limitation in limitation_acceptances:
            accepted_limitations.append(limitation)
        else:
            blocking.add(ReleaseGateCode.LIMITATION_ACCEPTANCE_MISSING.value)

    # RC eligibility is the strict subset.
    rc_blocking = {
        policy.gate_code_for(kind)
        for kind in policy.rc_required_kinds if kind not in accepted_kinds
    }
    rc_blocking.discard(None)
    eligible_for_rc = not rc_blocking and not blocking

    eligible_for_1_0_0 = not blocking

    codes = tuple(sorted(blocking))
    explanations = tuple(_EXPLANATIONS.get(c, c) for c in codes)

    return ReleaseDecision(
        eligible_for_rc=eligible_for_rc,
        eligible_for_1_0_0=eligible_for_1_0_0,
        blocking_codes=codes,
        blocking_explanations=explanations,
        accepted_limitations=tuple(accepted_limitations),
        candidate_version=candidate.version,
        source_commit=candidate.source_commit,
        source_ref=candidate.source_ref,
        repository=candidate.repository,
        inventory_digest=artifacts.inventory_digest(),
        evidence_used=tuple(sorted(accepted_ids)),
        evidence_rejected=tuple(sorted(rejected)),
        policy_digest=policy.policy_digest(),
        _evaluator_key=_EVALUATOR_KEY,
    )


@dataclass(frozen=True)
class CheckoutCheckResult:
    """Strict release-checkout verdict (REL-014). Read-only: never deletes."""

    ok: bool
    untracked: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "untracked": list(self.untracked)}


def check_release_checkout(
    workspace: str | Path, *, tracked_files: list[str]
) -> CheckoutCheckResult:
    """Reject untracked inputs in the build workspace WITHOUT deleting them.

    ``tracked_files`` are the workspace-relative POSIX paths that belong to the
    reviewed checkout. Any other regular file present is reported as untracked
    and fails the check (release builds must come from a clean checkout).
    """
    root = Path(workspace)
    tracked = {Path(p).as_posix() for p in tracked_files}
    untracked: list[str] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel not in tracked:
                untracked.append(rel)
    return CheckoutCheckResult(ok=not untracked, untracked=tuple(untracked))
