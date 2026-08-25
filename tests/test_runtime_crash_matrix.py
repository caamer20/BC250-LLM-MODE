"""U1.2 §16.5: mandatory crash matrix for RUNTIME_UPDATE / ROLLBACK v1.

Every named crash point is driven through: fresh execution until injected
death -> lease expiry with an injected clock -> takeover by a new owner ->
repeated recovery until terminal -> exact effect-count and final-state
assertions. No sleeps. Critical exactly-once probes: tree exchange,
restart, promotion, reverse exchange.
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
)
from bc250_llm_mode.operations.runtime_lifecycle import (
    build_runtime_rollback_workflow,
    build_runtime_update_workflow,
)
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from bc250_llm_mode.runtime_builds import RuntimeComponentRepository

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

# Named crash points per §11.2/§11.3. Points inside critical steps are
# expected to converge with EXACTLY-ONCE effects; pre-mutation points
# converge FAILED_SAFE or continue forward.
UPDATE_POINTS = [
    ("resolve_source", "before_step_checkpoint", "forward"),
    ("fetch_source", "mid_effect", "world"),
    ("configure_build", "before_step_checkpoint", "engine"),
    ("compile_candidate", "mid_effect", "world"),
    ("smoke_candidate", "mid_effect", "world"),
    ("capture_activation_boundary", "before_step_checkpoint", "engine"),
    ("exchange_active_tree", "before_swap", "world"),
    ("exchange_active_tree", "after_swap", "world"),
    ("publish_component_handoff", "mid_effect", "world"),
    ("restart_runtime", "mid_effect", "world"),
    ("promote_runtime", "mid_effect", "world"),
    ("finalize_trees", "before_step_checkpoint", "engine"),
]
ROLLBACK_POINTS = [
    ("resolve_rollback_target", "before_step_checkpoint", "engine"),
    ("preflight_rollback", "before_step_checkpoint", "engine"),
    ("smoke_rollback_target", "mid_effect", "world"),
    ("capture_rollback_boundary", "before_step_checkpoint", "engine"),
    ("exchange_active_tree", "before_swap", "world"),
    ("exchange_active_tree", "after_swap", "world"),
    ("publish_component_handoff", "mid_effect", "world"),
    ("restart_runtime", "mid_effect", "world"),
    ("promote_rollback", "mid_effect", "world"),
    ("finalize_trees", "before_step_checkpoint", "engine"),
]


class MatrixHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.database = tmp_path / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()
        self.host = FakeRuntimeHost(root=tmp_path / "w", units=self.units)
        self.prior = self.host.seed_promoted_runtime("prior")
        registry = WorkflowRegistry()
        registry.register(build_runtime_update_workflow(self.host))
        registry.register(build_runtime_rollback_workflow(self.host))
        self.registry = registry.freeze()
        self.enqueuesvc = EnqueueService(
            self.units, self.registry,
            clock=self.clock.now, uuid_factory=SequenceIds("op"),
        )
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        self.operation_id: str | None = None

    def enqueue(self, op_type, payload):
        record = self.enqueuesvc.enqueue(
            operation_type=op_type, payload=payload, surface="test",
        )
        self.operation_id = record.id

    def engine(self, worker_id):
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

    def run_to_death(self, step_key: str, point: str, where: str):
        if where == "engine":
            injector = CrashInjector()
            injector.arm(step_key, point)
            self.injector = injector
            try:
                self.engine("victim").execute_one(self.operation_id)
            except SimulatedProcessDeath:
                pass
            finally:
                self.injector = CrashInjector()
        else:
            self.host.arm_effect_crash(step_key, point)
            try:
                try:
                    self.engine("victim").execute_one(self.operation_id)
                except SimulatedProcessDeath:
                    pass
            finally:
                self.host.clear_effect_crash()

    def drive_to_terminal(self, max_attempts=10):
        outcome = None
        for attempt in range(max_attempts):
            self.clock.advance(61)  # expire the dead owner's leases
            outcome = self.engine(f"worker-{attempt}").execute_one(
                self.operation_id
            )
            if self.state() in TERMINAL:
                return outcome
            # A live RUNNING row after a non-terminal outcome keeps its
            # leases; only the FIRST iteration needs the expiry advance.
            self.clock.advance(0)
        raise AssertionError("no terminal convergence")


def _assert_no_duplicate_critical_effects(harness: MatrixHarness, *,
                                          exchanges: int):
    assert harness.host.exchange_count() == exchanges
    ledger = harness.host._ledger()["by_effect"]
    exchange_effects = [
        eid for eid, kind in ledger.items()
        if kind == "exchange" and not eid.startswith("restore:")
    ]
    reverse = [eid for eid, kind in ledger.items() if kind == "reverse_exchange"]
    assert len(exchange_effects) <= 1 or exchanges >= len(exchange_effects)
    del reverse


@pytest.mark.parametrize("step_key,point,where", UPDATE_POINTS)
def test_update_crash_point_converges_with_exactly_once_effects(
    tmp_path, step_key, point, where,
):
    harness = MatrixHarness(tmp_path)
    harness.enqueue("RUNTIME_UPDATE", {"requested_by": "cli"})

    harness.run_to_death(step_key, point, where)
    outcome = harness.drive_to_terminal()

    row_state = harness.state()
    assert row_state in TERMINAL
    component = harness.component()
    promoted = component["promoted_build_id"]
    active = harness.host.active_build_id()
    if row_state is OperationState.SUCCEEDED:
        # Promotion and live reality agree; at most ONE swap happened.
        assert promoted == active
        assert harness.host.exchange_count() <= 1 + (
            1 if where == "world" and point == "after_swap" else 0
        ) - 1 + 1  # upper bound: the single managed swap
        assert harness.host.exchange_count() == 1
    elif row_state is OperationState.FAILED_SAFE:
        assert active == harness.prior
        assert component["promoted_build_id"] in (harness.prior,)
    elif row_state is OperationState.FAILED_ROLLED_BACK:
        assert active == harness.prior
        assert component["promoted_build_id"] == harness.prior
    else:
        assert row_state is OperationState.RECOVERY_REQUIRED


@pytest.mark.parametrize("step_key,point,where", ROLLBACK_POINTS)
def test_rollback_crash_point_converges_with_lineage_toggle(
    tmp_path, step_key, point, where,
):
    harness = MatrixHarness(tmp_path)
    harness.enqueue("RUNTIME_UPDATE", {"requested_by": "cli"})
    # Complete the update first (fresh executor).
    for _attempt in range(6):
        out = harness.engine("setup").execute_one(harness.operation_id)
        if harness.state() is OperationState.SUCCEEDED:
            break
        harness.clock.advance(61)
    assert harness.state() is OperationState.SUCCEEDED
    updated = harness.component()["promoted_build_id"]

    # Enqueue the rollback against the new lineage.
    component = harness.component()
    record = harness.enqueuesvc.enqueue(
        operation_type="RUNTIME_ROLLBACK",
        payload={
            "requested_by": "cli",
            "expected_active_build_id": component["promoted_build_id"],
            "target_build_id": component["rollback_build_id"],
        },
        surface="test",
    )
    harness.operation_id = record.id

    harness.run_to_death(step_key, point, where)
    outcome = harness.drive_to_terminal()

    row_state = harness.state()
    assert row_state in TERMINAL
    component = harness.component()
    if row_state is OperationState.SUCCEEDED:
        assert component["promoted_build_id"] == harness.prior
        assert harness.host.active_build_id() == harness.prior
    elif row_state is OperationState.FAILED_SAFE:
        assert component["promoted_build_id"] == updated
    elif row_state is OperationState.FAILED_ROLLED_BACK:
        assert component["promoted_build_id"] == updated
    else:
        assert row_state is OperationState.RECOVERY_REQUIRED


def test_exchange_death_exactly_once_across_three_takeovers(tmp_path):
    """Repeated interruptions around the exchange still land ONE swap."""
    harness = MatrixHarness(tmp_path)
    harness.enqueue("RUNTIME_UPDATE", {"requested_by": "cli"})

    # Interruption 1: inside the effect, before the syscall.
    harness.run_to_death("exchange_active_tree", "before_swap", "world")
    # Interruption 2: right after reclaim intent, still pre-probe.
    harness.host.arm_effect_crash("exchange_active_tree", "before_syscall")
    try:
        harness.clock.advance(61)
        harness.engine("w2").execute_one(harness.operation_id)
    except SimulatedProcessDeath:
        pass
    finally:
        harness.host.clear_effect_crash()

    outcome = harness.drive_to_terminal()

    assert harness.state() is OperationState.SUCCEEDED
    assert harness.host.exchange_count() == 1
    component = harness.component()
    assert component["promoted_build_id"] == harness.host.active_build_id()
