"""Release state facade (pure, no I/O) — INFORMATIONAL ONLY since G1.

Originally P9 §15.1/§15.2 (boolean readiness). Migrated by C1 (§C1.2 of the
V1_0_RELEASE_CLOSURE plan) to an evidence-driven model, and by G1 (§G1.1 of
the RELEASE_GATE_AND_PIPELINE_REMEDIATION plan) to a pure read facade: the
``evidence_satisfied`` field is DELETED and ``may_tag_1_0_0()`` is
NON-AUTHORITATIVE (always False). Tag eligibility has exactly ONE authority:
the ``ReleaseDecision`` returned by
``bc250_llm_mode.release_gate.evaluate_release`` over a mandatory
``CandidateIdentity`` + ``ArtifactInventory`` + verified evidence. This module
keeps the known-unavailable capabilities VISIBLE rather than implying
completeness.

C7 (§C7.1–§C7.3): capability scope reconciliation. ``backup-restore-publish``
was IMPLEMENTED by C2 as a durable operation, so it is no longer an "unavailable
capability" — its remaining 1.0 requirement is physical-hardware qualification
evidence, tracked by the evidence gate (and by ``blocking_gaps``), not by
capability unavailability. ``model-conversion`` remains the single genuinely
unavailable capability, classified DEFERRED_NOT_ADVERTISED. The
unclassified-limitation surface is preserved: every known limitation must
carry a scope classification in the release policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .release_policy import default_release_policy

# C1.2: manifest schema v2 (evidence-driven). v1 was the boolean model.
RELEASE_MANIFEST_SCHEMA_VERSION = 2

# Capabilities that are documented but NOT available in this build. They must
# surface in the release state (P9 §15.1 rule 5/6) — never implied complete.
#
# C7 reconciliation: ``backup-restore-publish`` was REMOVED from this list when
# C2 implemented it as a durable operation (its remaining 1.0 requirement is
# hardware qualification evidence, tracked by the evidence gate, not by
# capability unavailability). ``model-conversion`` remains the single genuinely
# unavailable capability (C7 scope decision: DEFERRED_NOT_ADVERTISED — outside
# the 1.0 support promise and not advertised in product claims/primary UI).
KNOWN_UNAVAILABLE_CAPABILITIES: tuple[dict[str, str], ...] = (
    {
        "capability": "model-conversion",
        "reason": "no pinned, verified converter ships in this build; C7 scope "
                  "decision DEFERRED_NOT_ADVERTISED — only GGUF acquisition and "
                  "local GGUF import are supported model-ingestion paths in 1.0",
        "visible_in": "release notes + accepted-limitation record (not "
                      "advertised in product claims or primary UI)",
    },
)


@dataclass(frozen=True)
class ReleaseIdentity:
    """One executable/runtime/model integration identity in the manifest."""

    kind: str
    identity: str
    digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "identity": self.identity,
                "digest": self.digest}


@dataclass(frozen=True)
class ReleaseState:
    """Release-readiness state (INFORMATIONAL read facade since G1).

    The four ``*_green`` booleans are informational only. G1 DELETED the
    ``evidence_satisfied`` field (constructing one raises ``TypeError``) and
    made ``may_tag_1_0_0()`` a non-authoritative constant ``False``: no public
    ``ReleaseState`` construction can ever assert tag eligibility. The sole
    eligibility authority is ``release_gate.evaluate_release``.
    """

    version: str
    channel: str = "dev"
    schema_version: int = 12
    identities: tuple[ReleaseIdentity, ...] = field(default_factory=tuple)
    milestone_gates_green: bool = False
    hardware_qualification_green: bool = False
    human_acceptance_green: bool = False
    security_review_signed_off: bool = False

    def blocking_gaps(self) -> list[str]:
        """Informational gaps from the deprecated booleans (honest, visible)."""
        gaps: list[str] = []
        if not self.milestone_gates_green:
            gaps.append("milestone exit gates not all green/evidence-linked")
        if not self.hardware_qualification_green:
            gaps.append("hardware qualification + soak pending")
        if not self.human_acceptance_green:
            gaps.append("human acceptance pending")
        if not self.security_review_signed_off:
            gaps.append("security review sign-off pending")
        return gaps

    def unavailable_mandatory_capabilities(self) -> list[str]:
        """Mandatory capabilities still listed unavailable (always block)."""
        unavailable = {c["capability"] for c in KNOWN_UNAVAILABLE_CAPABILITIES}
        mandatory = default_release_policy().mandatory_capabilities()
        return sorted(unavailable & mandatory)

    def unclassified_limitations(self) -> list[str]:
        """Known-unavailable capabilities the release policy does NOT classify.

        C7 exit gate: an unclassified limitation can never qualify a 1.0 tag —
        every known limitation must carry a reviewed scope classification
        (MANDATORY_FOR_1_0 / SUPPORTED_OPTIONAL / EXPERIMENTAL /
        DEFERRED_NOT_ADVERTISED / REMOVED).
        """
        classified = default_release_policy().classified_capabilities()
        return sorted(c["capability"] for c in KNOWN_UNAVAILABLE_CAPABILITIES
                      if c["capability"] not in classified)

    def may_tag_1_0_0(self) -> bool:
        """G1 (§G1.1): NON-AUTHORITATIVE — always ``False``.

        Tag eligibility has exactly one authority: the ``ReleaseDecision``
        returned by ``bc250_llm_mode.release_gate.evaluate_release`` over a
        mandatory ``CandidateIdentity`` + ``ArtifactInventory`` + verified
        evidence. No public ``ReleaseState`` construction can ever assert
        eligibility; this method survives only as an explicit, always-false
        compatibility surface pointing callers at the real authority.
        """
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
            "version": self.version,
            "channel": self.channel,
            "database_schema_version": self.schema_version,
            "identities": [i.to_dict() for i in self.identities],
            "unavailable_capabilities": list(
                KNOWN_UNAVAILABLE_CAPABILITIES),
            "unavailable_mandatory_capabilities":
                self.unavailable_mandatory_capabilities(),
            "unclassified_limitations": self.unclassified_limitations(),
            "blocking_gaps": self.blocking_gaps(),
            "may_tag_1_0_0": self.may_tag_1_0_0(),
        }


def build_release_manifest(state: ReleaseState) -> dict[str, Any]:
    """Assemble the release manifest document (P9 §15.2)."""
    doc = state.to_dict()
    doc["manifest_schema_version"] = RELEASE_MANIFEST_SCHEMA_VERSION
    return doc
