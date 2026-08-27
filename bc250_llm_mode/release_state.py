"""P9 §15.1/§15.2: release state and manifest contract (pure, no I/O).

Models the release-readiness state for 1.0 qualification. Per §15.1 the build
must NOT be called ``1.0.0`` while a documented feature is silently routed to an
unsafe legacy path, and any unsupported capability (model conversion, the
hardware-gated backup-restore publish step) must be VISIBLE in the release
state rather than implying completeness. This module is pure: it assembles a
release manifest from declared identities and reports honest availability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RELEASE_MANIFEST_SCHEMA_VERSION = 1

# Capabilities that are documented but NOT available in this build. They must
# surface in the release state (P9 §15.1 rule 5/6) — never implied complete.
KNOWN_UNAVAILABLE_CAPABILITIES: tuple[dict[str, str], ...] = (
    {
        "capability": "model-conversion",
        "reason": "no pinned, verified converter ships in this build",
        "visible_in": "convert-model CLI + release notes",
    },
    {
        "capability": "backup-restore-publish",
        "reason": "atomic profile-level publish + post-restore inference "
                  "verification require physical BC250 hardware",
        "visible_in": "release notes (hardware-gated pending evidence)",
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
    """Release-readiness state (P9 §15.1)."""

    version: str
    channel: str = "dev"
    schema_version: int = 9
    identities: tuple[ReleaseIdentity, ...] = field(default_factory=tuple)
    milestone_gates_green: bool = False
    hardware_qualification_green: bool = False
    human_acceptance_green: bool = False
    security_review_signed_off: bool = False

    def blocking_gaps(self) -> list[str]:
        """Reasons this state cannot be tagged 1.0.0 yet (honest, visible)."""
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

    def may_tag_1_0_0(self) -> bool:
        """§15.1 rule 5: never 1.0.0 while gaps remain or a documented feature
        is silently unsafe. Unavailable capabilities stay visible regardless."""
        return not self.blocking_gaps()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
            "version": self.version,
            "channel": self.channel,
            "database_schema_version": self.schema_version,
            "identities": [i.to_dict() for i in self.identities],
            "unavailable_capabilities": list(
                KNOWN_UNAVAILABLE_CAPABILITIES),
            "blocking_gaps": self.blocking_gaps(),
            "may_tag_1_0_0": self.may_tag_1_0_0(),
        }


def build_release_manifest(state: ReleaseState) -> dict[str, Any]:
    """Assemble the release manifest document (P9 §15.2)."""
    doc = state.to_dict()
    doc["manifest_schema_version"] = RELEASE_MANIFEST_SCHEMA_VERSION
    return doc
