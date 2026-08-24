"""Session 6A fake-world workflow tests (U1.1 plan §10/§11/§18)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "operations"))

from acquisition_world import FakeAcquisitionHost  # noqa: E402
from fakes import (  # noqa: E402
    CrashInjector,
    FakeClock,
    SequenceIds,
    SimulatedProcessDeath,
)

from bc250_llm_mode.db import initialize_and_close  # noqa: E402
from bc250_llm_mode.operations.acquisition import (  # noqa: E402
    build_acquire_workflow,
    build_import_workflow,
)
from bc250_llm_mode.operations.engine import ExecutionEngine  # noqa: E402
from bc250_llm_mode.operations.model import OperationState  # noqa: E402
from bc250_llm_mode.operations.repositories import (  # noqa: E402
    OperationRepository,
)
from bc250_llm_mode.operations.workflow import (  # noqa: E402
    EnqueueService,
    WorkflowRegistry,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory  # noqa: E402


class AcquisitionHarness:
    def __init__(self, tmp_path, host: FakeAcquisitionHost) -> None:
        self.root = tmp_path / "profile"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()
        self.host = host
        self.operation_ids = SequenceIds("op")
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        registry = WorkflowRegistry()
        registry.register(build_acquire_workflow(host))
        registry.register(build_import_workflow(host))
        self.registry = registry.freeze()
        self._enqueue = EnqueueService(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.operation_ids,
        )
        self.operation_id: str | None = None

    def enqueue_import(self, source_path, *, operation_id="op-imp-1"):
        record = self._enqueue.enqueue(
            operation_type="MODEL_IMPORT",
            payload={"source_path": str(source_path), "requested_by": "test"},
            surface="test",
            operation_id=operation_id,
        )
        self.operation_id = record.id
        return record

    def engine(self, worker_id="worker-a"):
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

    def state(self):
        with self.units.begin() as conn:
            ops = OperationRepository(conn, clock=FakeClock())
            return ops.require(self.operation_id)


@pytest.fixture()
def world(tmp_path):
    host_root = tmp_path / "acq-world"
    host = FakeAcquisitionHost(root=host_root)
    harness = AcquisitionHarness(tmp_path, host)
    source = host.seed_local_source()
    harness.enqueue_import(source)
    return harness


def test_both_workflows_have_exact_eight_keys():
    for builder in (build_acquire_workflow, build_import_workflow):
        definition = builder(object())  # type: ignore[arg-type]
        keys = [s.step_key for s in definition.steps]
        assert keys == [
            "resolve_source",
            "reserve_storage",
            "transfer_source",
            "materialize_candidate",
            "validate_candidate",
            "publish_artifact",
            "register_installation",
            "finalize_staging",
        ]
        assert all(s.resources == ("model-storage",) for s in definition.steps)


def test_happy_import_succeeds_with_single_effects(world):
    outcome = world.engine().execute_one(world.operation_id)
    assert outcome.kind == "COMPLETED"
    record = world.state()
    assert record.state is OperationState.SUCCEEDED
    assert record.result_code == "MODEL_INSTALLED"
    assert world.host.counts == {
        "transfer": 1,
        "materialize": 1,
        "publication": 1,
        "registration": 1,
        "compensation": 0,
    }
    final = world.host.final_path()
    assert final.exists() and not final.name.endswith(".partial")


def test_mandatory_publication_death_converges_exactly_once(world):
    """§1.2 first red test: death after publication, before checkpoint."""
    world.run_to_death("publish_artifact", "before_step_checkpoint")
    record = world.state()
    assert record.state is not OperationState.SUCCEEDED
    assert world.host.final_path().exists()

    world.clock.advance(120)  # expire the dead worker's leases
    outcome = world.engine("worker-b").execute_one(world.operation_id)

    assert outcome.kind == "COMPLETED"
    record = world.state()
    assert record.state is OperationState.SUCCEEDED
    assert world.host.counts["transfer"] == 1      # no recopy/redownload
    assert world.host.counts["materialize"] == 1   # no reconversion
    assert world.host.counts["publication"] == 1   # no republication
    assert world.host.counts["registration"] == 1  # exactly one registration
    assert not world.host.staging_dir(world.operation_id).exists()


def test_invalid_candidate_is_quarantined_not_installed(tmp_path):
    host = FakeAcquisitionHost(
        tmp_path / "bad-world", validation_verdict="invalid",
        invalid_reason="GGUF_INVALID",
    )
    harness = AcquisitionHarness(tmp_path, host)
    source = host.seed_local_source()
    harness.enqueue_import(source, operation_id="op-q-1")
    outcome = harness.engine().execute_one(harness.operation_id)
    assert outcome.kind == "COMPLETED"  # safe terminal, not a crash
    record = harness.state()
    assert record.state is OperationState.FAILED_SAFE
    assert record.result_code == "ARTIFACT_QUARANTINED"
    assert host.quarantine_path("op-q-1").exists()
    assert not host.final_path().exists()
    assert host.registered["op-q-1"]["alias"] is None


def test_duplicate_import_reuses_artifact(tmp_path):
    host = FakeAcquisitionHost(tmp_path / "dup-world")
    harness = AcquisitionHarness(tmp_path, host)
    source = host.seed_local_source()
    harness.enqueue_import(source, operation_id="op-dup-1")
    first = harness.engine().execute_one(harness.operation_id)
    assert first.reason_code == "MODEL_INSTALLED"

    harness.enqueue_import(source, operation_id="op-dup-2")
    second = harness.engine("worker-b").execute_one(harness.operation_id)
    assert second.reason_code == "MODEL_REUSED"
    # The second op still proves the source bytes but never republishes.
    assert host.final_path().exists()
    assert host.counts["publication"] == 1
    assert len(host.registered) == 2


def test_cancellation_at_safe_chunk_becomes_cancelled(world):
    def request_cancel_after_first_pulse():
        with world.units.begin() as conn:
            ops = OperationRepository(conn, clock=FakeClock())
            if not ops.require(world.operation_id).cancel_requested_at:
                ops.request_cancel(world.operation_id)

    fired = {"n": 0}

    def hook():
        if fired["n"] == 0:
            request_cancel_after_first_pulse()
        fired["n"] += 1

    world.host.on_pulse = hook
    outcome = world.engine().execute_one(world.operation_id)
    record = world.state()
    assert record.state is OperationState.CANCELLED
    partial = world.host.partial_path(world.operation_id)
    assert partial.exists()  # labeled operation-owned partial retained
    assert 0 < partial.stat().st_size <= len(world.host.payload)


def test_transfer_resume_reuses_partial_without_recopy(world):
    world.run_to_death("transfer_source", "after_step_checkpoint")
    partial_before = world.host.partial_path(world.operation_id).stat().st_size
    assert partial_before > 0
    world.clock.advance(120)  # expire the dead worker's leases
    outcome = world.engine("worker-b").execute_one(world.operation_id)
    assert outcome.kind == "COMPLETED"
    assert world.host.counts["materialize"] == 1