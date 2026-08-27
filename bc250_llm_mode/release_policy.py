"""C1 §4.2/§4.4 (V1_0_RELEASE_CLOSURE plan): release policy contract (pure).

The versioned release policy names the closed evidence-kind vocabulary, the
evidence required for a release candidate and for ``1.0.0``, the gate-code
vocabulary, and the capability classification (mandatory vs accepted
limitation). Policy lives in reviewed source and is included in the release
manifest by digest. Pure and deterministic — no I/O.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# C7 (§C7.1): the reviewed policy content revision. The class schema is still
# generation 1 (``ReleasePolicyV1``); this field tracks reviewed CONTENT
# revisions within that schema. Revision 2 is the C7 capability-scope decision.
RELEASE_POLICY_VERSION = 2


class EvidenceKind(str, Enum):
    """Closed, versioned evidence vocabulary (§4.2). Unknown kinds never count."""

    SOURCE_CHECKOUT = "SOURCE_CHECKOUT"
    DEFAULT_TEST_SUITE = "DEFAULT_TEST_SUITE"
    SLOW_SECURITY_STRESS = "SLOW_SECURITY_STRESS"
    CLEAN_WHEEL_SMOKE = "CLEAN_WHEEL_SMOKE"
    UPGRADE_MATRIX = "UPGRADE_MATRIX"
    BACKUP_RESTORE_HARDWARE = "BACKUP_RESTORE_HARDWARE"
    SBOM = "SBOM"
    BUILD_PROVENANCE = "BUILD_PROVENANCE"
    ARTIFACT_ATTESTATION = "ARTIFACT_ATTESTATION"
    PACKAGE_PUBLISH_ATTESTATION = "PACKAGE_PUBLISH_ATTESTATION"
    CONTAINER_IDENTITY = "CONTAINER_IDENTITY"
    HARDWARE_QUALIFICATION = "HARDWARE_QUALIFICATION"
    SOAK_TEST = "SOAK_TEST"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    HUMAN_ACCEPTANCE = "HUMAN_ACCEPTANCE"
    KNOWN_LIMITATION_ACCEPTANCE = "KNOWN_LIMITATION_ACCEPTANCE"
    DOCUMENTATION_RECONCILIATION = "DOCUMENTATION_RECONCILIATION"
    RELEASE_APPROVAL = "RELEASE_APPROVAL"


EVIDENCE_KINDS: tuple[str, ...] = tuple(k.value for k in EvidenceKind)


class ReleaseGateCode(str, Enum):
    """Closed blocking-reason vocabulary (§C1.2)."""

    MILESTONE_EVIDENCE_MISSING = "MILESTONE_EVIDENCE_MISSING"
    TEST_EVIDENCE_MISSING = "TEST_EVIDENCE_MISSING"
    CLEAN_WHEEL_EVIDENCE_MISSING = "CLEAN_WHEEL_EVIDENCE_MISSING"
    SBOM_MISSING_OR_MISMATCHED = "SBOM_MISSING_OR_MISMATCHED"
    PROVENANCE_MISSING_OR_MISMATCHED = "PROVENANCE_MISSING_OR_MISMATCHED"
    SIGNATURE_MISSING_OR_INVALID = "SIGNATURE_MISSING_OR_INVALID"
    BACKUP_RESTORE_EVIDENCE_MISSING = "BACKUP_RESTORE_EVIDENCE_MISSING"
    HARDWARE_QUALIFICATION_MISSING = "HARDWARE_QUALIFICATION_MISSING"
    SOAK_EVIDENCE_MISSING = "SOAK_EVIDENCE_MISSING"
    SECURITY_REVIEW_MISSING = "SECURITY_REVIEW_MISSING"
    HUMAN_ACCEPTANCE_MISSING = "HUMAN_ACCEPTANCE_MISSING"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    LIMITATION_ACCEPTANCE_MISSING = "LIMITATION_ACCEPTANCE_MISSING"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    SOURCE_COMMIT_MISMATCH = "SOURCE_COMMIT_MISMATCH"
    ARTIFACT_DIGEST_MISMATCH = "ARTIFACT_DIGEST_MISMATCH"
    DOCUMENTATION_DRIFT = "DOCUMENTATION_DRIFT"
    REPOSITORY_NOT_PUBLISHED = "REPOSITORY_NOT_PUBLISHED"
    UNKNOWN_EVIDENCE = "UNKNOWN_EVIDENCE"


GATE_CODES: tuple[str, ...] = tuple(c.value for c in ReleaseGateCode)


class CapabilityClass(str, Enum):
    """C7 §C7.1 (V1_0_RELEASE_CLOSURE plan): the 1.0 capability-scope vocabulary.

    MANDATORY_FOR_1_0       release blocks until implemented and evidenced;
    SUPPORTED_OPTIONAL      available and supported when enabled;
    EXPERIMENTAL            intentionally outside the stable 1.0 support
                            contract, visibly labeled;
    DEFERRED_NOT_ADVERTISED not present in product claims or primary UI;
    REMOVED                 command/UI/API deleted with migration guidance.

    C1 originally shipped a 3-value draft (MANDATORY/OPTIONAL/DEFERRED) and
    marked it REL-012/C7; this is the reviewed refinement. Every known
    limitation must carry one of these classes — an UNCLASSIFIED limitation can
    never qualify a 1.0 tag (C7 exit gate).
    """

    MANDATORY_FOR_1_0 = "MANDATORY_FOR_1_0"
    SUPPORTED_OPTIONAL = "SUPPORTED_OPTIONAL"
    EXPERIMENTAL = "EXPERIMENTAL"
    DEFERRED_NOT_ADVERTISED = "DEFERRED_NOT_ADVERTISED"
    REMOVED = "REMOVED"


# Capability classes that are "accepted limitations": present in the policy but
# not mandatory, each requiring a reviewed KNOWN_LIMITATION_ACCEPTANCE record.
# REMOVED is excluded — a removed capability is gone (with migration guidance)
# and needs no limitation acceptance.
_LIMITATION_CLASSES: tuple[str, ...] = (
    CapabilityClass.SUPPORTED_OPTIONAL.value,
    CapabilityClass.EXPERIMENTAL.value,
    CapabilityClass.DEFERRED_NOT_ADVERTISED.value,
)


# Map each required evidence kind to the gate code raised when it is absent.
_KIND_TO_GATE_CODE: dict[str, str] = {
    EvidenceKind.SOURCE_CHECKOUT.value: ReleaseGateCode.REPOSITORY_NOT_PUBLISHED.value,
    EvidenceKind.DEFAULT_TEST_SUITE.value: ReleaseGateCode.TEST_EVIDENCE_MISSING.value,
    EvidenceKind.SLOW_SECURITY_STRESS.value: ReleaseGateCode.TEST_EVIDENCE_MISSING.value,
    EvidenceKind.CLEAN_WHEEL_SMOKE.value: ReleaseGateCode.CLEAN_WHEEL_EVIDENCE_MISSING.value,
    EvidenceKind.UPGRADE_MATRIX.value: ReleaseGateCode.TEST_EVIDENCE_MISSING.value,
    EvidenceKind.BACKUP_RESTORE_HARDWARE.value: ReleaseGateCode.BACKUP_RESTORE_EVIDENCE_MISSING.value,
    EvidenceKind.SBOM.value: ReleaseGateCode.SBOM_MISSING_OR_MISMATCHED.value,
    EvidenceKind.BUILD_PROVENANCE.value: ReleaseGateCode.PROVENANCE_MISSING_OR_MISMATCHED.value,
    EvidenceKind.ARTIFACT_ATTESTATION.value: ReleaseGateCode.SIGNATURE_MISSING_OR_INVALID.value,
    EvidenceKind.PACKAGE_PUBLISH_ATTESTATION.value: ReleaseGateCode.SIGNATURE_MISSING_OR_INVALID.value,
    EvidenceKind.CONTAINER_IDENTITY.value: ReleaseGateCode.SIGNATURE_MISSING_OR_INVALID.value,
    EvidenceKind.HARDWARE_QUALIFICATION.value: ReleaseGateCode.HARDWARE_QUALIFICATION_MISSING.value,
    EvidenceKind.SOAK_TEST.value: ReleaseGateCode.SOAK_EVIDENCE_MISSING.value,
    EvidenceKind.SECURITY_REVIEW.value: ReleaseGateCode.SECURITY_REVIEW_MISSING.value,
    EvidenceKind.HUMAN_ACCEPTANCE.value: ReleaseGateCode.HUMAN_ACCEPTANCE_MISSING.value,
    EvidenceKind.DOCUMENTATION_RECONCILIATION.value: ReleaseGateCode.DOCUMENTATION_DRIFT.value,
    EvidenceKind.RELEASE_APPROVAL.value: ReleaseGateCode.MILESTONE_EVIDENCE_MISSING.value,
}

# Evidence kinds required to cut a release candidate (a strict subset).
RC_REQUIRED_KINDS: frozenset[str] = frozenset({
    EvidenceKind.DEFAULT_TEST_SUITE.value,
    EvidenceKind.SLOW_SECURITY_STRESS.value,
    EvidenceKind.CLEAN_WHEEL_SMOKE.value,
    EvidenceKind.UPGRADE_MATRIX.value,
    EvidenceKind.DOCUMENTATION_RECONCILIATION.value,
})

# Evidence kinds required for 1.0.0 (everything in the map).
ONE_ZERO_REQUIRED_KINDS: frozenset[str] = frozenset(_KIND_TO_GATE_CODE.keys())


@dataclass(frozen=True)
class CapabilityPolicy:
    """One capability's 1.0 classification (REL-012 / C7)."""

    capability: str
    classification: str  # CapabilityClass value
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"capability": self.capability,
                "classification": self.classification, "reason": self.reason}


@dataclass(frozen=True)
class ReleasePolicyV1:
    """Versioned release policy (§4.4)."""

    policy_version: int = RELEASE_POLICY_VERSION
    rc_required_kinds: frozenset[str] = RC_REQUIRED_KINDS
    one_zero_required_kinds: frozenset[str] = ONE_ZERO_REQUIRED_KINDS
    capabilities: tuple[CapabilityPolicy, ...] = field(default_factory=tuple)
    max_evidence_age_days: int = 90
    min_soak_hours: int = 24

    def mandatory_capabilities(self) -> frozenset[str]:
        return frozenset(
            c.capability for c in self.capabilities
            if c.classification == CapabilityClass.MANDATORY_FOR_1_0.value)

    def limitation_capabilities(self) -> frozenset[str]:
        return frozenset(
            c.capability for c in self.capabilities
            if c.classification in _LIMITATION_CLASSES)

    def classified_capabilities(self) -> frozenset[str]:
        """Every capability this policy assigns a scope class to (C7)."""
        return frozenset(c.capability for c in self.capabilities)

    def gate_code_for(self, kind: str) -> str | None:
        return _KIND_TO_GATE_CODE.get(kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_policy_version": self.policy_version,
            "rc_required_kinds": sorted(self.rc_required_kinds),
            "one_zero_required_kinds": sorted(self.one_zero_required_kinds),
            "capabilities": [c.to_dict() for c in self.capabilities],
            "max_evidence_age_days": self.max_evidence_age_days,
            "min_soak_hours": self.min_soak_hours,
        }

    def policy_digest(self) -> str:
        body = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def default_release_policy() -> ReleasePolicyV1:
    """The reviewed default policy (C7 §C7.1/§C7.2 scope decision).

    ``backup-restore-publish`` is MANDATORY_FOR_1_0 (REL-004): implemented as a
    durable operation by C2, so it is no longer an "unavailable capability" —
    its remaining 1.0 requirement is physical-hardware qualification evidence,
    tracked by the evidence gate (BACKUP_RESTORE_HARDWARE / HARDWARE_
    QUALIFICATION / SOAK_TEST), not by capability unavailability.

    ``model-conversion`` is DEFERRED_NOT_ADVERTISED (REL-008): no pinned,
    verified converter ships in 1.0, so conversion is outside the 1.0 support
    promise and not advertised in product claims or the primary UI. It requires
    a reviewed accepted-limitation record. Direct GGUF acquisition + local GGUF
    import remain the supported model-ingestion paths.
    """
    return ReleasePolicyV1(
        capabilities=(
            CapabilityPolicy(
                capability="backup-restore-publish",
                classification=CapabilityClass.MANDATORY_FOR_1_0.value,
                reason="durable backup create/restore publication is required "
                       "for 1.0 (REL-004); implemented by C2, so hardware "
                       "qualification evidence is tracked by the evidence gate"),
            CapabilityPolicy(
                capability="model-conversion",
                classification=CapabilityClass.DEFERRED_NOT_ADVERTISED.value,
                reason="no pinned, verified converter ships in this build "
                       "(REL-008); C7 scope decision DEFERRED_NOT_ADVERTISED, "
                       "requires an accepted-limitation record"),
        ),
    )
