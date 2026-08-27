"""Release state and manifest facade (pure, no I/O).

Originally P9 §15.1/§15.2 (boolean readiness). Migrated by C1 (§C1.2 of the
V1_0_RELEASE_CLOSURE plan): the caller-supplied approval booleans are NO LONGER
the release authority. ``may_tag_1_0_0()`` can only become true through
validated, candidate-bound evidence (``evidence_satisfied``), and a mandatory
capability that is still unavailable always blocks the tag. The authoritative
fail-closed evaluator is ``bc250_llm_mode.release_gate.evaluate_release``; this
module remains the public manifest facade and keeps the known-unavailable
capabilities VISIBLE rather than implying completeness.

C7 (§C7.1–§C7.3): capability scope reconciliation. ``backup-restore-publish``
was IMPLEMENTED by C2 as a durable operation, so it is no longer an "unavailable
capability" — its remaining 1.0 requirement is physical-hardware qualification
evidence, tracked by the evidence gate (and by ``blocking_gaps``), not by
capability unavailability. ``model-conversion`` remains the single genuinely
unavailable capability, classified DEFERRED_NOT_ADVERTISED. C7 also adds the
unclassified-limitation gate: ``may_tag_1_0_0()`` can never be true while a
known limitation lacks a scope classification in the release policy.
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
    """Release-readiness state (manifest facade).

    The four ``*_green`` booleans are DEPRECATED as release authority (C1.2):
    they are informational only and can never, on their own, make
    ``may_tag_1_0_0()`` true. Eligibility requires validated evidence
    (``evidence_satisfied``), which the public constructor leaves False — only
    the evidence-driven evaluator path may set it.
    """

    version: str
    channel: str = "dev"
    schema_version: int = 10
    identities: tuple[ReleaseIdentity, ...] = field(default_factory=tuple)
    milestone_gates_green: bool = False
    hardware_qualification_green: bool = False
    human_acceptance_green: bool = False
    security_review_signed_off: bool = False
    # C1.2: evidence authority. Defaults False; booleans cannot set it.
    evidence_satisfied: bool = False

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
        """C1.2 + C7: never 1.0.0 from booleans alone. Requires (a) no
        informational gaps, (b) no unavailable mandatory capability, (c) no
        unclassified limitation, and (d) validated evidence. The public
        constructor cannot satisfy (d)."""
        if self.blocking_gaps():
            return False
        if self.unavailable_mandatory_capabilities():
            return False
        if self.unclassified_limitations():
            return False
        return self.evidence_satisfied

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
            "evidence_satisfied": self.evidence_satisfied,
            "may_tag_1_0_0": self.may_tag_1_0_0(),
        }


def build_release_manifest(state: ReleaseState) -> dict[str, Any]:
    """Assemble the release manifest document (P9 §15.2)."""
    doc = state.to_dict()
    doc["manifest_schema_version"] = RELEASE_MANIFEST_SCHEMA_VERSION
    return doc
