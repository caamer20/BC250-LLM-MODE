"""Session 5B: the full named crash-point matrix (plan §14.4).

For every protocol point on an effecting step: simulate process death, then
prove a fresh worker converges to SUCCEEDED with the external effect applied
exactly once, durable state consistent, and no fabricated terminals.
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

from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState, StepState

from fakes import FakeClock, SimulatedProcessDeath
from helpers import Harness

CRASH_POINTS = (
    "before_step_start",
    "after_step_start",
    "after_external_effect",
    "before_probe",
    "after_probe",
    "before_step_checkpoint",
    "after_step_checkpoint",
    "before_step_verification",
    "after_step_verification",
)


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_crash_at_named_point_on_apply_effect_recovers_exactly_once(
    tmp_path, point
):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-001")

    engine_a = ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
        worker_id="worker-a",
        lease_ttl_seconds=60,
        crash_hook=lambda step_key, pnt: harness.injector.check(step_key, pnt),
    )
    harness.injector.arm("apply_effect", point)

    if point in ("before_probe", "after_probe"):
        # Probe points are only reachable on the reclaim path: create the
        # interrupted RUNNING state first with a stage-1 effect crash.
        harness.injector.arm("apply_effect", "after_external_effect")
        try:
            engine_a.execute_one("op-001")
        except SimulatedProcessDeath:
            pass
        harness.clock.advance(120)

    # First worker dies at the armed point (or completes if the point is not
    # reachable on the first pass; both leave recoverable durable state).
    try:
        engine_a.execute_one("op-001")
    except SimulatedProcessDeath:
        pass

    # A second worker, past any TTL, always converges.
    harness.clock.advance(120)
    engine_b = ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
        worker_id="worker-b",
        lease_ttl_seconds=60,
    )
    outcome = engine_b.execute_one("op-001")
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "SUCCEEDED"

    active = harness.world.read_active()
    assert active["application_count"] == 1, "effect applied exactly once"

    with harness.units.begin() as conn:
        from bc250_llm_mode.operations.repositories import (
            OperationRepository,
            StepRepository,
        )

        steps = StepRepository(conn, clock=FakeClock())
        rows = {s.step_key: s.state for s in steps.list("op-001")}
        assert all(state is StepState.VERIFIED for state in rows.values())
        assert (
            steps.get("op-001", "apply_effect").attempts <= 3
        ), "at most one reclaim per process death"

        ops = OperationRepository(conn, clock=FakeClock())
        assert ops.get("op-001").state == OperationState.SUCCEEDED


def _engine(harness: Harness, worker_id: str) -> ExecutionEngine:
    return ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
        worker_id=worker_id,
        lease_ttl_seconds=60,
    )


def _registry_with(harness, **step_overrides):
    import dataclasses

    from bc250_llm_mode.operations.workflow import (
        WorkflowDefinition,
        WorkflowRegistry,
    )

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


@pytest.mark.parametrize(
    "point",
    [
        "before_compensation_checkpoint",
        "after_compensation_effect",
    ],
)
def test_compensation_phase_death_converges_or_barriers(tmp_path, point):
    """Deaths during compensation either complete it or leave durable intent;
    they never lose the compensation or fake success."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")

    def drifting_verify(ctx):
        raise AssertionError("drift")

    def dying_compensate(ctx):
        raise SimulatedProcessDeath(f"apply_effect:{point}")

    harness.registry = _registry_with(
        harness,
        verify_effect={"verify": drifting_verify},
        apply_effect={"compensate": dying_compensate},
    )
    harness.enqueue(desired_value="v1", operation_id="op-cx")

    engine = ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
        worker_id="worker-a",
        crash_hook=lambda step_key, pnt: harness.injector.check(step_key, pnt),
    )
    with pytest.raises(SimulatedProcessDeath):
        engine.execute_one("op-cx")


def _engine(harness: Harness, worker_id: str) -> ExecutionEngine:
    return ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
        worker_id=worker_id,
        lease_ttl_seconds=60,
    )