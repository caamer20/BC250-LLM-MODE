"""U1.2 §16.1: pure workflow behavior through the deterministic fake world.

Happy/failure/no-op/rollback/cancellation coverage for RUNTIME_UPDATE v1
and RUNTIME_ROLLBACK v1 without any host integration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "bc250_llm_mode.operations.runtime_lifecycle",
    reason="U1.2 Commit 4: runtime workflows not yet defined",
)

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.repositories import (
    OperationRepository,
    StepRepository,
)
from bc250_llm_mode.operations.runtime_lifecycle import (
    RuntimeRollbackRequestV1,
    build_runtime_rollback_workflow,
    build_runtime_update_workflow,
)
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from bc250_llm_mode.runtime_builds import (
    RuntimeBuildRepository,
    RuntimeComponentRepository,
    RuntimeVerificationRepository,
)
from fakes import FakeClock, SequenceIds
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


class Harness:
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
        self.prior_build_id: str | None = None
        self.op_ids = SequenceIds("op")
        self.effect_ids = SequenceIds("eff")
        registry = WorkflowRegistry()
        registry.register(build_runtime_update_workflow(self.host))
        registry.register(build_runtime_rollback_workflow(self.host))
        self.registry = registry.freeze()
        self.enqueue = EnqueueService(
            self.units, self.registry,
            clock=self.clock.now, uuid_factory=self.op_ids,
        )
        self.operation_id: str | None = None

    def seed_prior(self):
        self.prior_build_id = self.host.seed_promoted_runtime("prior")

    def enqueue_update(self, **payload):
        record = self.enqueue.enqueue(
            operation_type="RUNTIME_UPDATE",
            payload={"requested_by": "cli", **payload},
            surface="test",
        )
        self.operation_id = record.id
        return record.id

    def enqueue_rollback(self, target: str | None = None, **payload):
        component = self._component()
        record = self.enqueue.enqueue(
            operation_type="RUNTIME_ROLLBACK",
            payload={
                "requested_by": "cli",
                "expected_active_build_id": (
                    payload.pop("expected_active_build_id", None)
                    or (component or {}).get("promoted_build_id")
                    or ""
                ),
                "target_build_id": payload.pop("target_build_id", None)
                or (component or {}).get("rollback_build_id")
                or "",
                **payload,
            },
            surface="test",
        )
        self.operation_id = record.id
        return record.id

    def engine(self, worker_id="w1"):
        return ExecutionEngine(
            self.units, self.registry,
            clock=self.clock.now, uuid_factory=self.effect_ids,
            worker_id=worker_id, lease_ttl_seconds=60,
        )

    def run(self, worker_id="w1"):
        return self.engine(worker_id).execute_one(self.operation_id)

    def run_to_terminal(self, worker_id="w1", attempts=8):
        outcome = None
        for _ in range(attempts):
            outcome = self.run(worker_id)
            if self.state() in TERMINAL:
                return outcome
        return outcome

    def state(self) -> OperationState:
        with self.units.begin() as conn:
            return OperationRepository(conn).require(self.operation_id).state

    def row(self):
        with self.units.begin() as conn:
            return OperationRepository(conn).require(self.operation_id)

    def outputs(self) -> dict:
        with self.units.begin() as conn:
            rows = {
                s.step_key: s.output_json
                for s in StepRepository(conn).list(self.operation_id)
            }
        return {k: json.loads(v) for k, v in rows.items() if v}

    def _component(self):
        with self.units.read() as conn:
            return RuntimeComponentRepository(conn).current()


@pytest.fixture()
def h(tmp_path):
    harness = Harness(tmp_path)
    harness.seed_prior()
    return harness


# -- happy path ------------------------------------------------------------------


def test_happy_update_builds_exchanges_verifies_and_promotes(h):
    operation_id = h.enqueue_update()

    outcome = h.run_to_terminal()

    assert outcome.kind == "COMPLETED"
    row = h.row()
    assert row.state is OperationState.SUCCEEDED
    assert row.result_code == "RUNTIME_PROMOTED"
    assert h.host.exchange_count() == 1
    new_active = h.host.active_build_id()
    assert new_active != h.prior_build_id
    assert h.host.retained_prior_build_id() == h.prior_build_id
    # Immutable build record + SMOKE verification exist.
    with h.units.read() as conn:
        builds = RuntimeBuildRepository(conn)
        record = builds.require(new_active)
        assert record["provenance_class"] == "IMMUTABLE_SOURCE"
        verifications = RuntimeVerificationRepository(conn)
        kinds = [v["kind"] for v in verifications.list_for_build(new_active)]
    assert "SMOKE" in kinds
    # Handoff v2 carries the component identity chain.
    handoff = json.loads(h.host.handoff_path.read_text())
    assert handoff["schema_version"] == 2
    assert handoff["runtime_component_id"] == new_active
    # Durable lineage advanced with the prior as rollback target.
    component = h._component()
    assert component["promoted_build_id"] == new_active
    assert component["rollback_build_id"] == h.prior_build_id


def test_already_active_is_verified_noop_without_rebuild(h):
    """Re-running the same pinned target returns RUNTIME_ALREADY_ACTIVE."""
    operation_id = h.enqueue_update()
    first = h.run_to_terminal()
    assert h.row().result_code == "RUNTIME_PROMOTED"
    exchanges_after_first = h.host.exchange_count()
    invocations_after_first = int(h.host.service()["invocations"])

    second_op = h.enqueue_update()
    second = h.run_to_terminal(worker_id="w2")

    assert h.row().result_code == "RUNTIME_ALREADY_ACTIVE"
    assert h.row().state is OperationState.SUCCEEDED
    # No second swap/restart/promotion happened.
    assert h.host.exchange_count() == exchanges_after_first
    assert int(h.host.service()["invocations"]) == invocations_after_first


def test_first_install_publishes_and_promotes_without_exchange(h, tmp_path):
    fresh = Harness(tmp_path / "fresh")  # no seeded prior runtime
    operation_id = fresh.enqueue_update()

    outcome = fresh.run_to_terminal()

    assert outcome.kind == "COMPLETED"
    assert fresh.row().result_code == "RUNTIME_PROMOTED"
    assert fresh.host.active_root.exists()
    assert fresh.host.retained_prior_build_id() is None


def test_tag_moved_after_resolution_fails_safe_before_mutation(h):
    h.enqueue_update()
    # The ref MOVES between the durable resolution and its verification:
    # every observation after the first one sees a different commit.
    original_observe = h.host.observe_source_resolution
    moved = {"done": False}

    def observe_and_move(request, evidence):
        if not moved["done"]:
            moved["done"] = True
            h.host.refs[evidence.requested_ref] = "c" * 40
        return original_observe(request, evidence)

    h.host.observe_source_resolution = observe_and_move  # type: ignore
    outcome = h.run_to_terminal()

    row = h.row()
    assert row.state is OperationState.FAILED_SAFE
    assert json.loads(row.error_detail or "{}").get("code") in (
        "SOURCE_REF_MOVED",
        "STEP_FAILED_SAFE",
    )
    # Nothing was fetched/built/swapped.
    assert h.host.exchange_count() == 0
    assert h.host.active_build_id() == h.prior_build_id


# -- rollback --------------------------------------------------------------------


def _update_first(h):
    h.enqueue_update()
    outcome = h.run_to_terminal()
    assert outcome.kind == "COMPLETED"
    component = h._component()
    return component["promoted_build_id"], component["rollback_build_id"]


def test_happy_rollback_restores_prior_with_live_verification(h):
    new_active, prior = _update_first(h)

    operation_id = h.enqueue_rollback()
    outcome = h.run_to_terminal()

    assert outcome.kind == "COMPLETED"
    row = h.row()
    assert row.state is OperationState.SUCCEEDED
    assert row.result_code == "RUNTIME_RESTORED"
    assert h.host.active_build_id() == prior
    # Toggle-safe lineage: the updated build became the rollback target.
    component = h._component()
    assert component["promoted_build_id"] == prior
    assert component["rollback_build_id"] == new_active


def test_rollback_toggle_undo_without_rebuild(h):
    new_active, prior = _update_first(h)
    h.enqueue_rollback()
    h.run_to_terminal()

    # Rolling back AGAIN restores the updated build (undo of the undo).
    operation_id = h.enqueue_rollback()
    outcome = h.run_to_terminal(worker_id="w3")

    assert outcome.kind == "COMPLETED"
    assert h.row().result_code == "RUNTIME_RESTORED"
    component = h._component()
    assert component["promoted_build_id"] == new_active
    assert component["rollback_build_id"] == prior
    # No rebuild was needed for either toggle: exactly three managed
    # exchanges total (update, rollback, undo) over the same two trees.
    assert h.host.exchange_count() == 3


def test_rollback_refuses_when_expected_active_conflicts(h):
    new_active, prior = _update_first(h)
    stale = h.enqueue_enqueue_expect_conflict = h.enqueue.enqueue(
        operation_type="RUNTIME_ROLLBACK",
        payload={
            "requested_by": "cli",
            "expected_active_build_id": h.prior_build_id or "",
            "target_build_id": prior or "",
        },
        surface="test",
    )
    h.operation_id = stale.id
    outcome = h.run_to_terminal()

    assert h.row().state is OperationState.FAILED_SAFE
    assert h.host.active_build_id() == new_active  # untouched


def test_rollback_target_missing_fails_safe(h):
    _update_first(h)
    component = h._component()
    # Point at a nonexistent build id.
    bogus = "llamacpp:sha256:" + "9" * 64
    record = h.enqueue.enqueue(
        operation_type="RUNTIME_ROLLBACK",
        payload={
            "requested_by": "cli",
            "expected_active_build_id": component["promoted_build_id"],
            "target_build_id": bogus,
        },
        surface="test",
    )
    h.operation_id = record.id

    h.run_to_terminal()

    assert h.row().state is OperationState.FAILED_SAFE
    assert h.host.active_build_id() != bogus


# -- failure handling ---------------------------------------------------------------


def test_restart_failure_after_exchange_rolls_back_to_proven_prior(h):
    h.enqueue_update()

    # Fail the restart AFTER the exchange landed.
    orig_restart = h.host.restart_for_runtime_change

    def fail_once(*args, **kwargs):
        raise RuntimeError("systemctl failed")

    calls = {"n": 0}

    def failing(*args, **kwargs):
        calls["n"] += 1
        return fail_once(*args, **kwargs)

    h.host.restart_for_runtime_change = failing  # type: ignore[method-assign]
    h.clock.advance(61)  # not needed; single run
    outcome = h.run_to_terminal()

    row = h.row()
    # Restoration must be proven: exchange reversed, service restored.
    assert h.host.active_build_id() == h.prior_build_id
    assert h.host.service()["build_id"] == h.prior_build_id
    component = h._component()
    assert component["promoted_build_id"] == h.prior_build_id
    assert row.state in (
        OperationState.FAILED_ROLLED_BACK,
        OperationState.RECOVERY_REQUIRED,
    )


def test_unsupported_atomic_exchange_fails_safely_before_mutation(h):
    h.host.exchange_unsupported = True
    h.enqueue_update()

    outcome = h.run_to_terminal()

    assert h.row().state is OperationState.FAILED_SAFE
    assert h.host.active_build_id() == h.prior_build_id
    assert h.host.exchange_count() == 0


def test_insufficient_disk_fails_safely(h):
    h.host.disk_free_bytes = 1024
    h.enqueue_update()

    h.run_to_terminal()

    assert h.row().state is OperationState.FAILED_SAFE
    outputs = h.outputs()
    preflight = outputs.get("preflight_build") or {}
    assert preflight.get("disk_ok") is False or preflight.get("skipped") is True \
        or h.row().error_code in ("BUILD_ENVIRONMENT_UNPROVEN", "STEP_FAILED_SAFE")


def test_thermal_latch_stops_the_update(h):
    h.host.thermal_ok = False
    h.enqueue_update()

    h.run_to_terminal()

    assert h.row().state is OperationState.FAILED_SAFE
    assert h.host.exchange_count() == 0


def test_inference_failure_after_swap_enters_recovery_or_rollback(h):
    h.enqueue_update()
    h.host.inference_fails = True

    h.run_to_terminal()

    row = h.row()
    assert row.state in (
        OperationState.FAILED_ROLLED_BACK,
        OperationState.RECOVERY_REQUIRED,
    )


# -- request validation ----------------------------------------------------------


def test_update_request_rejects_forbidden_fields(h):
    from bc250_llm_mode.operations.runtime_lifecycle import (
        decode_runtime_update_request,
    )
    from bc250_llm_mode.operations.model import OperationValidationError

    with pytest.raises(OperationValidationError):
        decode_runtime_update_request({"repo_url": "https://evil.example"})
    with pytest.raises(OperationValidationError):
        decode_runtime_update_request({"build_flags": "-j999"})
    with pytest.raises(OperationValidationError):
        decode_runtime_update_request({"skip_smoke": True})
    with pytest.raises(OperationValidationError):
        decode_runtime_update_request({"requested_by": "cron"})


def test_rollback_request_requires_both_ids():
    from bc250_llm_mode.operations.runtime_lifecycle import (
        decode_runtime_rollback_request,
    )
    from bc250_llm_mode.operations.model import OperationValidationError

    decoded = decode_runtime_rollback_request({
        "expected_active_build_id": "legacy:llamacpp",
        "target_build_id": "legacy:llamacpp",
    })
    assert isinstance(decoded, RuntimeRollbackRequestV1)
    with pytest.raises(OperationValidationError):
        decode_runtime_rollback_request({"expected_active_build_id": "x"})
