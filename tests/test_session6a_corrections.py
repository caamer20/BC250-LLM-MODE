"""Session 6A final checkpoint §3: entry-correction gates (U1.1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "operations"))

from acquisition_world import FakeAcquisitionHost  # noqa: E402
from fakes import CrashInjector, FakeClock, SequenceIds  # noqa: E402

from bc250_llm_mode.db import initialize_and_close  # noqa: E402
from bc250_llm_mode.operations.acquisition import (  # noqa: E402
    build_import_workflow,
    decode_import_request,
)
from bc250_llm_mode.operations.engine import ExecutionEngine  # noqa: E402
from bc250_llm_mode.operations.model import (  # noqa: E402
    OperationState,
    OperationValidationError,
)
from bc250_llm_mode.operations.repositories import (  # noqa: E402
    OperationRepository,
)
from bc250_llm_mode.operations.workflow import (  # noqa: E402
    EnqueueService,
    WorkflowRegistry,
)
from bc250_llm_mode.repositories import (  # noqa: E402
    RepositoryConflict,
    StorageReservationRepository,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory  # noqa: E402


class Harness:
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
        registry.register(build_import_workflow(host))
        self.registry = registry.freeze()
        self._enqueue = EnqueueService(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.operation_ids,
        )
        self.operation_id: str | None = None

    def enqueue(self, source_path, *, operation_id="op-x-1"):
        record = self._enqueue.enqueue(
            operation_type="MODEL_IMPORT",
            payload={"source_path": str(source_path)},
            surface="test",
            operation_id=operation_id,
        )
        self.operation_id = record.id
        return record

    def engine(self, worker_id="worker-a", registry=None):
        return ExecutionEngine(
            self.units,
            registry or self.registry,
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
        except BaseException:
            pass
        finally:
            self.injector = CrashInjector()

    def state(self):
        with self.units.begin() as conn:
            ops = OperationRepository(conn, clock=FakeClock())
            return ops.require(self.operation_id)


@pytest.fixture()
def world(tmp_path):
    host = FakeAcquisitionHost(tmp_path / "w5b")
    harness = Harness(tmp_path, host)
    source = host.seed_local_source()
    harness.enqueue(source)
    return harness


def test_forward_only_failure_never_deletes_published_artifact(world):
    """§3.1 P0: registration failure after publication must NOT roll the
    published artifact back; the operation fails safe, forward."""
    real_register = world.host.register_installation

    def failing_register(ctx):
        raise RuntimeError("injected registration failure")

    world.host.register_installation = failing_register
    outcome = world.engine().execute_one(world.operation_id)
    assert outcome.kind == "COMPLETED"
    record = world.state()
    assert record.state is OperationState.FAILED_SAFE
    assert world.host.final_path().exists()
    assert world.host.counts["publication"] == 1
    world.host.register_installation = real_register


def test_cancellation_finalizer_releases_reservation_and_retains_partial(world):
    fired = {"n": 0}

    def hook():
        if fired["n"] == 0:
            with world.units.begin() as conn:
                ops = OperationRepository(conn, clock=FakeClock())
                if not ops.require(world.operation_id).cancel_requested_at:
                    ops.request_cancel(world.operation_id)
        fired["n"] += 1

    world.host.on_pulse = hook
    outcome = world.engine().execute_one(world.operation_id)
    assert outcome.kind == "COMPLETED"
    record = world.state()
    assert record.state is OperationState.CANCELLED
    assert record.result_code == "CANCELLED_PARTIAL_RETAINED"
    assert all(r["released"] for r in world.host.reservations.values())
    partial = world.host.partial_path(world.operation_id)
    assert partial.exists() and partial.stat().st_size > 0


def test_stable_step_failure_code_is_preserved(world):
    from dataclasses import fields as dc_fields

    from bc250_llm_mode.operations.workflow import StepFailure

    def failing_verify(ctx):
        raise StepFailure("PARTIAL_NOT_RESUMABLE", "transfer bytes incomplete")

    definition = world.registry.lookup("MODEL_IMPORT", 1, 1)
    trimmed_steps = []
    for step in definition.steps:
        if step.step_key == "transfer_source":
            kwargs = {f.name: getattr(step, f.name) for f in dc_fields(step)}
            kwargs["verify"] = failing_verify
            kwargs["compensate"] = None
            trimmed_steps.append(step.__class__(**kwargs))
        else:
            trimmed_steps.append(step)

    def stop_after_transfer(request, prior):
        return {}

    registry = WorkflowRegistry()
    registry.register(
        definition.__class__(
            operation_type=definition.operation_type,
            request_version=definition.request_version,
            recovery_policy_version=definition.recovery_policy_version,
            decode_request=definition.decode_request,
            steps=tuple(trimmed_steps),
            summary=definition.summary,
            terminal_decision=definition.terminal_decision,
            cancel_finalizer=definition.cancel_finalizer,
        )
    )
    world.engine(registry=registry.freeze()).execute_one(world.operation_id)
    record = world.state()
    assert record.state is OperationState.FAILED_SAFE
    assert record.error_code == "PARTIAL_NOT_RESUMABLE"


def test_reservation_lease_fence_in_same_connection(tmp_path):
    db_path = tmp_path / "s.db"
    initialize_and_close(db_path)
    units = UnitOfWorkFactory(db_path)
    with units.begin() as conn:
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version, "
            "recovery_policy_version, request_json, state, state_revision, "
            "surface, created_at, updated_at) VALUES "
            "('op-fence', 'MODEL_ACQUIRE', 1, 1, '{}', 'RUNNING', 1, "
            "'test', 't', 't')"
        )
        conn.execute(
            "INSERT INTO operation_leases (resource_key, operation_id, owner, "
            "lease_revision, acquired_at, heartbeat_at, expires_at) VALUES "
            "('model-storage', 'op-fence', 'worker-a', 1, 't', 't', "
            "'2999-01-01T00:00:00Z')"
        )
        reservations = StorageReservationRepository(
            conn, clock=lambda: "2000-01-01T00:00:00Z"
        )
        fenced = dict(lease_owner="worker-a", lease_revision=1)
        row = reservations.reserve(
            operation_id="op-fence",
            filesystem_identity="fs",
            required_bytes=100,
            available_bytes=1000,
            reserved_bytes=100,
            **fenced,
        )
        assert row["state"] == "RESERVED"
        with pytest.raises(RepositoryConflict) as err:
            reservations.release(
                "op-fence", lease_owner="stale", lease_revision=1
            )
        assert err.value.code == "LEASE_LOST"
        reservations.release("op-fence", **fenced)
        assert reservations.get("op-fence")["state"] == "RELEASED"


def test_import_requests_fail_closed_on_bad_paths_and_bounds():
    workflow = build_import_workflow(object())  # type: ignore[arg-type]
    decode = workflow.decode_request
    for bad in (
        {"source_path": "relative/model.gguf"},
        {"source_path": "/tmp/bad\x00name.gguf"},
        {"source_path": "/" + "x" * 2000},
        {"source_path": "/tmp/m.gguf", "alias": ""},
        {"source_path": "/tmp/m.gguf", "display_name": "y" * 500},
    ):
        with pytest.raises(OperationValidationError):
            decode(bad)


def test_import_summary_never_contains_source_basename(tmp_path):
    src = tmp_path / "SECRET-BASENAME-CANARY.gguf"
    src.write_bytes(b"x")
    request = decode_import_request({"source_path": str(src)})
    summary = build_import_workflow(object()).summary(request)  # type: ignore[arg-type]
    assert "SECRET-BASENAME-CANARY" not in summary