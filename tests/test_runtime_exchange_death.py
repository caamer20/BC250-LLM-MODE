"""U1.2 MANDATORY first red test: exchange-death takeover (plan §11.1).

``RUNTIME_UPDATE v1`` swaps a staged, smoke-checked target tree into the
active path and process death occurs around the exchange effect. On
takeover the executor probes EXACT COMPONENT IDENTITIES (build IDs from
tree manifests, never filenames or desired state):

- target active and prior retained  -> checkpoint WITHOUT a second exchange;
- prior active and target staged    -> execute the original exchange once;
- neither provable                  -> RECOVERY_REQUIRED, delete/exchange NOTHING.

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
from bc250_llm_mode.operations.model import (
    OperationState,
    StepState,
)
from bc250_llm_mode.operations.repositories import (
    LeaseRepository,
    OperationRepository,
    StepRepository,
)
from bc250_llm_mode.operations.runtime_lifecycle import (
    RUNTIME_ACTIVE_RESOURCE,
    RUNTIME_INSTALLATION_RESOURCE,
    build_runtime_update_workflow,
)
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

from fakes import CrashInjector, FakeClock, SequenceIds, SimulatedProcessDeath
from runtime_world import FakeRuntimeHost


EXCHANGE_STEP = "exchange_active_tree"
TERMINAL_STATES = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.CANCELLED,
        OperationState.FAILED_SAFE,
        OperationState.FAILED_ROLLED_BACK,
        OperationState.RECOVERY_REQUIRED,
    }
)


class UpdateHarness:
    """Durable database + fake runtime world shared by fresh executors."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "profile"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()
        self.host = FakeRuntimeHost(
            root=tmp_path / "runtime-world", units=self.units
        )
        self.prior_build_id = self.host.seed_promoted_runtime("prior")
        self.operation_ids = SequenceIds("op")
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()

        registry = WorkflowRegistry()
        registry.register(build_runtime_update_workflow(self.host))
        self.registry = registry.freeze()
        self._enqueue = EnqueueService(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.operation_ids,
        )
        self.operation_id: str | None = None

    def enqueue_update(self, operation_id: str = "op-update-1"):
        record = self._enqueue.enqueue(
            operation_type="RUNTIME_UPDATE",
            payload={"requested_by": "cli"},
            surface="test",
            operation_id=operation_id,
        )
        self.operation_id = record.id
        return record

    def engine(self, worker_id: str) -> ExecutionEngine:
        return ExecutionEngine(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.effect_ids,
            worker_id=worker_id,
            lease_ttl_seconds=60,
            crash_hook=lambda step_key, point: self.injector.check(step_key, point),
        )

    def run_to_death(self, worker_id: str, step_key: str, point: str) -> None:
        injector = CrashInjector()
        injector.arm(step_key, point)
        self.injector = injector
        try:
            self.engine(worker_id).execute_one(self.operation_id)
        except SimulatedProcessDeath:
            pass
        finally:
            self.injector = CrashInjector()

    def operation_row(self):
        with self.units.begin() as conn:
            return OperationRepository(conn).require(self.operation_id)

    def step_row(self, step_key: str):
        with self.units.begin() as conn:
            return StepRepository(conn).require(self.operation_id, step_key)

    def held_leases(self) -> set[str]:
        with self.units.begin() as conn:
            leases = LeaseRepository(conn)
            found = set()
            for key in (RUNTIME_ACTIVE_RESOURCE, RUNTIME_INSTALLATION_RESOURCE):
                lease = leases.get(key)
                if lease is not None and lease.operation_id == self.operation_id:
                    found.add(key)
            return found


def _drive_to_terminal(harness: UpdateHarness, worker_id: str):
    # The dead executor's leases must EXPIRE before a fresh owner can take
    # over (revision++); the injected clock advances past the TTL instantly.
    harness.clock.advance(61)
    outcome = None
    for _attempt in range(12):
        outcome = harness.engine(worker_id).execute_one(harness.operation_id)
        state = harness.operation_row().state
        if state in TERMINAL_STATES:
            return outcome
    raise AssertionError("takeover did not converge to a terminal state")


def test_target_active_is_checkpointed_without_second_exchange(tmp_path):
    """Death after the exchange effect, before its checkpoint: takeover
    proves the target identity active and checkpoints WITHOUT swapping."""
    harness = UpdateHarness(tmp_path)
    harness.enqueue_update()
    harness.run_to_death("worker-a", EXCHANGE_STEP, "before_step_checkpoint")
    # The exchange effect landed exactly once; its step row stays RUNNING.
    assert harness.step_row(EXCHANGE_STEP).state is StepState.RUNNING
    assert harness.host.exchange_count() == 1
    target_id = harness.host.active_build_id()
    assert target_id is not None and target_id != harness.prior_build_id

    outcome = _drive_to_terminal(harness, "worker-b")

    row = harness.operation_row()
    assert row.state is OperationState.SUCCEEDED
    assert row.result_code == "RUNTIME_PROMOTED"
    assert harness.host.exchange_count() == 1  # no second swap ever
    assert harness.host.active_build_id() == target_id


def test_prior_active_executes_original_exchange_exactly_once(tmp_path):
    """Death inside the effect BEFORE the syscall: intent durable, swap not
    landed; takeover classifies ABSENT and performs the original exchange
    exactly once."""
    harness = UpdateHarness(tmp_path)
    harness.enqueue_update()
    # First attempt dies after intent but before the effect ran at all.
    harness.run_to_death("worker-a", EXCHANGE_STEP, "after_step_start")
    assert harness.host.exchange_count() == 0
    assert harness.host.active_build_id() == harness.prior_build_id

    # Takeover after lease expiry, then die INSIDE the effect after intent,
    # before the swap.
    harness.clock.advance(61)
    harness.host.arm_effect_crash(EXCHANGE_STEP, "before_swap")
    try:
        harness.engine("worker-b").execute_one(harness.operation_id)
    except SimulatedProcessDeath:
        pass
    finally:
        harness.host.clear_effect_crash()
    assert harness.host.exchange_count() == 0
    assert harness.step_row(EXCHANGE_STEP).state is StepState.RUNNING

    harness.clock.advance(61)
    _drive_to_terminal(harness, "worker-c")

    row = harness.operation_row()
    assert row.state is OperationState.SUCCEEDED
    # The ORIGINAL exchange executed exactly once across all attempts.
    assert harness.host.exchange_count() == 1
    new_active = harness.host.active_build_id()
    assert new_active is not None and new_active != harness.prior_build_id


def test_uncertain_arrangement_enters_recovery_without_touching_trees(tmp_path):
    """Neither arrangement provable: RECOVERY_REQUIRED; neither tree is
    deleted or exchanged and both leases remain as the barrier."""
    harness = UpdateHarness(tmp_path)
    harness.enqueue_update()
    harness.run_to_death("worker-a", EXCHANGE_STEP, "before_step_checkpoint")
    assert harness.host.exchange_count() == 1
    # Corrupt the evidence: destroy ALL tree manifests so no identity can
    # be proved from any locator.
    harness.host.destroy_manifests()
    snapshot_bytes = harness.host.snapshot_tree_bytes()

    outcome = _drive_to_terminal(harness, "worker-b")

    assert outcome.kind == "RECOVERY_REQUIRED_OUTCOME"
    row = harness.operation_row()
    assert row.state is OperationState.RECOVERY_REQUIRED
    assert row.error_code == "UNCERTAIN_UNSAFE"
    import json as _json

    detail = _json.loads(row.error_detail or "{}")
    assert detail["probe"] in ("TREE_EXCHANGE_UNCERTAIN", "NO_ACTIVE_IDENTITY")
    assert detail["step"] == EXCHANGE_STEP
    # Nothing was exchanged again and nothing was deleted.
    assert harness.host.exchange_count() == 1
    assert harness.host.snapshot_tree_bytes() == snapshot_bytes
    for locator in (harness.host.active_root, harness.host.retained_prior_root):
        assert locator.exists()
    # Both resource leases are retained as the recovery barrier.
    assert harness.held_leases() == {
        RUNTIME_ACTIVE_RESOURCE,
        RUNTIME_INSTALLATION_RESOURCE,
    }
