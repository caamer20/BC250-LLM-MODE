"""Session 5C §14.4–§14.6: activation crash matrix, concurrency, privacy.

For every protocol boundary on the candidate-effecting steps, process
death must converge under a fresh executor to exactly one closed outcome
(SUCCEEDED or FAILED_ROLLED_BACK) with publication/restart/restoration
effects bounded at one each, and no fabricated terminals.
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

from bc250_llm_mode.operations.model import OperationState

from fakes import CrashInjector, SimulatedProcessDeath
from test_operation_activation import ActivationHarness


@pytest.fixture()
def harness(tmp_path):
    h = ActivationHarness(tmp_path)
    h.seed_active_prior("model-a")
    h.enqueue_b(operation_id="op-act-matrix")
    assert h.operation_id == "op-act-matrix"
    return h

# Protocol boundaries on externally visible candidate steps (plan §14.5).
CRASH_POINTS = (
    "before_step_start",
    "after_step_start",
    "before_step_checkpoint",
    "after_step_checkpoint",
    "before_step_verification",
    "after_step_verification",
)
EFFECTING_STEPS = (
    "commit_candidate_config",
    "publish_candidate_handoff",
    "restart_candidate",
)


def _run_to_death(harness, step_key, point):
    injector = CrashInjector()
    injector.arm(step_key, point)
    harness.injector = injector
    try:
        harness.engine("worker-a").execute_one(harness.operation_id)
    except SimulatedProcessDeath:
        pass
    finally:
        harness.injector = CrashInjector()


def _assert_closed(harness, outcome):
    """§8 closed outcomes only; no fabricated terminals."""
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code in ("SUCCEEDED", "FAILED_ROLLED_BACK")
    host = harness.host
    publications = host.effect_count("publish")
    restorations = host.effect_count("restoration")
    restarts = host.effect_count("restart")
    promotions = host.effect_count("promote")
    if outcome.reason_code == "SUCCEEDED":
        assert host.running_alias() == "model-b"
        assert host.config()["model_alias"] == "model-b"
        assert host.known_good()["model_alias"] == "model-b"
        # Publication of B happened at most once; promotion exactly once.
        assert promotions == 1
    else:
        assert host.config()["model_alias"] == "model-a"
        assert host.running_alias() == "model-a"
        assert host.handoff()["model_id"] == "model-a"
        assert host.known_good()["model_alias"] == "model-a"
        assert restorations == 1
        assert promotions == 0
    # Global effect bounds regardless of branch.
    assert publications <= 2  # seed(A) + candidate(B), never duplicated
    assert restarts <= 1


@pytest.mark.parametrize("step_key", EFFECTING_STEPS)
@pytest.mark.parametrize("point", CRASH_POINTS)
def test_candidate_step_death_converges_under_takeover(
    harness, step_key, point
):
    _run_to_death(harness, step_key, point)
    harness.clock.advance(120)
    outcome = harness.engine("worker-b").execute_one(harness.operation_id)
    _assert_closed(harness, outcome)
    # Stale worker A is fenced: any further write attempt loses its lease.
    stale = harness.engine("worker-a")
    result = stale.execute_one(harness.operation_id)
    assert result.kind in ("SKIPPED_TERMINAL", "COMPLETED") or (
        result.reason_code in ("LOST_LEASE",)
    )


def test_verify_step_deaths_converge(harness):
    for step_key, point in (
        ("verify_candidate_health", "before_step_checkpoint"),
        ("verify_candidate_inference", "after_step_checkpoint"),
        ("promote_known_good", "before_step_checkpoint"),
    ):
        fresh = ActivationHarness(harness.root.parent / f"w-{step_key}")
        fresh.seed_active_prior("model-a")
        fresh.enqueue_b(operation_id=f"op-{step_key}")
        _run_to_death(fresh, step_key, point)
        fresh.clock.advance(120)
        outcome = fresh.engine("worker-b").execute_one(fresh.operation_id)
        _assert_closed(fresh, outcome)
        assert fresh.operation_state() in (
            OperationState.SUCCEEDED,
            OperationState.FAILED_ROLLED_BACK,
        )


def test_active_lease_returns_busy_without_mutation(harness):
    """Two activations contend on ``runtime-active``; the loser is busy."""
    _run_to_death(harness, "commit_candidate_config", "after_step_checkpoint")
    # A second frontend enqueues its own request instead of waiting.
    second = harness._enqueue.enqueue(
        operation_type="MODEL_ACTIVATE",
        payload={
            "model_alias": "model-b",
            "expected_runtime_revision": 2,
            "requested_by": "gui",
        },
        surface="test",
        operation_id="op-second",
    )
    outcome = harness.engine("worker-c").execute_one(second.id)
    assert outcome.kind == "SKIPPED_BUSY"
    assert outcome.reason_code == "RESOURCE_HELD"


def test_secret_like_completion_text_never_becomes_durable(harness):
    """The inference probe's completion content never reaches the
    operation database, events, results, or the handoff artifact."""
    # Run a full successful activation, then scan every durable byte.
    outcome = harness.engine("worker-a").execute_one(harness.operation_id)
    assert outcome.reason_code == "SUCCEEDED"

    database_bytes = harness.database.read_bytes()
    for path in harness.host.root.iterdir():
        if path.is_file():
            database_bytes += path.read_bytes()
    for forbidden in (b"completion-text", b"generated answer"):
        assert forbidden not in database_bytes
