"""C1 §4.5 (V1_0_RELEASE_CLOSURE plan): fail-closed release evaluator (pure).

``evaluate_release`` derives the release decision ONLY from validated,
candidate-bound evidence records — never from caller-supplied approval
booleans. It is deterministic (independent of evidence input order) and
fail-closed: any missing required evidence, unavailable mandatory capability,
or unaccepted limitation yields stable blocking codes. ``check_release_checkout``
is a read-only strict-checkout gate that rejects untracked build inputs without
deleting the developer's files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .release_evidence import (
    EvidenceRejectionCode,
    collect_superseded_ids,
    validate_evidence_record,
)
from .release_policy import (
    EvidenceKind,
    ReleaseGateCode,
    ReleasePolicyV1,
    default_release_policy,
)

RELEASE_DECISION_SCHEMA_VERSION = 2

# Module-private sentinel: only evaluate_release may produce an eligible
# decision (§4.5 — no caller instantiates "eligible" directly).
_EVALUATOR_KEY = object()


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
}


def evaluate_release(
    *,
    evidence: list[dict[str, Any]],
    candidate_version: str,
    source_commit: str | None = None,
    policy: ReleasePolicyV1 | None = None,
    declared_limitations: list[str] | None = None,
    unavailable_capabilities: frozenset[str] = frozenset(),
) -> ReleaseDecision:
    """Pure, deterministic, fail-closed release evaluation."""
    policy = policy or default_release_policy()

    supersedes = collect_superseded_ids(evidence)
    seen_ids: set[str] = set()
    accepted_kinds: set[str] = set()
    accepted_ids: list[str] = []
    rejected: list[tuple[str, str]] = []
    limitation_acceptances: set[str] = set()

    for record in evidence:
        ok, code = validate_evidence_record(
            record, candidate_version=candidate_version,
            source_commit=source_commit, supersedes=supersedes,
            seen_ids=seen_ids)
        rid = str(record.get("evidence_id") or "<missing-id>") if isinstance(record, dict) else "<malformed>"
        if not ok:
            rejected.append((rid, code or EvidenceRejectionCode.MALFORMED.value))
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

    blocking: set[str] = set()

    # Required evidence kinds for 1.0.
    for kind in policy.one_zero_required_kinds:
        if kind not in accepted_kinds:
            gate = policy.gate_code_for(kind)
            if gate:
                blocking.add(gate)

    # Mandatory capabilities that remain unavailable block the release.
    mandatory = policy.mandatory_capabilities()
    if mandatory & unavailable_capabilities:
        blocking.add(ReleaseGateCode.CAPABILITY_UNAVAILABLE.value)

    # Limitations (declared + policy-classified) need an acceptance record.
    effective_limitations = set(declared_limitations or []) | policy.limitation_capabilities()
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
        candidate_version=candidate_version,
        source_commit=source_commit,
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
