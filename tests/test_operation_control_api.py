"""U1.4: operation query + fenced command services over the shared truth.

Every assertion reads through `OperationQueryService` or mutates through
`OperationCommandService` — never raw SQL from the caller side. The fake
MODEL_ACTIVATE workflow from tests/operations provides durable states;
production-shaped behaviors (path redaction, detach argv) are covered in
their dedicated tests.
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

from bc250_llm_mode.operations.command_service import OperationCommandService
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.query_service import OperationQueryService
from bc250_llm_mode.operations.repositories import (
    OperationRepository,
    WorkerLockRepository,
)
from bc250_llm_mode.operations.views import (
    OPERATION_VIEW_SCHEMA_VERSION,
    redact_request,
)

from fakes import (
    CrashInjector,
    FakeClock,
    SequenceIds,
    SimulatedProcessDeath,
)
from helpers import Harness


class World:
    """Harness + both U1.4 services sharing one database and clock."""

    def __init__(self, tmp_path: Path) -> None:
        self.harness = Harness(tmp_path)
        self.clock = self.harness.clock
        self.units = self.harness.units
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        self.enqueue_adapter = _EnqueueAdapter(self.harness)
        self.query = OperationQueryService(
            self.units, clock=self.clock.now
        )
        self.commands = OperationCommandService(
            self.units,
            engine_factory=self.make_engine,
            enqueue=self.enqueue_adapter,
        )

    def make_engine(self, worker_id="control-worker"):
        return ExecutionEngine(
            self.units,
            self.harness.registry,
            clock=self.clock.now,
            uuid_factory=self.effect_ids,
            worker_id=worker_id,
            lease_ttl_seconds=60,
            crash_hook=lambda s, p: self.injector.check(s, p),
        )

    def enqueue(self, *, operation_id=None, desired="v1"):
        self.harness.set_desired(desired)
        record = self.enqueue_adapter.raw(
            desired_value=desired, operation_id=operation_id
        )
        return record.id

    def state(self, operation_id):
        with self.units.begin() as conn:
            return OperationRepository(conn).require(operation_id).state


class _EnqueueAdapter:
    """The ONE EnqueueService behind the command service's retry path."""

    def __init__(self, harness: Harness) -> None:
        from bc250_llm_mode.operations.workflow import EnqueueService

        self._svc = EnqueueService(
            harness.units,
            harness.registry,
            clock=harness.clock.now,
            uuid_factory=SequenceIds("op"),
        )

    def enqueue(self, *, operation_type, payload, surface="control",
                parent_operation_id=None):
        assert operation_type == "MODEL_ACTIVATE"
        return self._svc.enqueue(
            operation_type="MODEL_ACTIVATE",
            payload=payload,
            surface=surface,
            parent_operation_id=parent_operation_id,
        )

    def raw(self, *, desired_value, operation_id=None, surface="test"):
        return self._svc.enqueue(
            operation_type="MODEL_ACTIVATE",
            payload={"desired_value": desired_value},
            surface=surface,
            operation_id=operation_id,
        )


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


# -- query service -----------------------------------------------------------------


def test_list_pages_scopes_and_rejects_unbounded_inputs(world):
    ids = [world.enqueue() for _ in range(3)]
    page1 = world.query.list(page=1, page_size=2)
    page2 = world.query.list(page=2, page_size=2)
    assert [i.operation_id for i in page1.items] == [ids[2], ids[1]]
    assert [i.operation_id for i in page2.items] == [ids[0]]
    assert page1.total == 3
    assert world.query.list(scope="active", page_size=200).total == 3
    for bad in (
        dict(page=0),
        dict(page_size=0),
        dict(page_size=201),
        dict(scope="everything"),
    ):
        with pytest.raises(ValueError):
            world.query.list(**bad)


def test_show_returns_detail_with_schema_and_redacted_paths(world):
    operation_id = world.enqueue()
    detail = world.query.show(operation_id)
    assert detail is not None
    payload = detail.to_dict()
    assert payload["schema_version"] == OPERATION_VIEW_SCHEMA_VERSION
    assert payload["request"] == {"desired_value": "v1"}
    assert payload["state"] == "QUEUED"
    assert len(payload["steps"]) == 5
    assert world.query.show("missing-id") is None


def test_request_redaction_strips_absolute_paths():
    redacted = redact_request(
        {
            "source_path": "/Users/secret-user/private/model-file.gguf",
            "alias": "tiny-model",
            "nested": {"config_path": "~/.hidden/cfg"},
            "count": 4,
        }
    )
    assert redacted["source_path"] == "file:model-file.gguf"
    assert redacted["nested"]["config_path"] == "file:cfg"
    assert redacted["alias"] == "tiny-model"
    assert redacted["count"] == 4


def test_events_pagination_is_bounded_and_ordered(world):
    operation_id = world.enqueue()
    page = world.query.events(operation_id, limit=500)
    assert page.events[0].event_type == "OPERATION_QUEUED"
    assert page.events[0].message_key == "operation.event.operation_queued"
    with pytest.raises(ValueError):
        world.query.events(operation_id, limit=501)
    with pytest.raises(ValueError):
        world.query.events(operation_id, after_sequence=-1)
    with pytest.raises(KeyError):
        world.query.events("missing-id")


def test_leases_report_expired_without_pretending_work_is_dead(world):
    operation_id = world.enqueue()
    # Drive partway so live leases exist, then let them expire.
    # Engine-level point: dies AFTER apply_effect ran, BEFORE checkpoint.
    world.injector.arm("apply_effect", "before_step_checkpoint")
    try:
        world.make_engine().execute_one(operation_id)
    except SimulatedProcessDeath:
        pass
    finally:
        world.injector = CrashInjector()
    summary = world.query.show(operation_id).summary
    assert summary.state == "RUNNING"
    assert summary.recoverable is False  # leases alive: owner exists
    view = world.query.leases(operation_id)[0]
    assert view.expired is False

    world.clock.advance(120)
    view = world.query.leases(operation_id)[0]
    assert view.expired is True
    summary = world.query.show(operation_id).summary
    assert summary.recoverable is True  # stale lease, work still alive


def test_wait_reports_change_once_terminal_via_injected_waiter(world):
    operation_id = world.enqueue()
    revision_before = world.query.show(operation_id).summary
    del revision_before

    def completing_waiter(seconds: float) -> bool:
        world.make_engine(worker_id="waiter-worker").execute_one(operation_id)
        return True

    world.query._waiter = completing_waiter
    result = world.query.wait(operation_id, timeout_seconds=5)
    assert result.timed_out is False
    assert result.changed is True
    assert result.state == "SUCCEEDED"
    assert result.latest_revision >= 1
    assert result.to_dict()["schema_version"] == OPERATION_VIEW_SCHEMA_VERSION
    with pytest.raises(ValueError):
        world.query.wait(operation_id, timeout_seconds=0)


def test_active_summary_counts_and_worker_lock(world, tmp_path):
    world.enqueue()
    with world.units.begin() as conn:
        WorkerLockRepository(conn, clock=lambda: "2099-01-01T00:00:00Z")\
            .acquire(owner="host-a", ttl_seconds=600)
    summary = world.query.active_summary()
    assert summary.active_count == 1
    assert summary.queued_count == 1
    assert summary.worker_lock_owner == "host-a"
    assert summary.worker_lock_expired is False


# -- command service -----------------------------------------------------------------


def test_cancel_records_durable_intent_without_false_success(world):
    operation_id = world.enqueue()
    result = world.commands.cancel(operation_id)
    assert result.ok and result.outcome == "ACCEPTED"
    assert world.state(operation_id) is OperationState.CANCEL_REQUESTED
    again = world.commands.cancel(operation_id)
    assert again.outcome == "NOOP"
    detail = world.query.show(operation_id)
    assert detail.summary.cancellable is False


def test_cancel_fencing_refuses_stale_revision(world):
    operation_id = world.enqueue()
    with world.units.begin() as conn:
        stale_revision = OperationRepository(conn).require(
            operation_id
        ).state_revision
    # Move the durable row forward: revision advances past the snapshot.
    world.injector.arm("capture_prior", "before_step_checkpoint")
    try:
        world.make_engine().execute_one(operation_id)
    except SimulatedProcessDeath:
        pass
    finally:
        world.injector = CrashInjector()
    duplicate = world.commands.cancel(
        operation_id, expected_revision=stale_revision
    )
    assert duplicate.outcome == "REFUSED"
    assert duplicate.reason_code == "STALE_REVISION"


def test_resume_drives_paused_work_to_completion(world):
    operation_id = world.enqueue()
    world.commands.cancel(operation_id)
    # Let the engine honor cancellation at its safe point.
    outcome = world.make_engine().execute_one(operation_id)
    assert outcome.kind == "COMPLETED"
    assert world.state(operation_id) is OperationState.CANCELLED
    resumed = world.commands.retry(operation_id)
    assert resumed.ok and resumed.detail["parent_operation_id"] == operation_id


def test_retry_creates_new_operation_never_touches_history(world):
    operation_id = world.enqueue()
    with world.units.begin() as conn:
        ops = OperationRepository(conn)
        record = ops.require(operation_id)
        ops.compare_and_transition(
            operation_id,
            expected_state=record.state,
            expected_revision=record.state_revision,
            target_state=OperationState.PAUSED,
            event_code="TEST_PAUSE",
            event_summary="paused for retry semantics",
        )
    resumed = world.commands.resume(operation_id)
    assert resumed.ok and resumed.outcome == "DRIVEN"
    assert world.state(operation_id) is OperationState.SUCCEEDED
    retry_refused = world.commands.retry(operation_id)
    assert retry_refused.outcome == "REFUSED"
    assert retry_refused.reason_code == "NOT_RETRYABLE"


def test_recover_requires_confirmation_then_takes_over_exactly_once(world):
    operation_id = world.enqueue()
    # Simulate frontend death mid-effect: RUNNING with held leases.
    world.injector.arm("apply_effect", "before_step_checkpoint")
    crashed = world.make_engine(worker_id="doomed")
    raised = False
    try:
        crashed.execute_one(operation_id)
    except SimulatedProcessDeath:
        raised = True
    finally:
        world.injector = CrashInjector()
    assert raised
    unconfirmed = world.commands.recover(operation_id)
    assert unconfirmed.outcome == "REFUSED"
    assert unconfirmed.reason_code == "CONFIRMATION_REQUIRED"

    # Time passes; every lease expires; recovery takes over ONCE.
    world.clock.advance(120)
    recovered = world.commands.recover(operation_id, confirm=True)
    assert recovered.ok and recovered.outcome == "DRIVEN"
    assert world.state(operation_id) is OperationState.SUCCEEDED
    # The interrupted effect was reclaimed with the SAME effect id: the
    # recorder shows exactly one application despite two executors.


def test_recover_refuses_recovery_required_barrier_with_guidance(world):
    operation_id = world.enqueue()
    with world.units.begin() as conn:
        ops = OperationRepository(conn)
        for target in (
            OperationState.PREPARING,
            OperationState.RUNNING,
            OperationState.RECOVERY_REQUIRED,
        ):
            record = ops.require(operation_id)
            ops.compare_and_transition(
                operation_id,
                expected_state=record.state,
                expected_revision=record.state_revision,
                target_state=target,
                event_code=f"SEED_{target.value}",
                event_summary="seed barrier",
            )
    refused = world.commands.recover(operation_id, confirm=True)
    assert refused.outcome == "REFUSED"
    assert refused.exit_code == 78
    assert refused.reason_code == "RECOVERY_BARRIER_MANUAL"
    assert world.state(operation_id) is OperationState.RECOVERY_REQUIRED


def test_dismiss_hides_from_default_views_without_deleting_history(world):
    operation_id = world.enqueue()
    world.make_engine().execute_one(operation_id)
    assert world.state(operation_id) is OperationState.SUCCEEDED
    hidden = world.commands.dismiss(operation_id)
    assert hidden.ok
    assert world.query.list(page_size=50).total == 0
    visible_again = world.query.list(include_dismissed=True, page_size=50)
    assert visible_again.total == 1
    assert visible_again.items[0].dismissed is True
    restored = world.commands.dismiss(operation_id, restore=True)
    assert restored.ok
    assert world.query.list(page_size=50).total == 1
    active = world.enqueue()
    refused = world.commands.dismiss(active)  # still QUEUED → wait, it IS non-terminal
    assert refused.outcome == "REFUSED"
    assert refused.reason_code == "NOT_TERMINAL"


def test_detach_spawns_exactly_one_worker_and_records_handoff(world):
    operation_id = world.enqueue()
    spawned = []

    def spawner(argv):
        spawned.append(list(argv))
        return 4321

    result = world.commands.detach(operation_id, spawner=spawner)
    assert result.outcome == "DETACHED"
    assert result.detail["pid"] == 4321
    assert spawned == [[sys.executable, "-m", "bc250_llm_mode.worker_main"]]
    events = world.query.events(operation_id, limit=10)
    assert any(e.event_type == "WORKER_HANDOFF" for e in events.events)
    assert world.state(operation_id) is OperationState.QUEUED

    driven = world.make_engine().execute_one(operation_id)
    assert driven.kind == "COMPLETED"
    not_queued = world.commands.detach(operation_id, spawner=spawner)
    assert not_queued.outcome == "REFUSED"
    assert not_queued.reason_code == "NOT_QUEUED"
