"""Typed repositories over the operation tables (ADR 002 / migration 003).

Rules (Session 5A §6):

- Repositories NEVER commit. Callers own the unit-of-work boundary so a
  state transition, its step update, and its event commit atomically.
- Every state change is a compare-and-swap against the expected state AND
  revision; a stale caller loses deterministically (``OperationConflict``).
- Terminal states have no outgoing transitions.
- Payloads are sanitized/bounded by ``operations.validation`` before insert;
  timestamps and UUIDs come from injected providers so tests never depend on
  wall-clock ordering.
"""

from __future__ import annotations

import json
import uuid as _uuid
from typing import Any, Callable

from ..legacy_import import utcnow
from .model import (
    OperationConflict,
    OperationEvent,
    OperationLease,
    OperationRecord,
    OperationState,
    OperationStepRecord,
    OperationType,
    StepState,
    assert_step_transition,
    assert_transition,
    is_terminal,
)
from .validation import (
    EVENT_DETAIL_MAX_BYTES,
    EVENT_LEVELS,
    sanitize_payload,
    sanitize_request,
    sanitize_summary,
)
from .model import InvalidTransition, OperationValidationError

DEFAULT_LEASE_TTL_SECONDS = 60

_ROW_COLUMNS = (
    "id, operation_type, request_version, recovery_policy_version,"
    " request_json, state, state_revision, progress_phase, progress_current,"
    " progress_total, progress_unit, progress_summary, surface,"
    " cancel_requested_at, result_code, result_detail, error_code,"
    " error_detail, parent_operation_id, created_at, started_at, updated_at,"
    " finished_at"
)


def _operation_from_row(row) -> OperationRecord:
    return OperationRecord(
        id=row["id"],
        operation_type=OperationType(row["operation_type"]),
        request_version=row["request_version"],
        recovery_policy_version=row["recovery_policy_version"],
        request_json=row["request_json"],
        state=OperationState(row["state"]),
        state_revision=row["state_revision"],
        progress_phase=row["progress_phase"],
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        progress_unit=row["progress_unit"],
        progress_summary=row["progress_summary"],
        surface=row["surface"],
        cancel_requested_at=row["cancel_requested_at"],
        result_code=row["result_code"],
        result_detail=row["result_detail"],
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        parent_operation_id=row["parent_operation_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


def _step_from_row(row) -> OperationStepRecord:
    return OperationStepRecord(
        operation_id=row["operation_id"],
        step_key=row["step_key"],
        sequence=row["sequence"],
        implementation_version=row["implementation_version"],
        state=StepState(row["state"]),
        attempts=row["attempts"],
        input_json=row["input_json"],
        output_json=row["output_json"],
        external_effect_id=row["external_effect_id"],
        failure_code=row["failure_code"],
        failure_detail=row["failure_detail"],
        started_at=row["started_at"],
        checkpointed_at=row["checkpointed_at"],
        finished_at=row["finished_at"],
    )


def _lease_from_row(row) -> OperationLease:
    return OperationLease(
        resource_key=row["resource_key"],
        operation_id=row["operation_id"],
        owner=row["owner"],
        lease_revision=row["lease_revision"],
        acquired_at=row["acquired_at"],
        heartbeat_at=row["heartbeat_at"],
        expires_at=row["expires_at"],
    )


def json_loads_or_none(raw):
    return json.loads(raw) if raw is not None else None


def _require_known(conn, operation_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM operations WHERE id = ?", (operation_id,)
    ).fetchone()
    if row is None:
        raise OperationConflict(f"no such operation: {operation_id}")


class OperationRepository:
    """Create/read/transition durable operations. Never commits."""

    def __init__(
        self,
        conn,
        *,
        clock: Callable[[], str] | None = None,
        uuid_factory: Callable[[], str] | None = None,
    ) -> None:
        self.conn = conn
        self.clock = clock or utcnow
        self.uuid_factory = uuid_factory or (lambda: str(_uuid.uuid4()))
        self.events = EventRepository(conn, clock=self.clock)

    def create(
        self,
        *,
        operation_type: OperationType | str,
        request: dict[str, Any],
        request_version: int = 1,
        recovery_policy_version: int = 1,
        surface: str = "unknown",
        operation_id: str | None = None,
        parent_operation_id: str | None = None,
    ) -> OperationRecord:
        now = self.clock()
        sanitized_request = sanitize_request(
            operation_type, request_version, dict(request)
        )
        operation_type = OperationType(operation_type)
        operation_id = operation_id or self.uuid_factory()
        if parent_operation_id is not None:
            _require_known(self.conn, parent_operation_id)
        try:
            self.conn.execute(
                """
                INSERT INTO operations (
                    id, operation_type, request_version,
                    recovery_policy_version, request_json, state,
                    state_revision, surface, parent_operation_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'QUEUED', 1, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    operation_type.value,
                    request_version,
                    recovery_policy_version,
                    sanitized_request,
                    surface,
                    parent_operation_id,
                    now,
                    now,
                ),
            )
        except Exception as exc:
            raise OperationConflict(
                f"cannot create operation {operation_id!r}: {exc}"
            ) from exc
        self.events.append(
            operation_id,
            summary=f"operation {operation_type.value} queued",
            code="OPERATION_QUEUED",
            detail={"surface": surface},
        )
        record = self.get(operation_id)
        assert record is not None
        return record

    def get(self, operation_id: str) -> OperationRecord | None:
        row = self.conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        return _operation_from_row(row) if row is not None else None

    def require(self, operation_id: str) -> OperationRecord:
        record = self.get(operation_id)
        if record is None:
            raise OperationConflict(f"no such operation: {operation_id}")
        return record

    def list_active(self) -> list[OperationRecord]:
        rows = self.conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM operations"
            " WHERE finished_at IS NULL"
            " ORDER BY created_at, id"
        ).fetchall()
        return [_operation_from_row(row) for row in rows]

    def list_recent(self, limit: int = 20) -> list[OperationRecord]:
        limit = max(1, int(limit))
        rows = self.conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM operations WHERE finished_at IS NOT NULL"
            " ORDER BY finished_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_operation_from_row(row) for row in rows]

    def compare_and_transition(
        self,
        operation_id: str,
        *,
        expected_state: OperationState | str,
        expected_revision: int,
        target_state: OperationState | str,
        event_level: str = "info",
        event_code: str | None = None,
        event_summary: str | None = None,
        event_detail: dict[str, Any] | None = None,
        result_code: str | None = None,
        result_detail: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: dict[str, Any] | None = None,
    ) -> OperationRecord:
        """CAS transition + event append in the caller's open transaction."""
        current = self.require(operation_id)
        assert_transition(expected_state, target_state)
        now = self.clock()

        sets = ["state = ?", "state_revision = state_revision + 1", "updated_at = ?"]
        params: list[Any] = [OperationState(target_state).value, now]
        if current.started_at is None and not is_terminal(target_state):
            sets.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        if is_terminal(OperationState(target_state)):
            sets.append("finished_at = ?")
            params.append(now)
        if result_code is not None:
            sets.append("result_code = ?")
            params.append(result_code)
        if result_detail is not None:
            sets.append("result_detail = ?")
            params.append(
                sanitize_payload(result_detail, max_bytes=EVENT_DETAIL_MAX_BYTES)
            )
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(error_code)
        if error_detail is not None:
            sets.append("error_detail = ?")
            params.append(
                sanitize_payload(error_detail, max_bytes=EVENT_DETAIL_MAX_BYTES)
            )

        params.extend(
            [operation_id, OperationState(expected_state).value, expected_revision]
        )
        cursor = self.conn.execute(
            f"""
            UPDATE operations SET {", ".join(sets)}
            WHERE id = ? AND state = ? AND state_revision = ?
            """,
            params,
        )
        if cursor.rowcount != 1:
            raise OperationConflict(
                f"stale transition lost for {operation_id}: expected "
                f"{expected_state}@{expected_revision}, found "
                f"{current.state.value}@{current.state_revision}"
            )
        self.events.append(
            operation_id,
            level=event_level,
            code=event_code or f"STATE_{OperationState(target_state).value}",
            summary=event_summary or f"{expected_state} -> {target_state}",
            detail=event_detail,
        )
        updated = self.get(operation_id)
        assert updated is not None
        return updated

    def request_cancel(
        self, operation_id: str, *, expected_revision: int | None = None
    ) -> OperationRecord:
        """Durably request cancellation at the next safe point.

        The first accepted request sets ``cancel_requested_at`` in the same
        CAS update that moves state to ``CANCEL_REQUESTED``; a repeated
        request preserves the original timestamp and revision. A stale
        revision changes neither state nor timestamp. Cancellation is refused
        from critical sections (`COMMITTING`, `ROLLING_BACK`) because ADR 002
        deliberately defines no deferred-cancellation state there.
        """
        current = self.require(operation_id)
        if current.state in (
            OperationState.CANCEL_REQUESTED,
            OperationState.CANCELLED,
        ):
            return current  # repeat requests are no-ops
        if current.state in (
            OperationState.COMMITTING,
            OperationState.ROLLING_BACK,
        ):
            raise InvalidTransition(
                f"cancellation refused inside critical section "
                f"{current.state.value}; it will be resolved to a terminal "
                "state before cancellation can be considered"
            )
        if is_terminal(current.state):
            raise InvalidTransition(
                f"cannot cancel terminal operation in {current.state.value}"
            )
        revision = (
            expected_revision
            if expected_revision is not None
            else current.state_revision
        )
        now = self.clock()
        cursor = self.conn.execute(
            """
            UPDATE operations SET
                state = 'CANCEL_REQUESTED',
                cancel_requested_at = ?,
                state_revision = state_revision + 1,
                updated_at = ?
            WHERE id = ? AND state = ? AND state_revision = ?
            """,
            (now, now, operation_id, current.state.value, revision),
        )
        if cursor.rowcount != 1:
            refreshed = self.get(operation_id)
            detail = (
                f"{refreshed.state.value}@{refreshed.state_revision}"
                if refreshed
                else "missing"
            )
            raise OperationConflict(
                f"stale cancellation lost for {operation_id}: expected "
                f"{current.state.value}@{revision}, found {detail}"
            )
        self.events.append(
            operation_id,
            code="CANCEL_REQUESTED",
            summary="cancellation requested; honoring at next safe point",
        )
        updated = self.get(operation_id)
        assert updated is not None
        return updated

    def update_progress(
        self,
        operation_id: str,
        *,
        phase: str,
        current: int,
        total: int | None = None,
        unit: str | None = None,
        summary: str | None = None,
    ) -> OperationRecord:
        """Monotonic progress within a phase; a new phase may reset the counter.

        CAS against the current state+revision (same-state refreshes still
        bump the revision so stale readers lose deterministically).
        """
        record = self.require(operation_id)
        if phase == record.progress_phase and current < record.progress_current:
            raise OperationConflict(
                f"progress moved backwards in phase {phase!r}: "
                f"{record.progress_current} -> {current}"
            )
        now = self.clock()
        cursor = self.conn.execute(
            """
            UPDATE operations SET
                progress_phase = ?, progress_current = ?, progress_total = ?,
                progress_unit = ?, progress_summary = ?,
                state_revision = state_revision + 1, updated_at = ?
            WHERE id = ? AND state = ? AND state_revision = ?
            """,
            (
                phase,
                current,
                total,
                sanitize_summary(unit),
                sanitize_summary(summary),
                now,
                operation_id,
                record.state.value,
                record.state_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise OperationConflict(
                f"stale progress lost for {operation_id} (expected "
                f"{record.state.value}@{record.state_revision})"
            )
        refreshed = self.get(operation_id)
        assert refreshed is not None
        return refreshed

    def record_terminal_result(
        self,
        operation_id: str,
        *,
        terminal_state: OperationState | str,
        result_code: str | None = None,
        result_detail: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: dict[str, Any] | None = None,
        event_summary: str | None = None,
    ) -> OperationRecord:
        """Move to a terminal state and persist the final outcome."""
        record = self.require(operation_id)
        terminal = OperationState(terminal_state)
        if not is_terminal(terminal):
            raise InvalidTransition(f"{terminal.value} is not a terminal state")
        level = "info"
        if terminal in (OperationState.CANCELLED, OperationState.FAILED_ROLLED_BACK):
            level = "warn"
        elif terminal == OperationState.RECOVERY_REQUIRED:
            level = "error"
        return self.compare_and_transition(
            operation_id,
            expected_state=record.state,
            expected_revision=record.state_revision,
            target_state=terminal,
            event_level=level,
            event_code=f"OPERATION_{terminal.value}",
            event_summary=event_summary or f"operation reached {terminal.value}",
            result_code=result_code,
            result_detail=result_detail,
            error_code=error_code,
            error_detail=error_detail,
        )


class EventRepository:
    """Append-only operation events; cursor-based reads. Never commits."""

    def __init__(self, conn, *, clock: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self.clock = clock or utcnow

    def append(
        self,
        operation_id: str,
        *,
        level: str = "info",
        code: str | None = None,
        summary: str,
        detail: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
    ) -> int:
        _require_known(self.conn, operation_id)
        if level not in EVENT_LEVELS:
            raise OperationValidationError(f"unknown event level: {level!r}")
        bounded = sanitize_summary(summary)
        assert bounded is not None, "event summary is required"
        detail_json = (
            sanitize_payload(detail, max_bytes=EVENT_DETAIL_MAX_BYTES)
            if detail is not None
            else None
        )
        progress_json = (
            sanitize_payload(progress, max_bytes=EVENT_DETAIL_MAX_BYTES)
            if progress is not None
            else None
        )
        cursor = self.conn.execute(
            """
            INSERT INTO operation_events (
                operation_id, ts, level, code, summary, detail_json, progress_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                self.clock(),
                level,
                code,
                bounded,
                detail_json,
                progress_json,
            ),
        ).lastrowid
        return int(cursor)

    def list_after(
        self, operation_id: str, *, after_cursor: int = 0, limit: int = 100
    ) -> list[OperationEvent]:
        limit = max(1, int(limit))
        rows = self.conn.execute(
            """
            SELECT cursor, operation_id, ts, level, code, summary,
                   detail_json, progress_json
            FROM operation_events
            WHERE operation_id = ? AND cursor > ?
            ORDER BY cursor
            LIMIT ?
            """,
            (operation_id, after_cursor, limit),
        ).fetchall()
        return [
            OperationEvent(
                cursor=row["cursor"],
                operation_id=row["operation_id"],
                ts=row["ts"],
                level=row["level"],
                code=row["code"],
                summary=row["summary"],
                detail=json_loads_or_none(row["detail_json"]),
                progress=json_loads_or_none(row["progress_json"]),
            )
            for row in rows
        ]


class StepRepository:
    """Step lifecycle transitions (ADR 002 step table). Never commits."""

    def __init__(self, conn, *, clock: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self.clock = clock or utcnow

    def add_steps(
        self,
        operation_id: str,
        steps: list[tuple[str, int]],
        *,
        implementation_version: "int | list[int] | tuple[int, ...]" = 1,
        inputs: dict[str, dict[str, Any]] | None = None,
    ) -> list[OperationStepRecord]:
        _require_known(self.conn, operation_id)
        if isinstance(implementation_version, (list, tuple)):
            versions = [int(v) for v in implementation_version]
        else:
            versions = [int(implementation_version)] * len(steps)
        if len(versions) != len(steps):
            raise OperationValidationError(
                "one implementation version per step is required"
            )
        for version in versions:
            if version < 1:
                raise OperationValidationError(
                    f"implementation versions must be >= 1; got {version}"
                )
        ordered = sorted(
            zip(steps, versions), key=lambda item: item[0][1]
        )
        inputs = inputs or {}
        wanted = {key for key, _sequence in steps}
        for (step_key, sequence), version in ordered:
            input_json = (
                sanitize_payload(inputs[step_key]) if step_key in inputs else None
            )
            try:
                self.conn.execute(
                    """
                    INSERT INTO operation_steps (
                        operation_id, step_key, sequence,
                        implementation_version, state, input_json
                    ) VALUES (?, ?, ?, ?, 'PENDING', ?)
                    """,
                    (
                        operation_id,
                        step_key,
                        sequence,
                        version,
                        input_json,
                    ),
                )
            except Exception as exc:
                raise OperationConflict(
                    f"cannot add step {step_key!r} to {operation_id}: {exc}"
                ) from exc
        return [
            record for record in self.list(operation_id) if record.step_key in wanted
        ]

    def get(self, operation_id: str, step_key: str) -> OperationStepRecord | None:
        row = self.conn.execute(
            "SELECT * FROM operation_steps"
            " WHERE operation_id = ? AND step_key = ?",
            (operation_id, step_key),
        ).fetchone()
        return _step_from_row(row) if row is not None else None

    def require(self, operation_id: str, step_key: str) -> OperationStepRecord:
        step = self.get(operation_id, step_key)
        if step is None:
            raise OperationConflict(f"no such step: {operation_id}:{step_key}")
        return step

    def list(self, operation_id: str) -> list[OperationStepRecord]:
        rows = self.conn.execute(
            "SELECT * FROM operation_steps WHERE operation_id = ? ORDER BY sequence",
            (operation_id,),
        ).fetchall()
        return [_step_from_row(row) for row in rows]

    def _cas_step(
        self,
        operation_id: str,
        step_key: str,
        *,
        expected_state: StepState | str,
        target_state: StepState | str,
        extra_sets: list[str],
        extra_params: list[Any],
    ) -> OperationStepRecord:
        """CAS a step transition; RUNNING->RUNNING reclaim is allowed."""
        expected = StepState(expected_state)
        target = StepState(target_state)
        assert_step_transition(expected, target)
        sets = ["state = ?"] + extra_sets
        params: list[Any] = [target.value, *extra_params]
        cursor = self.conn.execute(
            "UPDATE operation_steps SET " + ", ".join(sets)
            + " WHERE operation_id = ? AND step_key = ? AND state = ?",
            [*params, operation_id, step_key, expected.value],
        )
        if cursor.rowcount != 1:
            raise OperationConflict(
                f"stale step transition lost for {operation_id}:{step_key}"
                f" (expected {expected.value})"
            )
        return self.require(operation_id, step_key)

    def start(
        self,
        operation_id: str,
        step_key: str,
        *,
        external_effect_id: str | None = None,
        reclaim: bool = False,
        input_payload: dict[str, Any] | None = None,
    ) -> OperationStepRecord:
        step = self.require(operation_id, step_key)
        expected = StepState.RUNNING if reclaim else StepState.PENDING
        if reclaim and step.state is not StepState.RUNNING:
            raise OperationConflict(
                f"reclaim requires RUNNING, found {step.state.value}"
            )
        extra_sets = [
            "attempts = attempts + 1",
            "started_at = COALESCE(started_at, ?)",
        ]
        extra_params: list[Any] = [self.clock()]
        if external_effect_id is not None:
            extra_sets.append("external_effect_id = ?")
            extra_params.append(external_effect_id)
        if input_payload is not None:
            extra_sets.append("input_json = ?")
            extra_params.append(sanitize_payload(input_payload))
        return self._cas_step(
            operation_id,
            step_key,
            expected_state=expected,
            target_state=StepState.RUNNING,
            extra_sets=extra_sets,
            extra_params=extra_params,
        )

    def checkpoint(
        self,
        operation_id: str,
        step_key: str,
        *,
        output: dict[str, Any] | None = None,
    ) -> OperationStepRecord:
        extra_sets = ["checkpointed_at = ?"]
        extra_params: list[Any] = [self.clock()]
        if output is not None:
            extra_sets.append("output_json = ?")
            extra_params.append(sanitize_payload(output))
        return self._cas_step(
            operation_id,
            step_key,
            expected_state=StepState.RUNNING,
            target_state=StepState.CHECKPOINTED,
            extra_sets=extra_sets,
            extra_params=extra_params,
        )

    def verify(self, operation_id: str, step_key: str) -> OperationStepRecord:
        return self._cas_step(
            operation_id,
            step_key,
            expected_state=StepState.CHECKPOINTED,
            target_state=StepState.VERIFIED,
            extra_sets=[],
            extra_params=[],
        )

    def fail(
        self,
        operation_id: str,
        step_key: str,
        *,
        expected_state: StepState | str,
        code: str,
        detail: dict[str, Any] | None = None,
    ) -> OperationStepRecord:
        detail_json = (
            sanitize_payload(detail, max_bytes=EVENT_DETAIL_MAX_BYTES)
            if detail is not None
            else None
        )
        return self._cas_step(
            operation_id,
            step_key,
            expected_state=expected_state,
            target_state=StepState.FAILED,
            extra_sets=[
                "failure_code = ?",
                "failure_detail = ?",
                "finished_at = ?",
            ],
            extra_params=[code, detail_json, self.clock()],
        )

    def begin_compensation(
        self, operation_id: str, step_key: str, *, expected_state: StepState | str
    ) -> OperationStepRecord:
        return self._cas_step(
            operation_id,
            step_key,
            expected_state=expected_state,
            target_state=StepState.COMPENSATING,
            extra_sets=[],
            extra_params=[],
        )

    def complete_compensation(
        self, operation_id: str, step_key: str
    ) -> OperationStepRecord:
        return self._cas_step(
            operation_id,
            step_key,
            expected_state=StepState.COMPENSATING,
            target_state=StepState.COMPENSATED,
            extra_sets=["finished_at = ?"],
            extra_params=[self.clock()],
        )


class LeaseRepository:
    """Resource leases: deterministic acquisition, stale-owner protection.

    Contention serializes on the unit of work's BEGIN IMMEDIATE, so exactly
    one contender wins an actively-held resource and winner selection is
    deterministic. Expired takeovers begin a new lease generation
    (revision++), which is how superseded owners are detected.
    """

    def __init__(self, conn, *, clock: Callable[[], str] | None = None) -> None:
        self.conn = conn
        self.clock = clock or utcnow

    @staticmethod
    def _advance(timestamp: str, seconds: int) -> str:
        import datetime

        base = datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        return (base + datetime.timedelta(seconds=int(seconds))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def get(self, resource_key: str) -> OperationLease | None:
        row = self.conn.execute(
            "SELECT * FROM operation_leases WHERE resource_key = ?",
            (resource_key,),
        ).fetchone()
        return _lease_from_row(row) if row is not None else None

    def assert_owned(
        self,
        resource_key: str,
        operation_id: str,
        *,
        owner: str,
        lease_revision: int,
        now: str,
    ) -> None:
        """Fence an owner-sensitive mutation (Session 5B §3.1).

        Proves from the current row that the resource key, operation, owner
        token, and lease revision all match AND the lease has not expired at
        the injected current time. Call this in the SAME write unit of work
        as the mutation it guards; a lost fence raises ``OperationConflict``
        and the stale worker must perform no further writes or effects.
        """
        row = self.conn.execute(
            "SELECT * FROM operation_leases WHERE resource_key = ?",
            (resource_key,),
        ).fetchone()
        if (
            row is None
            or row["operation_id"] != operation_id
            or row["owner"] != owner
            or int(row["lease_revision"]) != int(lease_revision)
            or row["expires_at"] <= now
        ):
            raise OperationConflict(
                f"lost lease for {resource_key!r}: "
                f"owner {owner!r}@{lease_revision} is no longer current"
            )

    def list_expired(self, now: str, *, limit: int = 50) -> list[OperationLease]:
        """Bounded deterministic read of expired leases (Session 5B §3.5)."""
        limit = max(0, int(limit))
        if limit == 0:
            return []
        rows = self.conn.execute(
            "SELECT * FROM operation_leases WHERE expires_at <= ?"
            " ORDER BY resource_key LIMIT ?",
            (now, limit),
        ).fetchall()
        return [_lease_from_row(row) for row in rows]

    def _owning_operation_state(self, resource_key: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT o.state FROM operation_leases l
            JOIN operations o ON o.id = l.operation_id
            WHERE l.resource_key = ?
            """,
            (resource_key,),
        ).fetchone()
        return row["state"] if row is not None else None

    def acquire(
        self,
        resource_key: str,
        *,
        operation_id: str,
        owner: str,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> OperationLease:
        _require_known(self.conn, operation_id)
        now = self.clock()
        expires = self._advance(now, ttl_seconds)
        existing = self.get(resource_key)
        if existing is not None and existing.expires_at > now:
            raise OperationConflict(
                f"resource {resource_key!r} is actively held by "
                f"{existing.owner!r} (revision {existing.lease_revision})"
            )
        # Recovery barrier (Session 5B §3.3): an otherwise expired lease whose
        # owning operation is RECOVERY_REQUIRED may NOT be taken over — the
        # external state is unproven until an explicit recovery resolves it.
        if existing is not None:
            state = self._owning_operation_state(resource_key)
            if state == OperationState.RECOVERY_REQUIRED.value:
                raise OperationConflict(
                    f"resource {resource_key!r} is a RECOVERY_REQUIRED barrier;"
                    " takeover refused until explicit recovery"
                )
        cursor = self.conn.execute(
            """
            INSERT INTO operation_leases (
                resource_key, operation_id, owner, lease_revision,
                acquired_at, heartbeat_at, expires_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(resource_key) DO UPDATE SET
                operation_id = excluded.operation_id,
                owner = excluded.owner,
                lease_revision = operation_leases.lease_revision + 1,
                heartbeat_at = excluded.heartbeat_at,
                expires_at = excluded.expires_at
            WHERE operation_leases.expires_at <= excluded.acquired_at
            """,
            (resource_key, operation_id, owner, now, now, expires),
        )
        if cursor.rowcount != 1:
            raise OperationConflict(
                f"resource {resource_key!r} held; acquisition refused"
            )
        lease = self.get(resource_key)
        assert lease is not None
        return lease

    def heartbeat(
        self,
        resource_key: str,
        *,
        owner: str,
        expected_revision: int,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> OperationLease:
        now = self.clock()
        expires = self._advance(now, ttl_seconds)
        cursor = self.conn.execute(
            """
            UPDATE operation_leases SET heartbeat_at = ?, expires_at = ?
            WHERE resource_key = ? AND owner = ? AND lease_revision = ?
            """,
            (now, expires, resource_key, owner, expected_revision),
        )
        if cursor.rowcount != 1:
            raise OperationConflict(
                f"stale lease owner for {resource_key!r}: "
                f"{owner!r}@{expected_revision}"
            )
        lease = self.get(resource_key)
        assert lease is not None
        return lease

    def release(
        self, resource_key: str, *, owner: str, expected_revision: int
    ) -> None:
        cursor = self.conn.execute(
            """
            DELETE FROM operation_leases
            WHERE resource_key = ? AND owner = ? AND lease_revision = ?
            """,
            (resource_key, owner, expected_revision),
        )
        if cursor.rowcount != 1:
            raise OperationConflict(
                f"stale lease owner for {resource_key!r}: "
                f"{owner!r}@{expected_revision}"
            )

    def takeover(
        self,
        resource_key: str,
        *,
        operation_id: str,
        new_owner: str,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> OperationLease:
        """Take over an EXPIRED lease into a new generation (revision++)."""
        now = self.clock()
        lease = self.get(resource_key)
        if lease is None:
            raise OperationConflict(f"no lease exists for {resource_key!r}")
        if lease.expires_at > now:
            raise OperationConflict(
                f"lease {resource_key!r} has not expired; takeover refused"
            )
        return self.acquire(
            resource_key,
            operation_id=operation_id,
            owner=new_owner,
            ttl_seconds=ttl_seconds,
        )


__all__ = [
    "EventRepository",
    "LeaseRepository",
    "OperationRepository",
    "StepRepository",
]
