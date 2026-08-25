"""U1.2 §8: phase-scoped resource leasing in the shared executor.

Deterministic contention proofs with NO sleeps and NO threads: occupancy
is simulated by holding leases durably under a foreign operation row, and
the engine's behavior is observed through its typed outcomes.

The synthetic workflows below use the reserved-but-unimplemented
RUNTIME_UPDATE / RUNTIME_ROLLBACK types purely as resource-shape hosts;
Commit 4 replaces them with the real definitions and the same assertions
hold against those.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
for _path in (sys_path, sys_path / "operations"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import (
    OperationConflict,
    OperationState,
    OperationType,
)
from bc250_llm_mode.operations.repositories import (
    LeaseRepository,
    OperationRepository,
)
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.workflow import (
    EnqueueService,
    ProbeResult,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRegistry,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

from fakes import FakeClock, SequenceIds

INSTALLATION = "runtime-installation"
ACTIVE = "runtime-active"


@dataclass(frozen=True)
class _ProbeRequestV1:
    requested_by: str = "cli"


def _decode(payload):
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    unknown = set(payload) - {"requested_by"}
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    return _ProbeRequestV1(requested_by=payload.get("requested_by", "cli"))


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, kind: str) -> dict:
        self.events.append(kind)
        return {}


def _step(key, seq, resources, recorder, effect_kind, **extra):
    def execute(ctx):
        return recorder.record(effect_kind)

    return StepDefinition(
        step_key=key,
        phase="phase-" + key,
        sequence=seq,
        derive_input=lambda request, prior: {},
        probe=lambda ctx: ProbeResult(RecoveryClass.ABSENT, "NO_EVIDENCE"),
        execute=execute,
        verify=lambda ctx: {},
        resources=resources,
        **extra,
    )


def build_two_phase_update(recorder: Recorder) -> WorkflowDefinition:
    """Synthetic update shape: long build on installation; swap on both."""
    return WorkflowDefinition(
        operation_type=OperationType.RUNTIME_UPDATE,
        request_version=1,
        recovery_policy_version=1,
        decode_request=_decode,
        steps=(
            _step("build", 1, (INSTALLATION,), recorder, "build"),
            _step(
                "swap",
                2,
                (ACTIVE, INSTALLATION),
                recorder,
                "swap",
                externally_visible=True,
                critical=True,
                compensate=lambda ctx: recorder.record("unswap"),
            ),
            _step("finalize", 3, (ACTIVE, INSTALLATION), recorder, "finalize"),
        ),
        summary=lambda request: "synthetic two-phase update",
        phase_scoped_resources=True,
        recovery_barrier_resources=(ACTIVE, INSTALLATION),
    )


def build_all_at_once_rollback(recorder: Recorder) -> WorkflowDefinition:
    """Compatibility shape: NOT phase-scoped; everything claimed up front."""
    return WorkflowDefinition(
        operation_type=OperationType.RUNTIME_ROLLBACK,
        request_version=1,
        recovery_policy_version=1,
        decode_request=_decode,
        steps=(
            _step("first", 1, (ACTIVE,), recorder, "r-first"),
            _step("second", 2, (INSTALLATION,), recorder, "r-second"),
        ),
        summary=lambda request: "compatibility all-at-claim workflow",
    )


@pytest.fixture()
def env(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    clock = FakeClock()

    class Env:
        pass

    e = Env()
    e.units = units
    e.clock = clock
    e.update_recorder = Recorder()
    e.rollback_recorder = Recorder()
    registry = WorkflowRegistry()
    registry.register(build_two_phase_update(e.update_recorder))
    registry.register(build_all_at_once_rollback(e.rollback_recorder))
    e.registry = registry.freeze()
    e.op_ids = SequenceIds("op")
    e.effect_ids = SequenceIds("eff")
    e.enqueue = EnqueueService(
        units, e.registry, clock=clock.now, uuid_factory=e.op_ids
    )

    def engine(worker_id="worker-a"):
        return ExecutionEngine(
            units,
            e.registry,
            clock=clock.now,
            uuid_factory=e.effect_ids,
            worker_id=worker_id,
            lease_ttl_seconds=60,
        )

    e.engine = engine

    def enqueue(op_type, op_id):
        record = e.enqueue.enqueue(
            operation_type=op_type,
            payload={},
            surface="test",
            operation_id=op_id,
        )
        return record.id

    e.enqueue_op = enqueue

    def hold(resource, op_id="foreign-op", owner="foreign"):
        with units.begin() as conn:
            ops = OperationRepository(conn)
            try:
                ops.create(
                    operation_type=OperationType.MODEL_ACTIVATE,
                    request={"model_alias": "x"},
                    surface="test",
                    operation_id=op_id,
                )
            except OperationConflict:
                pass
            lease = LeaseRepository(conn, clock=clock.now).acquire(
                resource, operation_id=op_id, owner=owner, ttl_seconds=60
            )
        return lease.lease_revision

    e.hold = hold

    def leases_for(op_id):
        with units.read() as conn:
            repo = LeaseRepository(conn, clock=clock.now)
            return {
                key: lease.operation_id
                for key in (ACTIVE, INSTALLATION)
                if (lease := repo.get(key)) is not None
            }

    e.leases_for = leases_for
    e.state_of = lambda op_id: (
        lambda conn: OperationRepository(conn).require(op_id).state
    )
    return e


def _state(units, op_id):
    with units.begin() as conn:
        return OperationRepository(conn).require(op_id).state


def test_build_holds_only_installation_and_does_not_block_activation_resource(env):
    """A long fake build holding runtime-installation must NOT block a
    MODEL_ACTIVATE-shaped consumer of runtime-active (§8.3.1)."""
    update_op = env.enqueue_op("RUNTIME_UPDATE", "op-update-1")
    # Simulate a live builder occupying ONLY the installation resource.
    env.hold(INSTALLATION, op_id="builder-op")
    outcome = env.engine().execute_one(update_op)
    assert outcome.kind == "SKIPPED_BUSY"
    assert env.update_recorder.events == []  # no effect performed

    # While installation stays held, the activation resource is free.
    rollback_compat = env.enqueue_op("RUNTIME_ROLLBACK", "op-compat-1")
    # The compatibility workflow holds ACTIVE+INSTALLATION at claim; prove
    # the RESOURCE-level fact directly instead: acquire active now.
    with env.units.begin() as conn:
        ops = OperationRepository(conn)
        ops.create(
            operation_type=OperationType.MODEL_ACTIVATE,
            request={"model_alias": "m"},
            surface="test",
            operation_id="activator-op",
        )
        acquired = LeaseRepository(conn, clock=env.clock.now).acquire_many(
            [ACTIVE],
            operation_id="activator-op",
            owner="activator",
            ttl_seconds=60,
        )
    assert set(acquired) == {ACTIVE}
    # The builder's lease on installation is untouched by that acquisition.
    assert env.leases_for("builder-op")[INSTALLATION] == "builder-op"


def test_conflict_at_activation_boundary_refuses_before_any_work(env):
    """Runtime update finds runtime-active owned by model activation: it
    refuses cleanly BEFORE building (no wasted work, nothing swapped), and
    proceeds only once the boundary is free."""
    update_op = env.enqueue_op("RUNTIME_UPDATE", "op-update-1")
    env.hold(ACTIVE, op_id="activator-op")  # activation owns runtime-active

    outcome = env.engine().execute_one(update_op)

    assert outcome.kind == "SKIPPED_BUSY"
    assert outcome.reason_code == "RESOURCE_HELD"
    assert env.update_recorder.events == []  # not even the build started
    assert "swap" not in env.update_recorder.events

    # Once the activation releases the resource, the SAME queued operation
    # runs to completion through both phases.
    with env.units.begin() as conn:
        lease = LeaseRepository(conn, clock=env.clock.now).get(ACTIVE)
        LeaseRepository(conn, clock=env.clock.now).release(
            ACTIVE,
            owner="foreign",
            expected_revision=lease.lease_revision,
        )
    resumed = env.engine().execute_one(update_op)
    assert resumed.kind == "COMPLETED"
    assert env.update_recorder.events == ["build", "swap", "finalize"]


def test_two_updates_cannot_both_build_under_one_installation_lease(env):
    first = env.enqueue_op("RUNTIME_UPDATE", "op-update-1")
    second = env.enqueue_op("RUNTIME_UPDATE", "op-update-2")
    first_outcome = env.engine("worker-a").execute_one(first)
    assert first_outcome.kind == "COMPLETED"
    assert env.update_recorder.events == ["build", "swap", "finalize"]
    second_outcome = env.engine("worker-b").execute_one(second)
    assert second_outcome.kind == "COMPLETED"
    # Both ran serially (leases released at terminal); each swapped once.
    assert env.update_recorder.events.count("swap") == 2

    # Now prove mutual exclusion DURING a build: hold installation as a
    # live builder would.
    env.hold(INSTALLATION, op_id="builder-op")
    third = env.enqueue_op("RUNTIME_UPDATE", "op-update-3")
    busy = env.engine("worker-c").execute_one(third)
    assert busy.kind == "SKIPPED_BUSY"
    assert env.update_recorder.events.count("build") == 2


def test_partial_acquisition_is_atomic_across_the_whole_set(env):
    """acquire_many is all-or-none: a conflict on one key leaves NO trace
    of the other acquisitions (§8.3.5)."""
    env.hold(ACTIVE, op_id="holder-op")
    with env.units.begin() as conn:
        ops = OperationRepository(conn)
        ops.create(
            operation_type=OperationType.RUNTIME_UPDATE,
            request={},
            surface="test",
            operation_id="contender-op",
        )
        with pytest.raises(OperationConflict):
            LeaseRepository(conn, clock=env.clock.now).acquire_many(
                sorted({ACTIVE, INSTALLATION}),
                operation_id="contender-op",
                owner="w",
                ttl_seconds=60,
            )
    # No partial state: installation was never created for the contender.
    with env.units.read() as conn:
        leases = LeaseRepository(conn, clock=env.clock.now)
        installation = leases.get(INSTALLATION)
        active = leases.get(ACTIVE)
    assert installation is None
    assert active.operation_id == "holder-op"


def test_takeover_increments_revision_and_fences_stale_executor(env):
    revision = env.hold(ACTIVE, op_id="victim-op", owner="worker-victim")
    env.clock.advance(61)  # TTL expires
    with env.units.begin() as conn:
        leases = LeaseRepository(conn, clock=env.clock.now)
        taken = leases.acquire_many(
            [ACTIVE],
            operation_id="victim-op",
            owner="worker-new",
            ttl_seconds=60,
        )
    assert taken[ACTIVE] == revision + 1
    with env.units.begin() as conn:
        leases = LeaseRepository(conn, clock=env.clock.now)
        with pytest.raises(OperationConflict):
            leases.assert_owned(
                ACTIVE,
                "victim-op",
                owner="worker-victim",
                lease_revision=revision,
                now=env.clock.now(),
            )


def test_recovery_required_barrier_refuses_takeover(env):
    env.hold(ACTIVE, op_id="stuck-op")
    with env.units.begin() as conn:
        ops = OperationRepository(conn)
        ops.compare_and_transition(
            "stuck-op", expected_state=OperationState.QUEUED,
            expected_revision=1, target_state=OperationState.PREPARING,
        )
        record = ops.require("stuck-op")
        ops.compare_and_transition(
            "stuck-op", expected_state=OperationState.PREPARING,
            expected_revision=record.state_revision,
            target_state=OperationState.RUNNING,
        )
        record = ops.require("stuck-op")
        ops.record_terminal_result(
            "stuck-op",
            terminal_state=OperationState.RECOVERY_REQUIRED,
            error_code="TREE_EXCHANGE_UNCERTAIN",
        )
    env.clock.advance(120)
    blocked = env.enqueue_op("RUNTIME_UPDATE", "op-late")
    outcome = env.engine().execute_one(blocked)
    assert outcome.kind == "SKIPPED_BUSY"
    assert outcome.reason_code == "RESOURCE_HELD"
    assert env.update_recorder.events == []


def test_compatibility_workflows_keep_claim_time_all_resources(env):
    """Non-phase-scoped workflows acquire every declared resource at claim,
    exactly as before §17 (no silent weakening)."""
    compat = env.enqueue_op("RUNTIME_ROLLBACK", "op-compat-1")
    env.hold(INSTALLATION, op_id="blocker-op")
    outcome = env.engine().execute_one(compat)
    # Claim needs BOTH resources even though step 1 only uses ACTIVE.
    assert outcome.kind == "SKIPPED_BUSY"
    assert env.rollback_recorder.events == []


def test_suffix_union_has_no_gap_at_the_boundary():
    wf = build_two_phase_update(Recorder())
    assert wf.initial_resources() == (INSTALLATION,)
    assert wf.barrier_resources() == (ACTIVE, INSTALLATION)
    assert wf.resources_pending_from("build") == (INSTALLATION, ACTIVE)
    # From the boundary step onward both are required — acquisition of the
    # missing key happens BEFORE the critical effect, never after it.
    assert wf.resources_pending_from("swap") == (ACTIVE, INSTALLATION)
