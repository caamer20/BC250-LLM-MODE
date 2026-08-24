"""Session 5B: durable cancellation, compensation, and failure terminals."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState, StepState
from bc250_llm_mode.operations.repositories import (
    LeaseRepository,
    OperationRepository,
    StepRepository,
)
from bc250_llm_mode.operations.workflow import (
    WorkflowDefinition,
    WorkflowRegistry,
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
    )


def _registry_with(harness, **step_overrides):
    steps = tuple(
        (
            replace(step, **step_overrides[step.step_key])
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


def test_cancellation_before_any_effect_cancels_directly(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-c1")
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        ops.request_cancel("op-c1")

    outcome = _engine(harness, "w1").execute_one("op-c1")
    assert outcome.reason_code == "CANCELLED"
    assert harness.world.read_active()["application_count"] == 0
    assert not harness.world.publication_exists()


def test_cancellation_after_effect_compensates_then_cancels(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-c2")

    # Die after apply_effect's external effect; then cancel durably.
    harness.injector.arm("apply_effect", "after_external_effect")
    with pytest.raises(SimulatedProcessDeath):
        _engine(harness, "w-a").execute_one("op-c2")
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        ops.request_cancel("op-c2")

    # Recovery honors the accepted cancellation: compensate then CANCELLED.
    harness.clock.advance(120)
    outcome = _engine(harness, "w-b").execute_one("op-c2")
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "CANCELLED"
    active = harness.world.read_active()
    assert active["application_count"] == 1  # never repeated
    assert active["compensated"] is True
    assert not harness.world.publication_exists()

    with harness.units.begin() as conn:
        steps = StepRepository(conn, clock=FakeClock())
        rows = {s.step_key: s.state for s in steps.list("op-c2")}
    assert rows["apply_effect"] == StepState.COMPENSATED


def test_verify_failure_after_mutation_rolls_back_verified(tmp_path):
    """Verification failure after a mutation rolls back; the prior state is
    restored and verified → FAILED_ROLLED_BACK."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")

    def failing_verify(ctx):
        raise AssertionError("postcondition drifted")

    harness.registry = _registry_with(
        harness, verify_effect={"verify": failing_verify}
    )
    harness.enqueue(desired_value="v1", operation_id="op-rb")
    outcome = _engine(harness, "w1").execute_one("op-rb")
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "FAILED_ROLLED_BACK"
    active = harness.world.read_active()
    assert active["compensated"] is True


def test_compensation_failure_enters_recovery_required(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")

    def broken_compensate(ctx):
        raise OSError("undo failed")

    def drifting_verify(ctx):
        raise AssertionError("postcondition drifted")

    harness.registry = _registry_with(
        harness,
        apply_effect={
            "verify": drifting_verify,
            "compensate": broken_compensate,
        },
    )
    harness.enqueue(desired_value="v1", operation_id="op-rec")
    outcome = _engine(harness, "w1").execute_one("op-rec")
    assert outcome.kind == "RECOVERY_REQUIRED_OUTCOME"

    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        leases = LeaseRepository(conn, clock=FakeClock())
        record = ops.get("op-rec")
        assert record.state == OperationState.RECOVERY_REQUIRED
        # Barrier lease rows are RETAINED.
        assert leases.get("alpha-res") is not None
        assert leases.get("beta-res") is not None


def test_repeated_cancellation_produces_single_terminal(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-dup")
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        ops.request_cancel("op-dup")
        ops.request_cancel("op-dup")  # idempotent at the repository layer

    _engine(harness, "w1").execute_one("op-dup")

    with sqlite3.connect(str(harness.database)) as conn:
        terminals = [
            r[0]
            for r in conn.execute(
                "SELECT code FROM operation_events"
                " WHERE code = 'OPERATION_CANCELLED'"
            )
        ]
    assert terminals == ["OPERATION_CANCELLED"]