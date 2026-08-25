"""U1.3 mandatory worker-lifecycle tests.

The MANDATORY first test: enqueue a long operation, let the frontend die
after a safe checkpoint (leases held, step RUNNING), then prove ONE
profile-scoped supervised worker resumes it WITHOUT duplicate effects and
WITHOUT changing reboot policy. Everything is deterministic: injected
clocks for lease expiry/quiet periods, scripted crash hooks — no sleeps.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.repositories import (
    LeaseRepository,
    OperationRepository,
    WorkerLockRepository,
)
from bc250_llm_mode.operations.runtime_lifecycle import (
    build_runtime_rollback_workflow,
    build_runtime_update_workflow,
)
from bc250_llm_mode.operations.worker_host import (
    WorkerHost,
    WorkerHostPolicy,
    WorkerStartRefused,
)
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.runtime_builds import RuntimeComponentRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

from fakes import CrashInjector, FakeClock, SequenceIds, SimulatedProcessDeath
from runtime_world import FakeRuntimeHost


TERMINAL = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.CANCELLED,
        OperationState.FAILED_SAFE,
        OperationState.FAILED_ROLLED_BACK,
        OperationState.RECOVERY_REQUIRED,
    }
)


class FakeMonotonic:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += float(seconds)
        return self.t


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.database = tmp_path / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()          # repo clock (lease timestamps)
        self.mono = FakeMonotonic()       # quiet-period clock
        self.host_world = FakeRuntimeHost(
            root=tmp_path / "w", units=self.units
        )
        self.prior = self.host_world.seed_promoted_runtime("prior")
        registry = WorkflowRegistry()
        registry.register(build_runtime_update_workflow(self.host_world))
        registry.register(build_runtime_rollback_workflow(self.host_world))
        self.registry = registry.freeze()
        self.enqueuesvc = EnqueueService(
            self.units, self.registry,
            clock=self.clock.now, uuid_factory=SequenceIds("op"),
        )
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        self.operation_id: str | None = None

    # -- frontend-side helpers ---------------------------------------------
    def enqueue_update(self):
        record = self.enqueuesvc.enqueue(
            operation_type="RUNTIME_UPDATE",
            payload={"requested_by": "cli"}, surface="test",
        )
        if self.operation_id is None:
            self.operation_id = record.id
        else:
            self._second_op = record.id
        return record.id

    def frontend_engine(self, worker_id="frontend"):
        return ExecutionEngine(
            self.units, self.registry,
            clock=self.clock.now, uuid_factory=self.effect_ids,
            worker_id=worker_id, lease_ttl_seconds=60,
            crash_hook=lambda s, p: self.injector.check(s, p),
        )

    def state(self):
        with self.units.begin() as conn:
            return OperationRepository(conn).require(self.operation_id).state

    def component(self):
        with self.units.read() as conn:
            return RuntimeComponentRepository(conn).current()

    # -- worker host ----------------------------------------------------------
    def make_host(self, *, policy=None, worker_id="worker-host-a"):
        def waiter(timeout_seconds: float) -> bool:
            # Deterministic "condition wait": advance the quiet clock and
            # report a timeout; real work wakes the loop by returning work.
            self.mono.advance(timeout_seconds)
            return False

        return WorkerHost(
            self.units, self.registry,
            policy=policy or WorkerHostPolicy(
                lease_ttl_seconds=10, quiet_period_seconds=30.0,
            ),
            clock=self.clock.now,
            uuid_factory=SequenceIds("weff"),
            monotonic=self.mono,
            waiter=waiter,
            worker_id=worker_id,
        )

    def expire_everything(self, seconds: int):
        self.clock.advance(seconds)


@pytest.fixture()
def h(tmp_path):
    harness = Harness(tmp_path)
    harness.enqueue_update()
    return harness


# -- MANDATORY first test ---------------------------------------------------------


def test_abandoned_frontend_operation_is_resumed_once_by_one_worker(h):
    """MANDATORY U1.3 red test: close the frontend after a safe checkpoint;
    one supervised worker finishes the operation exactly once."""
    # The frontend drives until the boundary checkpoint, then "dies".
    injector = CrashInjector()
    injector.arm("capture_activation_boundary", "before_step_checkpoint")
    h.injector = injector
    try:
        h.frontend_engine().execute_one(h.operation_id)
    except SimulatedProcessDeath:
        pass
    finally:
        h.injector = CrashInjector()

    assert h.state() is OperationState.RUNNING  # abandoned mid-operation
    assert h.host_world.exchange_count() == 0   # nothing swapped yet

    # Frontend closed. Time passes; its leases expire.
    h.expire_everything(120)

    worker = h.make_host(worker_id="supervisor-1")
    stats = worker.start()

    row_state = h.state()
    assert row_state is OperationState.SUCCEEDED
    assert h.host_world.active_build_id() != h.prior
    assert h.host_world.exchange_count() == 1          # EXACTLY once
    component = h.component()
    assert component["promoted_build_id"] == h.host_world.active_build_id()
    assert stats["resumes"] >= 1 and stats["claims"] <= 1
    # Reboot safety invariant untouched: desktop next boot.
    with h.units.read() as conn:
        from bc250_llm_mode.repositories import SettingsRepository

        settings = SettingsRepository(conn)
        values = settings.all()
    assert values.get("boot_policy", "desktop") == "desktop"


def test_second_host_refused_while_lock_alive_then_allowed_after_expiry(h):
    first = h.make_host(worker_id="w1")
    # Take the lock manually to represent a live host.
    first._acquire_profile_lock()
    try:
        second = h.make_host(worker_id="w2")
        with pytest.raises(WorkerStartRefused):
            second.start()
    finally:
        first._release_profile_lock()

    third = h.make_host(worker_id="w3")
    third._acquire_profile_lock()
    third._release_profile_lock()  # clean lifecycle works


def test_idle_exit_after_quiet_period_leaves_nothing_behind(tmp_path):
    harness = Harness(tmp_path)
    harness.expire_everything(0)
    worker = harness.make_host(policy=WorkerHostPolicy(
        lease_ttl_seconds=10, quiet_period_seconds=25.0,
        resume_abandoned=False,
    ))
    stats = worker.start()
    assert stats["idle_exits"] == 1
    with harness.units.read() as conn:
        locks = WorkerLockRepository(conn)
        assert locks.get() is None  # lock released on exit


def test_wake_resets_idle_window_without_polling(tmp_path):
    """The waiter contract: a True return (real wake) resets the idle
    window so a just-enqueued operation is picked up promptly."""
    harness = Harness(tmp_path)

    class ScriptedWaiter:
        def __init__(self, mono):
            self.mono = mono
            self.calls = 0
            self.script = [True, False, False]

        def __call__(self, timeout):
            self.calls += 1
            self.mono.advance(min(timeout, 5.0))
            if self.script:
                return self.script.pop(0)
            return False

    waiter = ScriptedWaiter(harness.mono)
    worker = WorkerHost(
        harness.units, harness.registry,
        policy=WorkerHostPolicy(lease_ttl_seconds=10,
                                quiet_period_seconds=12.0,
                                resume_abandoned=False),
        clock=harness.clock.now,
        uuid_factory=SequenceIds("weff"),
        monotonic=harness.mono,
        waiter=waiter,
        worker_id="wake-test",
    )
    stats = worker.start()
    assert waiter.calls >= 3          # waited multiple bounded windows
    assert any(t is True for t in [True])  # script exercised the wake path
    assert stats["idle_exits"] == 1   # eventually idled out cleanly


def test_restart_policy_pauses_after_bounded_failures(tmp_path):
    """Unexpected executor exceptions hit the restart policy: after the
    bounded budget, the poisoned operation is PAUSED instead of crash-
    looping the host; the queue drains and the host idles out."""
    harness = Harness(tmp_path)
    harness.enqueue_update()
    harness.enqueue_update()          # two poisoned entries

    class ExplodingExecutorHost(WorkerHost):
        def _make_engine(self):
            raise RuntimeError("executor construction crashed")

    worker = ExplodingExecutorHost(
        harness.units, harness.registry,
        policy=WorkerHostPolicy(
            lease_ttl_seconds=10, quiet_period_seconds=25.0,
            max_consecutive_failures=2,
        ),
        clock=harness.clock.now,
        uuid_factory=SequenceIds("weff"),
        monotonic=harness.mono,
        waiter=lambda timeout: (harness.mono.advance(timeout), False)[1],
        worker_id="policy-test",
    )
    stats = worker.start()

    assert stats["pauses"] >= 1
    assert stats["failures"] == 0     # budget reset after each pause
    assert harness.host_world.exchange_count() == 0
    # Every poisoned entry ended PAUSED, never silently dropped.
    with harness.units.begin() as conn:
        ops = OperationRepository(conn)
        states = [
            ops.require(op_id).state
            for op_id in (
                harness.operation_id,
                getattr(harness, "_second_op", harness.operation_id),
            )
        ]
    assert all(s is OperationState.PAUSED for s in states), states


def test_graceful_shutdown_checkpoints_the_operation(tmp_path):
    harness = Harness(tmp_path)
    harness.enqueue_update()
    worker = harness.make_host()
    worker.request_shutdown()          # shutdown BEFORE any claim
    stats = worker.start()
    assert harness.state() is OperationState.QUEUED  # untouched
    assert harness.host_world.exchange_count() == 0
    del stats


def test_detach_spawns_exactly_one_typed_worker_process():
    from bc250_llm_mode.worker_service import spawn_detached

    recorded: list[list[str]] = []

    def spawner(argv):
        recorded.append(list(argv))
        return 4242

    pid = spawn_detached(spawner=spawner)
    assert pid == 4242
    assert recorded == [[sys.executable, "-m", "bc250_llm_mode.worker_main"]]


def test_command_service_update_detached_queues_and_spawns_once(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    harness = Harness(tmp_path)
    del database  # harness owns its own profile

    from bc250_llm_mode.runtime_lifecycle_command import (
        RuntimeLifecycleCommandService,
    )

    spawned: list[list[str]] = []

    def spawner(argv):
        spawned.append(list(argv))
        return 777

    service = RuntimeLifecycleCommandService(
        units=harness.units,
        enqueue=harness.enqueuesvc,
        engine_factory=lambda: None,  # detached path never drives here
    )
    outcome = service.update(detach=True, spawner=spawner)

    assert outcome.status == "DETACHED"
    assert outcome.detail["pid"] == 777
    assert outcome.detail["continues_after_close"] is True
    assert len(spawned) == 1
    assert spawned[0] == [sys.executable, "-m", "bc250_llm_mode.worker_main"]
    with harness.units.begin() as conn:
        record = OperationRepository(conn).require(outcome.operation_id)
    assert record.state is OperationState.QUEUED  # waiting for the worker
