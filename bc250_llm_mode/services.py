"""Domain services (Phase A of ROAD_TO_1_0_IMPLEMENTATION_PLAN.md).

Each service owns a narrow slice of durable state, performs its repository
writes inside one explicit commit, and enforces its own invariants so no
whole-state writer can violate them.
"""

from __future__ import annotations

from typing import Any

from .repositories import ThermalStateRepository

NOMINAL = "nominal"
THROTTLED = "throttled"
STOPPED = "stopped"


class ThermalLatchProtected(RuntimeError):
    """A latched thermal stop may not be cleared or downgraded implicitly."""


class ThermalStateService:
    """Safety-authoritative persistence for the thermal latch and baseline.

    The thermal_state row is writable ONLY through this service. The
    compatibility facade never persists thermal keys from whole-state
    dictionaries, so a stale GUI/CLI draft cannot clear or downgrade a
    latched stop. Escalation to STOPPED is always allowed; every other
    transition out of STOPPED requires :meth:`reset_to_nominal`.
    """

    def __init__(self, conn) -> None:
        self._repo = ThermalStateRepository(conn)
        self._conn = conn

    def current(self) -> dict[str, Any]:
        return self._repo.get()

    def _apply(
        self,
        *,
        latch_state: str | None = None,
        set_baseline: dict[str, Any] | None = None,
        clear_baseline: bool = False,
        annotate_baseline: dict[str, Any] | None = None,
        allow_from_stopped: bool = False,
    ) -> dict[str, Any]:
        current = self.current()
        current_latch = str(current.get("latch_state") or NOMINAL)
        if (
            current_latch == STOPPED
            and not allow_from_stopped
            and latch_state is not None
            and latch_state != STOPPED
        ):
            raise ThermalLatchProtected(
                "Thermal latch is STOPPED; an explicit safe reset is required "
                "before the state can change."
            )
        new_state = latch_state or current_latch

        if clear_baseline:
            new_baseline = None
        elif set_baseline is not None:
            new_baseline = set_baseline
        elif annotate_baseline is not None:
            merged = dict(current.get("baseline") or {})
            merged.update(annotate_baseline)
            new_baseline = merged
        else:
            new_baseline = current.get("baseline")

        self._repo.set(new_state, new_baseline)
        self._conn.commit()
        return {"latch_state": new_state, "baseline": new_baseline}

    # -- commands ---------------------------------------------------------

    def ensure_throttle(self, baseline: dict[str, Any]) -> dict[str, Any]:
        """Enter THROTTLED and capture the user's GPU profile verbatim."""
        return self._apply(latch_state=THROTTLED, set_baseline=dict(baseline))

    def mark_hold(self) -> dict[str, Any]:
        """Stay THROTTLED without touching the preserved baseline."""
        return self._apply(latch_state=THROTTLED)

    def mark_nominal(self, *, clear_baseline: bool = False) -> dict[str, Any]:
        """Return to NOMINAL after verified host restoration."""
        return self._apply(latch_state=NOMINAL, clear_baseline=clear_baseline)

    def mark_stopped(self) -> dict[str, Any]:
        """Escalate to STOPPED. Always permitted; baseline kept as evidence."""
        return self._apply(latch_state=STOPPED)

    def annotate_restore_failure(self, error: str) -> dict[str, Any]:
        """Record failed profile restoration on the durable baseline."""
        return self._apply(annotate_baseline={"last_restore_error": error})

    def reset_to_nominal(self) -> dict[str, Any]:
        """The only path out of STOPPED: explicit human-safe reset."""
        return self._apply(
            latch_state=NOMINAL, clear_baseline=True, allow_from_stopped=True
        )