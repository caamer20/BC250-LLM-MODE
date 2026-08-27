"""P5 §11.2: typed health dimensions and deterministic composition.

The health vocabulary is CLOSED. Every dimension reports:

- ``state``    — one of the eight typed states below;
- ``basis``    — how the state was established: ``verified`` (a bounded
                 probe proved it), ``observed`` (a live reading shows it),
                 or ``inferred`` (derived from durable configuration only);
- ``evidence`` — a bounded human-readable evidence string;
- ``as_of``    — ISO timestamp of the underlying reading (None if never);
- ``stale``    — True when the reading is older than its bound.

Fail-closed staleness rule
    Positive service claims EXPIRE: a stale READY/DEGRADED/BUSY dimension
    is demoted to UNVERIFIED (a stale reading must never render as a
    current green status). Negative safety conditions PERSIST: a stale
    BLOCKED/RECOVERY_REQUIRED/REPAIR_REQUIRED/UNAVAILABLE dimension keeps
    its state until an operator clears it.

Green-claim rule
    READY requires basis ``verified`` or ``observed`` plus non-empty
    evidence and a timestamp. An inferred READY is a construction error —
    desired configuration is never proof of a live capability.
"""

from __future__ import annotations

from dataclasses import dataclass

READY = "READY"
DEGRADED = "DEGRADED"
BUSY = "BUSY"
BLOCKED = "BLOCKED"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
REPAIR_REQUIRED = "REPAIR_REQUIRED"
UNVERIFIED = "UNVERIFIED"
UNAVAILABLE = "UNAVAILABLE"

HEALTH_STATES = (
    READY,
    DEGRADED,
    BUSY,
    BLOCKED,
    RECOVERY_REQUIRED,
    REPAIR_REQUIRED,
    UNVERIFIED,
    UNAVAILABLE,
)

# Deterministic severity ordering, most severe first. The overall state is
# the most severe effective dimension, so any single safety condition
# dominates every service-quality claim:
#
#   REPAIR_REQUIRED    durable state is corrupt; operator repair required
#   RECOVERY_REQUIRED  durable work is stuck; operator recovery required
#   BLOCKED            a fail-closed precondition prevents service
#   UNAVAILABLE        the capability is observably absent
#   UNVERIFIED         no bounded current evidence; no quality claim
#   DEGRADED           serving below intent (known)
#   BUSY               healthy but occupied
#   READY              verified/observed good
SEVERITY_ORDER = (
    REPAIR_REQUIRED,
    RECOVERY_REQUIRED,
    BLOCKED,
    UNAVAILABLE,
    UNVERIFIED,
    DEGRADED,
    BUSY,
    READY,
)

_SEVERITY_RANK = {state: rank for rank, state in enumerate(reversed(SEVERITY_ORDER))}

BASIS_VERIFIED = "verified"
BASIS_OBSERVED = "observed"
BASIS_INFERRED = "inferred"
BASIS_RANK = {BASIS_VERIFIED: 2, BASIS_OBSERVED: 1, BASIS_INFERRED: 0}

# Positive service claims expire when stale; safety conditions persist.
_EXPIRES_WHEN_STALE = frozenset({READY, DEGRADED, BUSY})


def severity_rank(state: str) -> int:
    """Higher is more severe. Raises for unknown states (closed vocabulary)."""
    try:
        return _SEVERITY_RANK[state]
    except KeyError:
        raise ValueError(f"unknown health state: {state!r}") from None


def more_severe(a: str, b: str) -> str:
    """Deterministic max over the severity ordering."""
    return a if severity_rank(a) >= severity_rank(b) else b


@dataclass(frozen=True)
class HealthDimension:
    """One typed health reading with its provenance."""

    name: str
    state: str
    basis: str
    evidence: str
    as_of: str | None = None
    stale: bool = False
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in HEALTH_STATES:
            raise ValueError(f"unknown health state: {self.state!r}")
        if self.basis not in BASIS_RANK:
            raise ValueError(f"unknown health basis: {self.basis!r}")
        if self.state == READY:
            # §11.2/P5 gate: every green readiness claim needs a bounded
            # evidence source. Desired configuration alone never qualifies.
            if self.basis == BASIS_INFERRED:
                raise ValueError(
                    f"dimension {self.name!r}: READY cannot be inferred; "
                    "it requires a verified or observed reading"
                )
            if not self.evidence or not self.as_of:
                raise ValueError(
                    f"dimension {self.name!r}: READY requires evidence and a timestamp"
                )

    @property
    def effective_state(self) -> str:
        """The state after fail-closed staleness demotion."""
        if self.stale and self.state in _EXPIRES_WHEN_STALE:
            return UNVERIFIED
        return self.state

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "effective_state": self.effective_state,
            "basis": self.basis,
            "evidence": self.evidence,
            "as_of": self.as_of,
            "stale": self.stale,
            "stale_reason": self.stale_reason,
        }


def dimension(
    name: str,
    state: str,
    *,
    basis: str,
    evidence: str = "",
    as_of: str | None = None,
    stale: bool = False,
    stale_reason: str | None = None,
) -> HealthDimension:
    """Construct a validated dimension (see HealthDimension for rules)."""
    return HealthDimension(
        name=name,
        state=state,
        basis=basis,
        evidence=evidence,
        as_of=as_of,
        stale=stale,
        stale_reason=stale_reason,
    )


def compose_overall(dimensions: list[HealthDimension]) -> HealthDimension:
    """Compose the overall appliance state: the most severe effective
    dimension wins; ties keep the first (stable, deterministic) name.

    The composite carries the WINNING dimension's provenance (basis,
    timestamp, staleness), so the overall claim is exactly as strong as the
    evidence that established it.
    """
    if not dimensions:
        raise ValueError("compose_overall requires at least one dimension")
    worst = dimensions[0]
    for candidate in dimensions[1:]:
        if severity_rank(candidate.effective_state) > severity_rank(
            worst.effective_state
        ):
            worst = candidate
    return HealthDimension(
        name="overall",
        state=worst.effective_state,
        basis=worst.basis,
        evidence=f"most severe dimension: {worst.name} ({worst.effective_state})",
        as_of=worst.as_of,
        stale=worst.stale,
        stale_reason=worst.stale_reason,
    )
