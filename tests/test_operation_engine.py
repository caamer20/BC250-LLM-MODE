"""Session 5B: the executor. The FIRST test is the mandatory
death-after-effect-before-checkpoint crash test (plan §9); success-path and
recovery-class coverage follow it."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationConflict, OperationState, StepState
from bc250_llm_mode.operations.repositories import (
    LeaseRepository,
    OperationRepository,
    StepRepository,
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


def test_death_after_effect_before_checkpoint_then_exact_recovery(tmp_path):
    """Plan §9 — the mandatory first acceptance test."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    created = harness.enqueue(desired_value="v1", operation_id="op-001")
    assert created.id == "op-001"

    # Arm the crash: apply_effect dies AFTER the external effect ran but
    # BEFORE the step checkpoint transaction.
    harness.injector.arm("apply_effect", "after_external_effect")

    engine_a = _engine(harness, "worker-a")
    with pytest.raises(SimulatedProcessDeath):
        engine_a.execute_one("op-001")

    # --- assert after simulated death ---------------------------------------
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        steps = StepRepository(conn, clock=FakeClock())
        leases = LeaseRepository(conn, clock=FakeClock())

        operation = ops.get("op-001")
        assert operation is not None
        assert operation.state in (
            OperationState.RUNNING,
            OperationState.PREPARING,
        ), "operation must remain non-terminal after process death"

        row = steps.get("op-001", "apply_effect")
        assert row is not None
        assert row.state == StepState.RUNNING
        assert row.attempts == 1
        assert row.checkpointed_at is None
        assert row.output_json is None
        assert row.external_effect_id

        for key in ("alpha-res", "beta-res"):
            lease = leases.get(key)
            assert lease is not None and lease.owner == "worker-a"

    active = harness.world.read_active()
    assert active["application_count"] == 1, "effect ran exactly once"
    assert active["value"] == "v1"
    assert len(active["effects"]) == 1

    # No terminal event was fabricated.
    with sqlite3.connect(str(harness.database)) as conn:
        terminal_codes = [
            r[0]
            for r in conn.execute(
                "SELECT code FROM operation_events WHERE code LIKE 'OPERATION_%'"
            )
        ]
    assert terminal_codes == ["OPERATION_QUEUED"]

    # --- second process -------------------------------------------------------
    harness.clock.advance(120)  # beyond worker-a's lease TTL
    engine_b = _engine(harness, "worker-b")

    outcome = engine_b.execute_one("op-001")
    assert outcome.kind == "COMPLETED", outcome.detail
    assert outcome.reason_code == "SUCCEEDED"

    # Effect count still exactly one (no re-execution).
    active = harness.world.read_active()
    assert active["application_count"] == 1
    assert len(active["effects"]) == 1

    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        steps = StepRepository(conn, clock=FakeClock())
        leases = LeaseRepository(conn, clock=FakeClock())

        final = ops.get("op-001")
        assert final.state == OperationState.SUCCEEDED

        recovered = steps.get("op-001", "apply_effect")
        assert recovered.attempts == 2, "recovery ownership recorded"
        assert recovered.state == StepState.VERIFIED
        assert recovered.checkpointed_at is not None

        from bc250_llm_mode.operations.model import OperationConflict

        with pytest.raises(OperationConflict):
            leases.assert_owned(
                "alpha-res",
                "op-001",
                owner="worker-a",
                lease_revision=1,
                now=str(FakeClock()()),
            )
        assert leases.get("alpha-res") is None, "releasable leases are gone"
        assert leases.get("beta-res") is None

    # Terminal SUCCEEDED event exists exactly once.
    with sqlite3.connect(str(harness.database)) as conn:
        succeeded = [
            r[0]
            for r in conn.execute(
                "SELECT code FROM operation_events"
                " WHERE code = 'OPERATION_SUCCEEDED'"
            )
        ]
    assert succeeded.count("OPERATION_SUCCEEDED") == 1

    # Re-running recovery after completion is a no-op.
    outcome_again = engine_b.execute_one("op-001")
    assert outcome_again.kind == "SKIPPED_TERMINAL"
    assert harness.world.read_active()["application_count"] == 1


# --- success / failure terminals ----------------------------------------------


def _rebuild_registry(harness, step_overrides: dict[str, dict]):
    """Rebuild the harness workflow with per-step overrides (test faults)."""
    import dataclasses

    from bc250_llm_mode.operations.workflow import (
        WorkflowDefinition,
        WorkflowRegistry,
    )

    steps = tuple(
        dataclasses.replace(step, **step_overrides.get(step.step_key, {}))
        if step.step_key in step_overrides
        else step
        for step in harness.workflow.steps
    )
    definition = WorkflowDefinition(
        operation_type=harness.workflow.operation_type,
        request_version=harness.workflow.request_version,
        recovery_policy_version=harness.workflow.recovery_policy_version,
        decode_request=harness.workflow.decode_request,
        steps=steps,
        summary=harness.workflow.summary,
        preflight=harness.workflow.preflight,
    )
    registry = WorkflowRegistry()
    registry.register(definition)
    return registry.freeze()


def test_full_success_without_crash(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-s")
    outcome = _engine(harness, "w1").execute_one("op-s")
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "SUCCEEDED"
    active = harness.world.read_active()
    assert active["application_count"] == 1
    assert world_publication(harness)
    # All leases released at terminal.
    with harness.units.begin() as conn:
        leases = LeaseRepository(conn, clock=FakeClock())
        assert leases.get("alpha-res") is None
        assert leases.get("beta-res") is None


def world_publication(harness) -> bool:
    return harness.world.publication_exists()


def test_resources_acquired_in_sorted_order_despite_declaration(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-order")
    engine = ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
        worker_id="w-order",
        on_lease_acquired=lambda key: harness.recorder.record(
            "acquire", resource=key
        ),
    )
    engine.execute_one("op-order")
    assert harness.recorder.acquisition_order() == ["alpha-res", "beta-res"]


def test_preflight_failure_is_failed_safe(tmp_path):
    from bc250_llm_mode.operations.workflow import (
        StepFailure,
        WorkflowDefinition,
        WorkflowRegistry,
    )

    harness = Harness(tmp_path)
    harness.set_desired("v1")

    def broken_preflight(request):
        raise StepFailure("PREFLIGHT_BLOCKED", "model artifact missing")

    definition = WorkflowDefinition(
        operation_type=harness.workflow.operation_type,
        request_version=1,
        recovery_policy_version=1,
        decode_request=harness.workflow.decode_request,
        steps=harness.workflow.steps,
        summary=harness.workflow.summary,
        preflight=broken_preflight,
    )
    reg = WorkflowRegistry()
    reg.register(definition)
    harness.registry = reg.freeze()

    harness.enqueue(desired_value="v1", operation_id="op-pf")
    outcome = _engine(harness, "w1").execute_one("op-pf")
    assert outcome.reason_code == "FAILED_SAFE"
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        assert ops.get("op-pf").state == OperationState.FAILED_SAFE
    assert harness.world.read_active()["application_count"] == 0


def test_effect_exception_with_probe_absent_is_failed_safe(tmp_path):
    from bc250_llm_mode.operations.workflow import StepFailure

    harness = Harness(tmp_path)
    harness.set_desired("v1")
    boom = StepFailure("EFFECT_REFUSED", "adapter refused", mutation_possible=False)

    def refusing_execute(ctx):
        raise boom

    harness.registry = _rebuild_registry(
        harness, {"apply_effect": {"execute": refusing_execute}}
    )
    harness.enqueue(desired_value="v1", operation_id="op-safe")
    outcome = _engine(harness, "w1").execute_one("op-safe")
    assert outcome.reason_code == "FAILED_SAFE"
    assert harness.world.read_active()["application_count"] == 0

def test_failure_classification_probe_crash_fails_operation_safely(tmp_path):
    """P0 finding (worker entry gate): the classification probe re-observes
    reality and can raise for exactly the condition that failed the step.
    That exception must not escape ``execute_one``; the operation ends
    FAILED_SAFE and previously verified steps still drive compensation."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")

    def exploding_probe(ctx):
        raise RuntimeError("probe observes the same broken condition")

    def exploding_execute(ctx):
        raise RuntimeError("step condition broken")

    harness.registry = _rebuild_registry(
        harness,
        {
            "capture_prior": {
                "probe": exploding_probe,
                "execute": exploding_execute,
            }
        },
    )
    harness.enqueue(desired_value="v1", operation_id="op-probe-crash")

    outcome = _engine(harness, "w1").execute_one("op-probe-crash")

    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "FAILED_SAFE"
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        record = ops.get("op-probe-crash")
        assert record.state == OperationState.FAILED_SAFE
        assert record.error_code == "STEP_FAILED_SAFE"


def test_probe_crash_after_verified_effects_still_compensates(tmp_path):
    """An unreadable classification probe proves nothing: a checkpointed,
    reversible mutation whose probe crashes is still compensated (or
    escalated), never silently reported failed-safe while applied."""
    harness = Harness(tmp_path)
    harness.set_desired("v1")

    def exploding_probe(ctx):
        raise RuntimeError("verification target unreadable")

    def breaking_verify(ctx):
        raise RuntimeError("verify exploded after the effect landed")

    harness.registry = _rebuild_registry(
        harness,
        {
            "apply_effect": {
                "probe": exploding_probe,
                "verify": breaking_verify,
            }
        },
    )
    harness.enqueue(desired_value="v1", operation_id="op-late-probe")

    outcome = _engine(harness, "w1").execute_one("op-late-probe")

    assert outcome.reason_code == "FAILED_ROLLED_BACK"
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        state = ops.get("op-late-probe").state
    assert state is OperationState.FAILED_ROLLED_BACK
    # The applied effect was actually reversed by compensation.
    assert harness.world.read_active()["compensated"] is True
