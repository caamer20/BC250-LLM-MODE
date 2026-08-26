"""U1.4 §7.2: read-only operation query service.

One typed truth source for CLI, GUI Activity Center, detached workers,
and support tools. Every method opens a READ-ONLY unit of work, composes
repositories without N+1 listing behavior, refuses unbounded inputs, and
reports stale leases without pretending the work is dead.

``wait`` is bounded: production uses a sleep-interval bounded poller
(never a zero-timeout busy loop) and callers may inject a condition-backed
waiter (the GUI background adapter does exactly that).
"""

from __future__ import annotations

import json
import time as _time
from typing import Any, Callable

from .model import (
    OperationState,
    OperationType,
    TERMINAL_STATES,
    TRANSITIONS,
)
from .repositories import (
    LeaseRepository,
    OperationRepository,
    StepRepository,
    WorkerLockRepository,
)
from .views import (
    ActiveOperationSummary,
    EventPage,
    LeaseView,
    OperationDetail,
    OperationEventView,
    OperationPage,
    OperationStepView,
    OperationSummary,
    OperationWaitResult,
    redact_request,
)

MAX_PAGE_SIZE = 200
MAX_EVENT_LIMIT = 500
MAX_WAIT_SECONDS = 3600.0
_MIN_PAGE = 1

_RECOVERY_HINTS = {
    OperationState.RECOVERY_REQUIRED.value: (
        "recovery-required: inspect the retained evidence, then run "
        "`recover --confirm` or the kind-specific resume path"
    ),
    OperationState.PAUSED.value: "resume the paused operation",
    OperationState.FAILED_SAFE.value: (
        "inspect the failure code; retry only when the cause is resolved"
    ),
    OperationState.FAILED_ROLLED_BACK.value: (
        "the previous state was restored and verified; retry creates a "
        "new operation"
    ),
    OperationState.CANCELLED.value: (
        "cancelled safely at a checkpoint; retry creates a new operation"
    ),
    OperationState.SUCCEEDED.value: "nothing to do",
}


class OperationQueryService:
    """Read-only composition over operation repositories."""

    def __init__(
        self,
        units: Any,
        *,
        clock: Callable[[], str] | None = None,
        waiter: Callable[[float], bool] | None = None,
    ) -> None:
        self._units = units
        self._clock = clock or _default_clock
        # waiter(seconds)->bool: True means "state may have changed".
        # Production default: bounded sleep interval (no zero-timeout spin).
        self._waiter = waiter or _default_waiter

    # -- list -------------------------------------------------------------------

    def list(
        self,
        *,
        scope: str = "all",
        kind: str | None = None,
        page: int = 1,
        page_size: int = 20,
        include_dismissed: bool = False,
    ) -> OperationPage:
        if scope not in ("all", "active", "recent"):
            raise ValueError("scope must be one of all|active|recent")
        if not isinstance(page, int) or page < _MIN_PAGE:
            raise ValueError("page must be an integer >= 1")
        if not isinstance(page_size, int) or not (1 <= page_size <= MAX_PAGE_SIZE):
            raise ValueError(f"page_size must be within 1..{MAX_PAGE_SIZE}")
        resolved_kind = _validate_kind(kind)
        with self._units.read() as conn:
            ops = OperationRepository(conn)
            records = ops.list_window(
                scope=scope,
                operation_type=resolved_kind,
                include_dismissed=include_dismissed,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            total = ops.count_window(
                scope=scope,
                operation_type=resolved_kind,
                include_dismissed=include_dismissed,
            )
            summaries = [
                self._summary(record, self._lease_views(conn, record.id))
                for record in records
            ]
        return OperationPage(
            items=tuple(summaries), page=page, page_size=page_size, total=total
        )

    # -- show ---------------------------------------------------------------------

    def show(self, operation_id: str) -> OperationDetail | None:
        with self._units.read() as conn:
            ops = OperationRepository(conn)
            record = ops.get(operation_id)
            if record is None:
                return None
            steps = StepRepository(conn).list(operation_id)
            request = redact_request(json.loads(record.request_json))
            detail = OperationDetail(
                summary=self._summary(record, self._lease_views(conn, operation_id)),
                request=request,
                steps=tuple(self._step_views(steps)),
                current_step=self._current_step(steps),
                leases=self._lease_views(conn, operation_id),
                result_code=record.result_code,
                error_code=record.error_code,
                parent_operation_id=record.parent_operation_id,
            )
        return detail

    def steps(self, operation_id: str) -> tuple[OperationStepView, ...]:
        with self._units.read() as conn:
            rows = StepRepository(conn).list(operation_id)
        return tuple(self._step_views(rows))

    def events(
        self, operation_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> EventPage:
        from .repositories import EventRepository

        if after_sequence < 0:
            raise ValueError("after_sequence must be >= 0")
        if not (1 <= limit <= MAX_EVENT_LIMIT):
            raise ValueError(f"limit must be within 1..{MAX_EVENT_LIMIT}")
        with self._units.read() as conn:
            ops = OperationRepository(conn)
            if ops.get(operation_id) is None:
                raise KeyError(operation_id)
            rows = EventRepository(conn).list_after(
                operation_id, after_cursor=after_sequence, limit=limit
            )
        views = tuple(
            OperationEventView(
                sequence=row.cursor,
                timestamp=row.ts,
                level=row.level,
                event_type=row.code or "UNKNOWN",
                message_key=_message_key(row.code),
                message=row.summary,
                detail=dict(row.detail or {}),
            )
            for row in rows
        )
        return EventPage(
            operation_id=operation_id,
            events=views,
            after_sequence=after_sequence,
            limit=limit,
        )

    def leases(self, operation_id: str) -> tuple[LeaseView, ...]:
        with self._units.read() as conn:
            if OperationRepository(conn).get(operation_id) is None:
                raise KeyError(operation_id)
            return self._lease_views(conn, operation_id)

    # -- wait -----------------------------------------------------------------------

    def wait(
        self,
        operation_id: str,
        *,
        after_revision: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> OperationWaitResult:
        if not (0.0 < timeout_seconds <= MAX_WAIT_SECONDS):
            raise ValueError(
                f"timeout_seconds must be within (0, {MAX_WAIT_SECONDS}]"
            )
        deadline = _time.monotonic() + float(timeout_seconds)
        first = self._snapshot(operation_id)
        start_revision = (
            after_revision if after_revision is not None else first[1]
        )
        changed_seen = first[1] != start_revision
        while not first[2]:  # not terminal
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break
            woke_early = self._waiter(min(remaining, 0.25))
            current = self._snapshot(operation_id)
            if current[1] != start_revision:
                changed_seen = True
            if current[2] or (woke_early and current[1] != first[1]):
                first = current
                if current[2]:
                    break
            first = current
        final = self._snapshot(operation_id)
        timed_out = not final[2]
        return OperationWaitResult(
            operation_id=operation_id,
            state=final[0],
            latest_revision=final[1],
            changed=changed_seen or final[1] != start_revision,
            timed_out=timed_out,
            recommended_command=(
                "operations wait" if timed_out else _recommend_for(final[0])
            ),
        )

    # -- active summary -----------------------------------------------------------

    def active_summary(self) -> ActiveOperationSummary:
        now = self._clock()
        with self._units.read() as conn:
            ops = OperationRepository(conn)
            records = ops.list_active()
            counts = {"queued": 0, "running": 0}
            paused = 0
            recovery = 0
            oldest_queued: str | None = None
            for record in records:
                if record.state is OperationState.QUEUED:
                    counts["queued"] += 1
                    oldest_queued = oldest_queued or record.id
                elif record.state is OperationState.PAUSED:
                    paused += 1
                elif record.state is OperationState.RECOVERY_REQUIRED:
                    recovery += 1
                elif record.state in {
                    OperationState.PREPARING,
                    OperationState.RUNNING,
                    OperationState.VERIFYING,
                    OperationState.COMMITTING,
                    OperationState.CANCEL_REQUESTED,
                    OperationState.ROLLING_BACK,
                }:
                    counts["running"] += 1
            lock = WorkerLockRepository(conn, clock=self._clock).get()
            lock_expired = bool(lock and lock["expires_at"] <= now)
        return ActiveOperationSummary(
            active_count=len(records),
            queued_count=counts["queued"],
            running_count=counts["running"],
            paused_count=paused,
            recovery_required_count=recovery,
            oldest_queued_id=oldest_queued,
            worker_lock_owner=(lock or {}).get("owner"),
            worker_lock_expired=lock_expired,
        )

    # -- internals -----------------------------------------------------------------

    def _snapshot(self, operation_id: str) -> tuple[str, int, bool]:
        with self._units.read() as conn:
            record = OperationRepository(conn).get(operation_id)
        if record is None:
            raise KeyError(operation_id)
        return record.state.value, record.state_revision, not record.active

    def _leases_for(self, conn: Any, operation_id: str):
        try:
            return self._lease_views(conn, operation_id)
        except Exception:  # noqa: BLE001 - listing never fails a query
            return ()

    def _lease_views(self, conn: Any, operation_id: str) -> tuple[LeaseView, ...]:
        now = self._clock()
        return tuple(
            LeaseView(
                resource_key=l.resource_key,
                owner=l.owner,
                lease_revision=l.lease_revision,
                acquired_at=l.acquired_at,
                heartbeat_at=l.heartbeat_at,
                expires_at=l.expires_at,
                expired=l.expires_at <= now,
            )
            for l in LeaseRepository(conn).leases_for_operation(operation_id)
        )

    def _summary(self, record, leases: Any) -> OperationSummary:
        state = record.state
        cancellable = OperationState.CANCEL_REQUESTED in TRANSITIONS[state]
        resumable = state is OperationState.PAUSED
        retryable = state in {
            OperationState.FAILED_SAFE,
            OperationState.FAILED_ROLLED_BACK,
            OperationState.CANCELLED,
        }
        recoverable = (
            state is OperationState.RECOVERY_REQUIRED
            or state
            in {
                OperationState.RUNNING,
                OperationState.PREPARING,
                OperationState.VERIFYING,
                OperationState.CANCEL_REQUESTED,
                OperationState.ROLLING_BACK,
            }
        ) and all(getattr(l, "expired", False) for l in leases)
        held_alive = any(not getattr(l, "expired", False) for l in leases)
        if recoverable and held_alive:
            recoverable = False
        recommendation = _RECOVERY_HINTS.get(state.value, "wait for progress")
        if state is OperationState.QUEUED and any(
            not getattr(l, "expired", False) for l in leases
        ):
            recommendation = "waiting: another operation holds required resources"
        return OperationSummary(
            operation_id=record.id,
            kind=record.operation_type.value,
            kind_version=record.request_version,
            title=f"{record.operation_type.value} ({record.surface})",
            state=state.value,
            phase=record.progress_phase,
            progress_current=record.progress_current,
            progress_total=record.progress_total,
            progress_unit=record.progress_unit,
            created_at=record.created_at,
            updated_at=record.updated_at,
            surface=record.surface,
            cancellable=cancellable,
            resumable=resumable,
            retryable=retryable,
            recoverable=recoverable,
            dismissable=state in TERMINAL_STATES,
            failure_code=record.error_code,
            recovery_recommendation=recommendation,
            dismissed=bool(record.dismissed_at),
        )

    @staticmethod
    def _step_views(rows) -> list[OperationStepView]:
        return [
            OperationStepView(
                ordinal=row.sequence,
                name=row.step_key,
                phase=row.step_key.split("_", 1)[0],
                state=row.state.value,
                attempts=row.attempts,
                started_at=row.started_at,
                finished_at=row.finished_at,
                checkpointed_at=row.checkpointed_at,
                failure_code=row.failure_code,
            )
            for row in sorted(rows, key=lambda r: r.sequence)
        ]

    @staticmethod
    def _current_step(rows) -> str | None:
        pending = [r for r in rows if r.state.value != "VERIFIED"]
        return pending[0].step_key if pending else None


def _validate_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    return OperationType(kind.upper()).value  # raises ValueError on unknown


def _message_key(code: str | None) -> str:
    return f"operation.event.{(code or 'UNKNOWN').lower()}"


def _recommend_for(state_value: str) -> str:
    hint = _RECOVERY_HINTS.get(state_value, "operations show")
    return f"operations show ({hint})"


def _default_clock() -> str:
    from ..legacy_import import utcnow

    return utcnow()


def _default_waiter(seconds: float) -> bool:
    _time.sleep(max(0.05, seconds))
    return False
