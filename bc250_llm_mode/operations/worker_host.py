"""U1.3 explicit worker lifecycle: one bounded, profile-scoped host.

The host exists ONLY because someone explicitly started it (embedded
``start()``, or ``WorkerService.detach`` spawning a helper process after
an enqueue). It is never constructed by composition, never starts
inference, and never touches boot policy.

Guarantees (plan §U1.3):

- ONE host per application profile, enforced by the migration-006
  ``worker_locks`` row heartbeated every loop;
- resumes ABANDONED operations (RUNNING rows whose every lease has
  expired) exactly once via standard takeover probes — no duplicate
  effects;
- idle exit after a bounded quiet period measured on an INJECTED
  monotonic clock;
- bounded restart policy: consecutive unexpected failures pause the
  operation; the failure budget resets on any clean outcome;
- graceful shutdown checkpoints through the engine's existing
  shutdown->PAUSED path;
- waiting is condition/event-backed with BOUNDED timeouts driven by
  injected clocks in tests — ``timeout=0`` polling is forbidden.
"""

from __future__ import annotations

import threading
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Callable

from ..legacy_import import utcnow
from .engine import ExecutionEngine, ExecutionOutcome
from .model import OperationState
from .repositories import (
    LeaseRepository,
    OperationRepository,
    WorkerLockRepository,
)

WORKER_LOCK_TOKEN = WorkerLockRepository.TOKEN

_TERMINAL_STATES = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.CANCELLED,
        OperationState.FAILED_SAFE,
        OperationState.FAILED_ROLLED_BACK,
        OperationState.RECOVERY_REQUIRED,
    }
)

_ACTIVE_WORK_STATES = frozenset(
    {
        OperationState.PREPARING,
        OperationState.RUNNING,
        OperationState.VERIFYING,
    }
)


class WorkerStartRefused(RuntimeError):
    """Another unexpired host owns the profile lock."""

    def __init__(self, code: str = "WORKER_ALREADY_RUNNING") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkerHostPolicy:
    lease_ttl_seconds: int = 60          # heartbeat cadence bound too
    quiet_period_seconds: float = 15.0   # idle exit after this much silence
    max_consecutive_failures: int = 2
    resume_abandoned: bool = True        # takeover of expired-lease work


class WorkerHost:
    """One supervised executor instance for one profile/database."""

    def __init__(
        self,
        units: Any,
        registry: Any,
        *,
        policy: WorkerHostPolicy | None = None,
        clock: Callable[[], str] | None = None,
        uuid_factory: Callable[[], str] | None = None,
        monotonic: Callable[[], float],
        waiter: Callable[[float], bool],
        worker_id: str | None = None,
        terminal_observer: Callable[[str], Any] | None = None,
    ) -> None:
        self.units = units
        self.registry = registry
        self.policy = policy or WorkerHostPolicy()
        self.clock = clock or utcnow
        self.uuid_factory = uuid_factory or (lambda: str(_uuid.uuid4()))
        # waiter(timeout_seconds) -> woke_early(bool). Production binds a
        # threading.Condition.wait; tests bind deterministic fakes that
        # advance the injected clocks instead of sleeping.
        self.monotonic = monotonic
        self.waiter = waiter
        self.worker_id = worker_id or f"worker-host-{_uuid.uuid4().hex[:12]}"
        self.terminal_observer = terminal_observer
        self._shutdown = threading.Event()
        self._lock_revision: int | None = None
        self.stats = {"claims": 0, "resumes": 0, "failures": 0,
                      "pauses": 0, "idle_exits": 0}

    # -- lifecycle ------------------------------------------------------------------

    def request_shutdown(self) -> None:
        self._shutdown.set()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

    def start(self) -> dict[str, Any]:
        """Acquire the profile lock and run until idle or shutdown."""
        self._acquire_profile_lock()
        try:
            return self._loop()
        finally:
            self._release_profile_lock()

    # -- single instance -------------------------------------------------------------

    def _acquire_profile_lock(self) -> None:
        with self.units.begin() as conn:
            locks = WorkerLockRepository(conn, clock=self.clock)
            existing = locks.get()
            if existing is not None \
                    and existing["expires_at"] > self.clock():
                raise WorkerStartRefused()
            try:
                lock = locks.acquire(
                    owner=self.worker_id,
                    ttl_seconds=self.policy.lease_ttl_seconds,
                )
            except Exception as exc:
                raise WorkerStartRefused() from exc
            self._lock_revision = int(lock["lease_revision"])

    def _heartbeat_lock(self) -> None:
        if self._lock_revision is None:
            return
        try:
            with self.units.begin() as conn:
                locks = WorkerLockRepository(conn, clock=self.clock)
                lock = locks.heartbeat(
                    owner=self.worker_id,
                    expected_revision=self._lock_revision,
                    ttl_seconds=self.policy.lease_ttl_seconds,
                )
                self._lock_revision = int(lock["lease_revision"])
        except Exception:  # noqa: BLE001 - lost lock ends the loop cleanly
            self._shutdown.set()

    def _release_profile_lock(self) -> None:
        if self._lock_revision is None:
            return
        try:
            with self.units.begin() as conn:
                WorkerLockRepository(conn, clock=self.clock).release(
                    owner=self.worker_id,
                    expected_revision=self._lock_revision,
                )
        except Exception:  # noqa: BLE001 - expiry cleans up anyway
            pass
        finally:
            self._lock_revision = None

    # -- work selection ---------------------------------------------------------------

    def _next_work(self) -> tuple[str | None, bool]:
        """``(operation_id, resumed)``: oldest QUEUED first; otherwise an
        ABANDONED active row whose every lease has expired."""
        with self.units.begin() as conn:
            ops = OperationRepository(conn, clock=self.clock)
            leases = LeaseRepository(conn, clock=self.clock)
            now = self.clock()
            for record in ops.list_active():
                if record.state is OperationState.QUEUED:
                    return record.id, False
            if not self.policy.resume_abandoned:
                return None, False
            for record in ops.list_active():
                if record.state not in _ACTIVE_WORK_STATES:
                    continue
                held = leases.leases_for_operation(record.id)
                if held and all(
                    lease.expires_at <= now for lease in held
                ):
                    return record.id, True
        return None, False

    def _heartbeat(self, operation_id: str | None) -> None:
        self._heartbeat_lock()
        if operation_id is None:
            return
        try:
            with self.units.begin() as conn:
                leases = LeaseRepository(conn, clock=self.clock)
                for lease in leases.leases_for_operation(operation_id):
                    if lease.owner == self.worker_id:
                        leases.heartbeat(
                            lease.resource_key,
                            owner=self.worker_id,
                            expected_revision=lease.lease_revision,
                            ttl_seconds=self.policy.lease_ttl_seconds,
                        )
        except Exception:  # noqa: BLE001 - heartbeat best-effort
            pass

    # -- execution ---------------------------------------------------------------------

    def _make_engine(self) -> ExecutionEngine:
        return ExecutionEngine(
            self.units,
            self.registry,
            clock=self.clock,
            uuid_factory=self.uuid_factory,
            worker_id=self.worker_id,
            lease_ttl_seconds=self.policy.lease_ttl_seconds,
            shutdown_requested=lambda: self.shutdown_requested,
            terminal_observer=self.terminal_observer,
        )

    def run_once(self) -> ExecutionOutcome | None:
        operation_id, resumed = self._next_work()
        if operation_id is None:
            return None
        if resumed:
            self.stats["resumes"] += 1
        else:
            self.stats["claims"] += 1
        self._heartbeat(operation_id)
        try:
            # Executor construction is inside the policy boundary: a
            # crash here is exactly what bounded restarts exist for.
            engine = self._make_engine()
            outcome = engine.execute_one(operation_id)
        except Exception:  # noqa: BLE001 - restart policy applies
            self.stats["failures"] += 1
            if self.stats["failures"] >= self.policy.max_consecutive_failures:
                self._pause_operation(operation_id)
                self.stats["pauses"] += 1
                self.stats["failures"] = 0
            return ExecutionOutcome(
                "FAILED_RETRYABLE", operation_id,
                reason_code="WORKER_RESTART_POLICY",
            )
        if outcome.kind != "FAILED_RETRYABLE":
            self.stats["failures"] = 0
        return outcome

    def _pause_operation(self, operation_id: str) -> None:
        try:
            with self.units.begin() as conn:
                ops = OperationRepository(conn, clock=self.clock)
                record = ops.require(operation_id)
                if record.state not in _TERMINAL_STATES:
                    ops.compare_and_transition(
                        operation_id,
                        expected_state=record.state,
                        expected_revision=record.state_revision,
                        target_state=OperationState.PAUSED,
                        event_code="WORKER_PAUSE_AFTER_FAILURES",
                        event_summary=(
                            f"paused after {self.policy.max_consecutive_failures}"
                            " consecutive worker failures"
                        ),
                    )
                    self.stats["pauses"] += 1
        except Exception:  # noqa: BLE001 - pause is best-effort
            pass

    # -- main loop ------------------------------------------------------------------------

    def _loop(self) -> dict[str, Any]:
        idle_since: float | None = None
        while not self.shutdown_requested:
            outcome = self.run_once()
            if self.shutdown_requested:
                break
            if outcome is None:
                now = self.monotonic()
                if idle_since is None:
                    idle_since = now
                remaining = (
                    self.policy.quiet_period_seconds - (now - idle_since)
                )
                if remaining <= 0:
                    self.stats["idle_exits"] += 1
                    break
                # Bounded condition-backed wait; tests drive the clock.
                if self.waiter(min(remaining, self.policy.lease_ttl_seconds)):
                    idle_since = None  # woken by real work
            else:
                idle_since = None
        return dict(self.stats)


__all__ = [
    "WORKER_LOCK_TOKEN",
    "WorkerHost",
    "WorkerHostPolicy",
    "WorkerStartRefused",
]
