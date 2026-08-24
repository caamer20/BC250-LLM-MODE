"""Session 5C entry corrections (plan §3): red tests before each fix.

Covers: critical-state cycling (§3.1), durable compensation takeover
(§3.2), durable reconstruction of effects (§3.3), intent-transaction
correctness (§3.4), per-step implementation versions (§3.5), and bounded
evidence / progress-pulse wiring (§3.6). No production adapter here.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.operations.engine import ExecutionEngine, LostLease
from bc250_llm_mode.operations.model import (
    OperationState,
    OperationType,
    StepState,
    can_transition,
)
from bc250_llm_mode.operations.repositories import (
    EventRepository,
    LeaseRepository,
    OperationRepository,
    StepRepository,
)
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.workflow import (
    ProbeResult,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRegistryError,
)

from fakes import FakeClock, SimulatedProcessDeath
from helpers import Harness


def _engine(harness: Harness, worker_id: str) -> ExecutionEngine:
    return ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
        worker_id=worker_id,
        lease_ttl_seconds=60,
        crash_hook=lambda step_key, pnt: harness.injector.check(step_key, pnt),
    )


def _registry_with(harness: Harness, **step_overrides):
    steps = tuple(
        (
            dataclasses.replace(step, **step_overrides[step.step_key])
            if step.step_key in step_overrides
            else step
        )
        for step in harness.workflow.steps
    )
    definition = WorkflowDefinition(
        operation_type=harness.workflow.operation_type,
        request_version=1,
        recovery_policy_version=1,
        decode_request=harness.workflow.decode_request,
        steps=steps,
        summary=harness.workflow.summary,
    )
    registry = WorkflowRegistry()
    registry.register(definition)
    return registry.freeze()


def _rows(harness: Harness, operation_id: str):
    with harness.units.begin() as conn:
        steps = StepRepository(conn, clock=FakeClock())
        return {s.step_key: s for s in steps.list(operation_id)}


def _operation(harness: Harness, operation_id: str) -> OperationState:
    with harness.units.begin() as conn:
        return OperationRepository(conn, clock=FakeClock()).require(
            operation_id
        ).state


# -- §3.1 critical-state cycling ----------------------------------------------


def test_transition_table_allows_committing_resolution_cycle():
    """ADR correction: COMMITTING resolves to VERIFYING or ROLLING_BACK."""
    assert can_transition(OperationState.COMMITTING, OperationState.VERIFYING)
    assert can_transition(OperationState.COMMITTING, OperationState.ROLLING_BACK)


def test_critical_step_enters_committing_before_effect(tmp_path):
    """Death after the external effect leaves the operation INSIDE the
    critical section, so cancellation cannot interleave."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-crit")
    harness.injector.arm("publish", "after_external_effect")

    with pytest.raises(SimulatedProcessDeath):
        _engine(harness, "worker-a").execute_one("op-crit")

    assert _operation(harness, "op-crit") is OperationState.COMMITTING


def test_cancellation_refused_once_critical_entered(tmp_path):
    from bc250_llm_mode.operations.model import InvalidTransition

    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-cancel2")
    harness.injector.arm("publish", "after_external_effect")
    with pytest.raises(SimulatedProcessDeath):
        _engine(harness, "worker-a").execute_one("op-cancel2")

    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        with pytest.raises(InvalidTransition):
            ops.request_cancel("op-cancel2")


def test_critical_completion_exits_to_verifying(tmp_path):
    """While the critical step verifies, the operation sits in COMMITTING;
    only after its verification commits does it continue."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-exit")
    states: list[OperationState] = []

    def spy_verify(ctx):
        states.append(_operation(harness, "op-exit"))
        return {}

    harness.registry = _registry_with(harness, publish={"verify": spy_verify})
    outcome = _engine(harness, "worker-a").execute_one("op-exit")
    assert outcome.reason_code == "SUCCEEDED"
    assert states == [OperationState.COMMITTING]


# -- §3.2 durable compensation takeover ----------------------------------------


def _death_during_compensation(harness: Harness, operation_id: str) -> None:
    """Drive an operation into ROLLING_BACK with apply_effect COMPENSATING."""

    def drifting_verify(ctx):
        raise AssertionError("drift")

    harness.registry = _registry_with(
        harness, verify_effect={"verify": drifting_verify}
    )
    harness.enqueue(desired_value="v1", operation_id=operation_id)
    harness.injector.arm("apply_effect", "during_compensation")
    with pytest.raises(SimulatedProcessDeath):
        _engine(harness, "worker-a").execute_one(operation_id)
    harness.clock.advance(120)


def test_forward_resume_of_rolling_back_row_converges(tmp_path):
    """A ROLLING_BACK row left by a dead executor resumes under a new
    executor and finishes FAILED_ROLLED_BACK with one restoration."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    _death_during_compensation(harness, "op-rb")

    outcome = _engine(harness, "worker-b").execute_one("op-rb")
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "FAILED_ROLLED_BACK"
    active = harness.world.read_active()
    assert active["value"] == "v1" and active["compensated"] is True
    assert harness.recorder.compensation_order() == ["apply_effect"]
    assert _operation(harness, "op-rb") is OperationState.FAILED_ROLLED_BACK
    row = _rows(harness, "op-rb")["apply_effect"]
    assert row.state is StepState.COMPENSATED


def test_compensation_probe_complete_checkpoints_without_effect(tmp_path):
    """An interrupted COMPENSATING step whose restoration postcondition is
    already true checkpoints COMPENSATED without repeating the effect."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    _death_during_compensation(harness, "op-probe")

    # Reality was restored by someone else while the row stayed COMPENSATING.
    harness.world.restore_prior()
    harness.registry = _registry_with(
        harness,
        apply_effect={
            "probe_restoration": lambda ctx: ProbeResult(
                RecoveryClass.COMPLETE, "ALREADY_RESTORED"
            )
        },
    )
    outcome = _engine(harness, "worker-b").execute_one("op-probe")
    assert outcome.reason_code == "FAILED_ROLLED_BACK"
    assert harness.recorder.compensation_order() == [], (
        "restoration probe must prevent a duplicate compensation effect"
    )
    row = _rows(harness, "op-probe")["apply_effect"]
    assert row.state is StepState.COMPENSATED


def test_compensation_probe_uncertain_barriers_with_lease(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    _death_during_compensation(harness, "op-unc")

    harness.world.restore_prior()
    harness.registry = _registry_with(
        harness,
        apply_effect={
            "probe_restoration": lambda ctx: ProbeResult(
                RecoveryClass.UNCERTAIN_MANUAL, "CANNOT_PROVE"
            )
        },
    )
    outcome = _engine(harness, "worker-b").execute_one("op-unc")
    assert outcome.kind == "RECOVERY_REQUIRED_OUTCOME"
    assert _operation(harness, "op-unc") is OperationState.RECOVERY_REQUIRED
    with harness.units.begin() as conn:
        lease = LeaseRepository(conn, clock=FakeClock()).get("alpha-res")
    assert lease is not None, "recovery barrier retains the lease"


# -- §3.3/§3.4 intent-transaction correctness ----------------------------------


def test_reclaim_reuses_stored_input_and_skips_derive(tmp_path):
    """The effect consumes the input durably recorded at intent; a changed
    environment must not redefine a reclaimed attempt."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    derive_calls: list[int] = []

    def counted_derive(*, request, prior):
        derive_calls.append(1)
        return {"value": request.desired_value}

    def capturing_execute(ctx):
        harness.world.apply_effect(
            ctx.inputs["value"], ctx.external_effect_id
        )
        harness.injector.check("apply_effect", "after_external_effect")
        return {"applied": ctx.external_effect_id}

    def lax_verify(ctx):
        return {}

    harness.registry = _registry_with(
        harness,
        apply_effect={
            "derive_input": counted_derive,
            "execute": capturing_execute,
            "verify": lax_verify,
        },
        verify_effect={"verify": lax_verify},
    )
    harness.enqueue(desired_value="v1", operation_id="op-intent")
    harness.injector.arm("apply_effect", "after_external_effect")
    with pytest.raises(SimulatedProcessDeath):
        _engine(harness, "worker-a").execute_one("op-intent")
    # One derive at enqueue (durable input recording) + one at the first
    # attempt; a reclaim must add none.
    calls_after_death = sum(derive_calls)

    # Environment drifted after the intent was durably recorded.
    harness.world.set_desired("v2")
    harness.clock.advance(120)
    outcome = _engine(harness, "worker-b").execute_one("op-intent")
    assert outcome.reason_code == "SUCCEEDED"

    assert sum(derive_calls) == calls_after_death == 2, (
        "derive_input runs once per new attempt and never on reclaim"
    )
    assert harness.world.read_active()["value"] == "v1", (
        "the reclaimed effect must consume the stored canonical input"
    )


def test_intent_identity_survives_reclaim(tmp_path):
    """Effect identity is stable across a reclaim."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-id")
    harness.injector.arm("apply_effect", "after_external_effect")
    with pytest.raises(SimulatedProcessDeath):
        _engine(harness, "worker-a").execute_one("op-id")
    harness.clock.advance(120)
    outcome = _engine(harness, "worker-b").execute_one("op-id")
    assert outcome.reason_code == "SUCCEEDED"
    rows = _rows(harness, "op-id")
    assert rows["apply_effect"].external_effect_id


# -- §3.5 per-step implementation versions -------------------------------------


def _fake_step(sequence: int = 1, version: int = 1) -> StepDefinition:
    return StepDefinition(
        step_key=f"step{sequence}",
        phase="prepare",
        sequence=sequence,
        derive_input=lambda *, request, prior: {},
        probe=lambda ctx: ProbeResult(RecoveryClass.ABSENT, "NONE"),
        execute=lambda ctx: {},
        verify=lambda ctx: {},
        implementation_version=version,
    )


def test_enqueue_records_each_declared_step_version(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    steps = tuple(
        dataclasses.replace(step, implementation_version=7)
        if step.step_key == "publish"
        else step
        for step in harness.workflow.steps
    )
    definition = WorkflowDefinition(
        operation_type=harness.workflow.operation_type,
        request_version=1,
        recovery_policy_version=1,
        decode_request=harness.workflow.decode_request,
        steps=steps,
        summary=harness.workflow.summary,
    )
    registry = WorkflowRegistry()
    registry.register(definition)
    harness.registry = registry.freeze()

    from bc250_llm_mode.operations.workflow import EnqueueService

    record = EnqueueService(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.operation_ids,
    ).enqueue(
        operation_type="MODEL_ACTIVATE",
        payload={"desired_value": "v1"},
        surface="test",
    )
    rows = _rows(harness, record.id)
    assert rows["capture_prior"].implementation_version == 1
    assert rows["publish"].implementation_version == 7


@pytest.mark.parametrize("bad_version", [0, -3])
def test_zero_or_negative_step_versions_are_rejected(bad_version):
    with pytest.raises(WorkflowRegistryError):
        WorkflowDefinition(
            operation_type=OperationType.MODEL_ACTIVATE,
            request_version=1,
            recovery_policy_version=1,
            decode_request=lambda payload: None,
            steps=(_fake_step(version=bad_version),),
            summary=lambda request: "x",
        )


# -- §3.6 bounded evidence and progress wiring ---------------------------------


def test_checkpoint_event_detail_is_a_plain_object(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-ev")
    outcome = _engine(harness, "worker-a").execute_one("op-ev")
    assert outcome.reason_code == "SUCCEEDED"

    with harness.units.begin() as conn:
        events = [
            e
            for e in EventRepository(conn, clock=FakeClock()).list_after(
                "op-ev", after_cursor=0
            )
            if e.code == "STEP_CHECKPOINTED"
        ]
    assert events
    for event in events:
        output = (event.detail or {}).get("output")
        assert output is None or isinstance(output, dict), (
            "checkpoint output must be a sanitized JSON object, "
            "not a nested JSON string"
        )


def test_pulse_updates_fenced_progress_and_heartbeat(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-pulse")

    def pulsing_execute(ctx):
        harness.clock.advance(5)
        ctx.pulse(phase="commit-config", current=1, total=8, unit="steps")
        harness.world.apply_effect(
            ctx.inputs["value"], ctx.external_effect_id
        )
        harness.injector.check("apply_effect", "after_external_effect")
        return {"applied": ctx.external_effect_id}

    harness.registry = _registry_with(
        harness, apply_effect={"execute": pulsing_execute}
    )
    harness.clock.advance(5)
    harness.injector.arm("apply_effect", "after_external_effect")
    with pytest.raises(SimulatedProcessDeath):
        _engine(harness, "worker-a").execute_one("op-pulse")

    with harness.units.begin() as conn:
        record = OperationRepository(conn, clock=FakeClock()).require("op-pulse")
        lease = LeaseRepository(conn, clock=FakeClock()).get("alpha-res")
    assert record.progress_phase == "commit-config"
    assert record.progress_current == 1
    assert lease is not None
    assert lease.heartbeat_at > lease.acquired_at, (
        "the pulse must renew the fenced lease heartbeat"
    )


def test_stale_worker_pulse_is_fenced(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-stale")
    captured: list = []

    def capturing_execute(ctx):
        captured.append(ctx.pulse)
        raise RuntimeError("boom")

    def absent_probe(ctx):
        return ProbeResult(RecoveryClass.ABSENT, "NO_EFFECT")

    harness.registry = _registry_with(
        harness,
        apply_effect={
            "execute": capturing_execute,
            "probe": absent_probe,
        },
    )
    outcome = _engine(harness, "worker-a").execute_one("op-stale")
    assert outcome.kind == "COMPLETED"  # failed safe, no mutation

    harness.clock.advance(120)
    stale_pulse = captured[0]
    with pytest.raises(LostLease):
        stale_pulse(phase="late", current=1)
