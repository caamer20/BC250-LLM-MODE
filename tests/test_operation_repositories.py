"""Session 5A: typed operation repositories — CAS transitions, idempotent
cancellation, monotonic progress, stable event cursors, lease ownership."""

from __future__ import annotations

import threading

import pytest

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.model import (
    InvalidTransition,
    OperationConflict,
    OperationState,
    StepState,
)
from bc250_llm_mode.operations.repositories import (
    EventRepository,
    LeaseRepository,
    OperationRepository,
    StepRepository,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


class FakeClock:
    """Deterministic clock: starts at t0, advances one second per read."""

    def __init__(self, start: str = "2026-08-23T12:00:00Z") -> None:
        import datetime

        self._datetime = datetime
        self._moment = datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")

    def __call__(self) -> str:
        self._moment += self._datetime.timedelta(seconds=1)
        return self._moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def units(tmp_path):
    database = tmp_path / "ops.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


def _create(units, **overrides):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        return ops.create(
            operation_type=overrides.pop("operation_type", "MODEL_ACTIVATE"),
            request=overrides.pop("request", {"model_id": "lfm25-26b"}),
            **overrides,
        )


# --- transitions -------------------------------------------------------------


def test_every_allowed_transition_succeeds_end_to_end(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(
            operation_type="MODEL_ACTIVATE",
            request={"model_id": "lfm25-26b"},
            surface="test",
        )
        op_id = created.id
        revision = created.state_revision

        path = [
            ("QUEUED", "PREPARING"),
            ("PREPARING", "RUNNING"),
            ("RUNNING", "VERIFYING"),
            ("VERIFYING", "COMMITTING"),
            ("COMMITTING", "SUCCEEDED"),
        ]
        for source, target in path:
            updated = ops.compare_and_transition(
                op_id,
                expected_state=source,
                expected_revision=revision,
                target_state=target,
            )
            assert updated.state.value == target
            revision = updated.state_revision

        final = ops.get(op_id)
        assert final.state == OperationState.SUCCEEDED
        assert final.finished_at is not None
        assert final.started_at is not None


def test_disallowed_transition_changes_nothing(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        steps = StepRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={"model_id": "m"})
        steps.add_steps(created.id, [("s1", 1)])
        before_events = len(ops.events.list_after(created.id))

        with pytest.raises((InvalidTransition, OperationConflict)):
            ops.compare_and_transition(
                created.id,
                expected_state="QUEUED",
                expected_revision=created.state_revision,
                target_state="SUCCEEDED",  # skipping the critical section
            )

        after = ops.get(created.id)
        assert after.state == created.state
        assert after.state_revision == created.state_revision
        assert steps.list(created.id)[0].state == "PENDING"
        assert len(ops.events.list_after(created.id)) == before_events


def test_terminal_states_refuse_further_transitions(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={})
        failed = ops.compare_and_transition(
            created.id,
            expected_state="QUEUED",
            expected_revision=created.state_revision,
            target_state="FAILED_SAFE",
            error_code="PRECHECK",
            error_detail={"reason": "model vanished before any mutation"},
        )
        assert failed.state == OperationState.FAILED_SAFE
        for target in ("RUNNING", "PAUSED", "CANCELLED"):
            with pytest.raises((InvalidTransition, OperationConflict)):
                ops.compare_and_transition(
                    created.id,
                    expected_state="FAILED_SAFE",
                    expected_revision=failed.state_revision,
                    target_state=target,
                )


def test_duplicate_operation_ids_fail(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        ops.create(operation_type="MODEL_ACTIVATE", request={}, operation_id="dup")
        with pytest.raises(OperationConflict, match="dup"):
            ops.create(
                operation_type="MODEL_ACTIVATE", request={}, operation_id="dup"
            )


def test_stale_revisions_lose_deterministically(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={})
        stale_revision = created.state_revision

        winner = ops.compare_and_transition(
            created.id,
            expected_state="QUEUED",
            expected_revision=stale_revision,
            target_state="PREPARING",
        )
        assert winner.state_revision == stale_revision + 1

        with pytest.raises(OperationConflict, match="stale"):
            ops.compare_and_transition(
                created.id,
                expected_state="QUEUED",
                expected_revision=stale_revision,
                target_state="CANCELLED",
            )


# --- cancellation / progress -------------------------------------------------


def test_cancellation_is_idempotent(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={})
        preparing = ops.compare_and_transition(
            created.id,
            expected_state="QUEUED",
            expected_revision=created.state_revision,
            target_state="PREPARING",
        )
        running = ops.compare_and_transition(
            preparing.id,
            expected_state="PREPARING",
            expected_revision=preparing.state_revision,
            target_state="RUNNING",
        )
        first = ops.request_cancel(running.id)
        second = ops.request_cancel(first.id)
        assert first.state == OperationState.CANCEL_REQUESTED
        assert second.state == OperationState.CANCEL_REQUESTED
        assert second.state_revision == first.state_revision


def test_cancelled_terminal_ignores_repeat_requests(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={})
        requested = ops.request_cancel(created.id)
        cancelled = ops.compare_and_transition(
            created.id,
            expected_state="CANCEL_REQUESTED",
            expected_revision=requested.state_revision,
            target_state="CANCELLED",
        )
        again = ops.request_cancel(cancelled.id)
        assert again.state == OperationState.CANCELLED


def test_progress_is_monotonic_within_phase_and_resets_on_new_phase(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACQUIRE", request={"url": "x"})
        ops.update_progress(
            created.id, phase="download", current=50, total=100, unit="MiB"
        )
        latest = ops.update_progress(
            created.id, phase="download", current=80, total=100
        )
        assert (latest.progress_current, latest.progress_phase) == (80, "download")

        with pytest.raises(OperationConflict, match="backwards"):
            ops.update_progress(created.id, phase="download", current=10)

        # A new phase may begin a new phase-local counter.
        validated = ops.update_progress(created.id, phase="validate", current=1)
        assert validated.progress_phase == "validate"
        assert validated.progress_current == 1


# --- events / leases ---------------------------------------------------------


def test_event_cursors_stable_and_unique_under_concurrent_append(units):
    created = _create(units, operation_type="MODEL_ACQUIRE", request={"url": "x"})
    errors: list[Exception] = []

    def append_many(worker: int) -> None:
        for i in range(10):
            try:
                with units.begin() as conn:
                    events = EventRepository(conn, clock=FakeClock())
                    events.append(
                        created.id,
                        code=f"W{worker}",
                        summary=f"worker {worker} event {i}",
                    )
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

    threads = [threading.Thread(target=append_many, args=(w,)) for w in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors

    with units.begin() as conn:
        events = OperationRepository(conn, clock=FakeClock()).events.list_after(
            created.id
        )
    cursors = [event.cursor for event in events]
    # 20 worker events + the OPERATION_QUEUED event from create().
    assert len(cursors) == 21
    assert cursors == sorted(cursors)
    assert len(set(cursors)) == len(cursors), "cursors must never repeat"
    # Cursor-based paging is stable: reading after a mid-point cursor yields
    # exactly the later events, in order.
    midpoint = cursors[9]
    from bc250_llm_mode.db import open_database

    with open_database(units.database_path, mode="read") as conn:
        tail_events = OperationRepository(conn, clock=FakeClock()).events.list_after(
            created.id, after_cursor=midpoint
        )
    assert [event.cursor for event in tail_events] == cursors[10:]


def test_only_one_contender_acquires_an_active_lease(units):
    created = _create(units)
    results = []
    lock = threading.Lock()

    def contend(owner: str) -> None:
        try:
            with units.begin() as conn:
                leases = LeaseRepository(conn, clock=FakeClock())
                lease = leases.acquire(
                    "runtime-active", operation_id=created.id, owner=owner
                )
                with lock:
                    results.append(lease)
        except Exception as exc:
            with lock:
                results.append(exc)

    threads = [
        threading.Thread(target=contend, args=("worker-a",)),
        threading.Thread(target=contend, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [r for r in results if not isinstance(r, Exception)]
    losers = [r for r in results if isinstance(r, Exception)]
    assert len(winners) == 1 and len(losers) == 1
    assert isinstance(losers[0], OperationConflict)


def test_expired_takeover_increments_revision(units):
    created = _create(units)
    with units.begin() as conn:
        leases = LeaseRepository(conn, clock=FakeClock())
        first = leases.acquire(
            "runtime-installation",
            operation_id=created.id,
            owner="w1",
            ttl_seconds=1,
        )
        assert first.lease_revision == 1
        taken = leases.takeover(
            "runtime-installation", operation_id=created.id, new_owner="w2"
        )
        assert taken.owner == "w2"
        assert taken.lease_revision == first.lease_revision + 1


def test_stale_owner_cannot_heartbeat_or_release_after_takeover(units):
    created = _create(units)
    with units.begin() as conn:
        leases = LeaseRepository(conn, clock=FakeClock())
        first = leases.acquire(
            "runtime-active", operation_id=created.id, owner="w1", ttl_seconds=1
        )
        leases.takeover(
            "runtime-active", operation_id=created.id, new_owner="w2"
        )

        with pytest.raises(OperationConflict):
            leases.heartbeat(
                "runtime-active",
                owner="w1",
                expected_revision=first.lease_revision,
            )
        with pytest.raises(OperationConflict):
            leases.release(
                "runtime-active", owner="w1", expected_revision=first.lease_revision
            )
        # The new generation works normally.
        renewed = leases.heartbeat(
            "runtime-active", owner="w2", expected_revision=first.lease_revision + 1
        )
        assert renewed.lease_revision == first.lease_revision + 1


# --- step lifecycle ----------------------------------------------------------


def test_step_lifecycle_start_checkpoint_verify_and_compensation(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        steps = StepRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="RUNTIME_UPDATE", request={"tag": "b9000"})
        steps.add_steps(created.id, [("build", 1), ("swap", 2)])

        started = steps.start(created.id, "build", external_effect_id="eff-1")
        assert started.attempts == 1
        assert started.external_effect_id == "eff-1"

        checkpointed = steps.checkpoint(
            created.id, "build", output={"binary_digest": "abc"}
        )
        assert checkpointed.state == StepState.CHECKPOINTED

        verified = steps.verify(created.id, "build")
        assert verified.state == StepState.VERIFIED

        compensating = steps.begin_compensation(
            created.id, "build", expected_state="VERIFIED"
        )
        completed = steps.complete_compensation(compensating.operation_id, "build")
        assert completed.state == StepState.COMPENSATED


def test_step_reclaim_after_death_increments_attempts(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        steps = StepRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACQUIRE", request={"url": "x"})
        steps.add_steps(created.id, [("download", 1)])

        first = steps.start(created.id, "download")
        assert first.attempts == 1
        # Simulated process death: the step is still RUNNING; a new worker
        # reclaims it (RUNNING -> RUNNING) without a new PENDING start.
        reclaimed = steps.start(created.id, "download", reclaim=True)
        assert reclaimed.attempts == 2
        assert reclaimed.state == StepState.RUNNING


def test_duplicate_step_keys_fail(units):
    from bc250_llm_mode.operations.model import OperationConflict as Conflict

    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        steps = StepRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={})
        steps.add_steps(created.id, [("s1", 1)])
        with pytest.raises(Conflict):
            steps.add_steps(created.id, [("s1", 2)])


def test_active_and_recent_queries_are_deterministically_ordered(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        finished_ids = []
        for index in range(3):
            record = ops.create(
                operation_type="MODEL_ACTIVATE",
                request={"index": index},
            )
            done = ops.record_terminal_result(
                record.id,
                terminal_state="FAILED_SAFE",
                error_code="TEST",
            )
            finished_ids.append(done.id)
        active = ops.create(operation_type="MODEL_ACQUIRE", request={"url": "y"})

        recent = ops.list_recent()
        assert [r.id for r in recent] == finished_ids[::-1]  # newest first
        active_list = ops.list_active()
        assert [r.id for r in active_list] == [active.id]
        # Repeat reads are deterministic.
        assert [r.id for r in ops.list_recent()] == [r.id for r in recent]
        assert [r.id for r in ops.list_active()] == [active.id]
