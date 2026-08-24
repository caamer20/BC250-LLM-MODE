"""Throttled progress policy/reporter (Session 5B §13.1).

Progress is monotonic within a phase; a phase change may reset its local
counter. Terminal/decision evidence never goes through throttling — those
are events, not progress writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Tuple
from typing import Any

from .model import OperationConflict


@dataclass(frozen=True)
class ProgressPolicy:
    """Persist only when something meaningful changed."""

    min_interval_ticks: int = 4
    min_delta: int = 1

    def should_emit(
        self,
        *,
        phase: str,
        current: int,
        last_phase: str | None,
        last_current: int,
        ticks_since_last_write: int,
    ) -> bool:
        if phase != last_phase:
            return True  # phase boundary always persists
        delta = current - last_current
        if delta <= 0:
            return False  # monotonic guard: never write backwards/no-op
        return (
            ticks_since_last_write >= self.min_interval_ticks
            and delta >= self.min_delta
        )


class ProgressReporter:
    """CAS progress writes that tolerate concurrent cancellation/conflict."""

    def __init__(self, repos_factory: Callable[[], Tuple[Any, Any, Any, Any]], policy=None) -> None:
        self._repos_factory = repos_factory
        self.policy = policy or ProgressPolicy()
        self._last_phase: str | None = None
        self._last_current = 0
        self._ticks = 0

    def tick(self) -> None:
        self._ticks += 1

    def maybe_emit(
        self,
        operation_id: str,
        *,
        phase: str,
        current: int,
        total: int | None = None,
        unit: str | None = None,
        summary: str | None = None,
    ) -> bool:
        if not self.policy.should_emit(
            phase=phase,
            current=current,
            last_phase=self._last_phase,
            last_current=self._last_current,
            ticks_since_last_write=self._ticks,
        ):
            return False
        emitted = self.emit(
            operation_id,
            phase=phase,
            current=current,
            total=total,
            unit=unit,
            summary=summary,
        )
        if emitted:
            self._ticks = 0
        return emitted

    def emit(
        self,
        operation_id: str,
        *,
        phase: str,
        current: int,
        total: int | None = None,
        unit: str | None = None,
        summary: str | None = None,
    ) -> bool:
        ops, _steps, _leases, _events = self._repos_factory()
        try:
            record = ops.update_progress(
                operation_id,
                phase=phase,
                current=current,
                total=total,
                unit=unit,
                summary=summary,
            )
        except OperationConflict:
            # A concurrent cancellation/transition won; reload instead of
            # overwriting it.
            refreshed = ops.get(operation_id)
            if refreshed is not None:
                self._last_phase = refreshed.progress_phase
                self._last_current = refreshed.progress_current
            return False
        self._last_phase = record.progress_phase
        self._last_current = record.progress_current
        self._ticks = 0
        return True