"""Bounded claim/run/shutdown worker loop (Session 5B §12).

The worker decides WHICH operation to run and WHEN to stop; it contains no
step semantics. It never starts itself from composition and shares no
connection across effects: every transaction opens through the injected
unit-of-work factory.
"""

from __future__ import annotations

import threading
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..legacy_import import utcnow
from .engine import ExecutionEngine, ExecutionOutcome


@dataclass
class WorkerStats:
    claims: int = 0
    completed: int = 0
    busy_skips: int = 0
    other_outcomes: int = 0
    outcomes: list = field(default_factory=list)


class Worker:
    def __init__(
        self,
        units: Any,
        registry: Any,
        *,
        worker_id: str | None = None,
        clock: Callable[[], str] | None = None,
        uuid_factory: Callable[[], str] | None = None,
        lease_ttl_seconds: int = 60,
        wake: Any | None = None,
    ) -> None:
        self.units = units
        self.registry = registry
        self.worker_id = worker_id or f"worker-{_uuid.uuid4().hex[:12]}"
        self.clock = clock or utcnow
        self.uuid_factory = uuid_factory or (
            lambda: str(_uuid.uuid4())
        )
        self.lease_ttl_seconds = lease_ttl_seconds
        # Injected wake primitive (threading.Event-like). No sleeps anywhere.
        self._wake = wake
        self._shutdown = threading.Event()

    # -- shutdown ------------------------------------------------------------

    def request_shutdown(self) -> None:
        self._shutdown.set()
        if self._wake is not None:
            self._wake.set()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

    def _wait_for_wake(self) -> bool:
        """Returns False when a (bounded) wait expired. Never sleeps blindly:
        the wake primitive is injected and tests drive it deterministically."""
        if self._wake is None:
            return True
        return self._wake.wait(timeout=0)

    # -- claiming --------------------------------------------------------------

    def claim_operation(self) -> str | None:
        """Oldest QUEUED operation; PAUSED requires an explicit resume."""
        with self.units.begin() as conn:
            from .repositories import OperationRepository

            ops = OperationRepository(conn, clock=self.clock)
            for record in ops.list_active():
                if record.state is __import__(
                    "bc250_llm_mode.operations.model",
                    fromlist=["OperationState"],
                ).OperationState.QUEUED:
                    return record.id
        return None

    # -- run -------------------------------------------------------------------

    def _make_engine(self) -> ExecutionEngine:
        return ExecutionEngine(
            self.units,
            self.registry,
            clock=self.clock,
            uuid_factory=self.uuid_factory,
            worker_id=self.worker_id,
            lease_ttl_seconds=self.lease_ttl_seconds,
            shutdown_requested=lambda: self.shutdown_requested,
        )

    def run_once(self) -> ExecutionOutcome | None:
        if self.shutdown_requested:
            self._wait_for_wake()
            return None
        operation_id = self.claim_operation()
        if operation_id is None:
            self._wait_for_wake()
            return None
        return self._make_engine().execute_one(operation_id)

    def run_until_idle(self, *, max_operations: int | None = None):
        """Run queued operations until none remain, shutdown is requested, or
        ``max_operations`` is hit. Returns collected outcomes."""
        collected = []
        runs = 0
        while not self.shutdown_requested and (
            max_operations is None or runs < max_operations
        ):
            outcome = self.run_once()
            if outcome is None:
                break
            runs += 1
            collected.append(outcome)
        return collected