"""Session 5C: durable MODEL_ACTIVATE v1 workflow tests (plan §7/§8/§14.3).

The MANDATORY first red test is ``test_handoff_death_takes_over_and_restores``
(§8): crash after publishing the candidate handoff but before checkpoint;
a fresh executor must inspect reality and converge to exactly one of the
closed outcomes without duplicating publication, restart, or restoration.
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
from bc250_llm_mode.operations.activation import (
    ModelActivateRequestV1,
    build_activation_workflow,
)
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState, StepState
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

from activation_world import FakeActivationHost

from fakes import CrashInjector, FakeClock, SequenceIds, SimulatedProcessDeath


class ActivationHarness:
    """Database + fake activation host + registry harness (plan §8)."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "profile"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()
        world_root = tmp_path / "activation-world"
        world_root.mkdir(parents=True, exist_ok=True)
        self.host = FakeActivationHost(root=world_root)
        # Two fake managed artifacts.
        self.host.seed_model("model-a")
        self.host.seed_model("model-b", content="gguf-b-bytes")
        self.operation_ids = SequenceIds("op")
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()

        registry = WorkflowRegistry()
        registry.register(build_activation_workflow(self.host))
        self.registry = registry.freeze()
        self._enqueue = EnqueueService(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.operation_ids,
        )
        self.operation_id: str | None = None

    def seed_active_prior(self, alias: str = "model-a") -> None:
        self.host.seed_active_prior(alias)

    def enqueue_b(self, *, operation_id: str | None = None, **overrides):
        payload = {
            "model_alias": "model-b",
            "expected_runtime_revision": 1,
            "requested_by": "cli",
        }
        payload.update(overrides)
        record = self._enqueue.enqueue(
            operation_type="MODEL_ACTIVATE",
            payload=payload,
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
            crash_hook=lambda step_key, pnt: self.injector.check(step_key, pnt),
        )

    def run_to_death(self, step_key: str, point: str) -> None:
        injector = CrashInjector()
        injector.arm(step_key, point)
        self.injector = injector
        try:
            self.engine("worker-a").execute_one(self.operation_id)
        except SimulatedProcessDeath:
            pass
        finally:
            self.injector = CrashInjector()

    def operation_state(self) -> OperationState:
        with self.units.begin() as conn:
            ops = OperationRepository(conn, clock=FakeClock())
            return ops.require(self.operation_id).state


@pytest.fixture()
def harness(tmp_path):
    h = ActivationHarness(tmp_path)
    h.seed_active_prior("model-a")
    h.enqueue_b(operation_id="op-act-1")
    assert h.operation_id == "op-act-1"
    return h


def test_workflow_shape_is_exact():
    definition = build_activation_workflow(object())  # type: ignore[arg-type]
    assert definition.operation_type.value == "MODEL_ACTIVATE"
    assert definition.request_version == 1
    assert definition.recovery_policy_version == 1
    keys = [step.step_key for step in definition.steps]
    assert keys == [
        "resolve_candidate",
        "capture_prior",
        "commit_candidate_config",
        "publish_candidate_handoff",
        "restart_candidate",
        "verify_candidate_health",
        "verify_candidate_inference",
        "promote_known_good",
    ]
    for step in definition.steps:
        assert step.implementation_version == 1
    visible = [s.step_key for s in definition.steps if s.externally_visible]
    assert visible == [
        "commit_candidate_config",
        "publish_candidate_handoff",
        "restart_candidate",
        "promote_known_good",
    ]
    critical = [s.step_key for s in definition.steps if s.critical]
    assert critical == keys[2:]


def test_request_decoder_is_closed():
    from bc250_llm_mode.operations.activation import decode_activation_request
    from bc250_llm_mode.operations.model import OperationValidationError

    request = decode_activation_request(
        {"model_alias": "model-a", "context_per_slot": 8192}
    )
    assert isinstance(request, ModelActivateRequestV1)
    with pytest.raises(OperationValidationError):
        decode_activation_request({"model_alias": "a", "path": "/etc"})
    with pytest.raises(OperationValidationError):
        decode_activation_request({"model_alias": "a", "context_per_slot": 100})
    with pytest.raises(OperationValidationError):
        decode_activation_request({"model_alias": "a", "parallel_slots": 9})
    with pytest.raises(OperationValidationError):
        decode_activation_request({"model_alias": "a", "allow_preview": True})


# -- MANDATORY first red test (plan §8) -------------------------------------------


def test_handoff_death_takes_over_and_restores(harness):
    """Death after the atomic handoff replacement, before checkpoint.

    The prior (model A) is still exact and healthy, so worker B must
    classify the interrupted publication as REVERTIBLE, run the aggregate
    restoration exactly once, and finish FAILED_ROLLED_BACK — never
    duplicating a publication, restart, or restoration.
    """
    harness.run_to_death("publish_candidate_handoff", "before_step_checkpoint")
    # Death left durable candidate state behind.
    assert harness.operation_state() is OperationState.COMMITTING
    assert harness.host.handoff()["model_id"] == "model-b"
    publications_before = harness.host.effect_count("publish")

    # Takeover: advance only the injected clock past TTL; new lease gen.
    harness.clock.advance(120)
    outcome = harness.engine("worker-b").execute_one(harness.operation_id)

    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "FAILED_ROLLED_BACK"
    host = harness.host
    # Config restored to prior content as a NEW revision (lineage kept).
    desired = host.desired()
    assert desired["config"]["model_alias"] == "model-a"
    assert desired["revision"] == 3  # 1 (prior) -> 2 (candidate) -> 3 (restore)
    assert desired["restored_content_of_revision"] == 1
    # Full handoff payload matches A exactly.
    assert host.handoff()["model_id"] == "model-a"
    # Service: still model A, healthy, inference-capable.
    assert host.running_alias() == "model-a"
    server = host.server()
    assert server["health_ok"] and server["infer_ok"]
    # Known-good row is A's.
    assert host.known_good()["model_alias"] == "model-a"
    # Effect counts: exactly ONE publication of B overall (the interrupted
    # worker's; takeover neither re-published nor duplicated); restoration
    # ran exactly once; no restart effect at all (A was never stopped).
    assert host.effect_count("publish") == publications_before
    assert host.effect_count("restoration") == 1
    assert host.effect_count("restart") == 0
    with harness.units.begin() as conn:
        from bc250_llm_mode.operations.repositories import StepRepository

        steps = StepRepository(conn, clock=FakeClock())
        rows = {s.step_key: s for s in steps.list(harness.operation_id)}
        assert rows["promote_known_good"].state is StepState.PENDING
        ops = OperationRepository(conn, clock=FakeClock())
        record = ops.require(harness.operation_id)
        assert record.state is OperationState.FAILED_ROLLED_BACK


def test_handoff_death_candidate_complete_succeeds_without_restart(harness):
    """Same crash point, but reality raced ahead: the runtime already serves
    the exact candidate. Worker B checkpoints from probe evidence and
    finishes SUCCEEDED; restart count stays at exactly one overall."""
    harness.run_to_death("publish_candidate_handoff", "before_step_checkpoint")
    # Between death and takeover the runtime came up on the exact candidate.
    harness.host.set_server_running("model-b")
    harness.host._record_effect("race-restart", "restart")
    harness.clock.advance(120)
    outcome = harness.engine("worker-b").execute_one(harness.operation_id)

    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "SUCCEEDED"
    host = harness.host
    assert host.running_alias() == "model-b"
    assert host.config()["model_alias"] == "model-b"
    assert host.known_good()["model_alias"] == "model-b"
    # Exactly one restart effect overall (the simulated race), one candidate
    # publish, one promotion, and no restoration.
    assert host.effect_count("restart") == 1
    assert host.effect_count("publish") == 2  # seed(A) + candidate(B)
    assert host.effect_count("promote") == 1
    assert host.effect_count("restoration") == 0


# -- happy paths --------------------------------------------------------------------


def test_happy_path_model_a_to_b(harness):
    outcome = harness.engine("worker-a").execute_one(harness.operation_id)
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "SUCCEEDED"

    host = harness.host
    assert host.config()["model_alias"] == "model-b"
    assert host.running_alias() == "model-b"
    assert host.known_good()["model_alias"] == "model-b"
    assert host.effect_count("config_commit") == 1
    assert host.effect_count("restart") == 1
    assert host.effect_count("promote") == 1

    with harness.units.begin() as conn:
        from bc250_llm_mode.operations.repositories import StepRepository

        steps = StepRepository(conn, clock=FakeClock())
        rows = {s.step_key: s.state for s in steps.list(harness.operation_id)}
        assert all(state is StepState.VERIFIED for state in rows.values())
        ops = OperationRepository(conn, clock=FakeClock())
        assert (
            ops.require(harness.operation_id).state is OperationState.SUCCEEDED
        )


def test_stopped_prior_initial_activation_succeeds(tmp_path):
    h = ActivationHarness(tmp_path)
    h.host.seed_stopped_prior("model-a")
    h.enqueue_b(operation_id="op-act-stop")
    outcome = h.engine("worker-a").execute_one(h.operation_id)
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "SUCCEEDED"
    assert h.host.running_alias() == "model-b"
