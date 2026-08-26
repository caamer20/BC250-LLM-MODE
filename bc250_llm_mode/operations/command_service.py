"""U1.4 §7.3: fenced operation command service.

One control plane for cancel / resume / retry / recover / dismiss / wait
/ detach over ANY durable operation kind. Every mutation:

- fences on the durable state revision (CAS) — two concurrent command
  processes can never both win;
- appends an audit event with a stable code;
- returns a typed result (never booleans, never raw rows).

Semantics honored from the plan:

- ``cancel`` records durable intent; the engine honors it at the next
  safe checkpoint — no false "cancelled" result;
- ``resume`` re-arms ONLY paused work and drives it foreground through
  the shared engine factory (same contract as the kind-specific resume
  paths);
- ``retry`` creates a NEW operation from the immutable stored request
  with lineage (`parent_operation_id`); history is never mutated and no
  forward-only effect is repeated outside the engine's probe protocol;
- ``recover`` runs only REAL recovery: takeover of an interrupted
  operation whose every lease has expired, requiring explicit
  confirmation. ``RECOVERY_REQUIRED`` barriers stay protected (ADR 004:
  retained trees demand human verification) and are refused with the
  kind-specific guidance instead.
- ``dismiss`` toggles the durable visibility flag on terminal rows;
  audit history is never deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .model import (
    InvalidTransition,
    OperationConflict,
    OperationState,
    OperationType,
    TERMINAL_STATES,
)
from .repositories import (
    LeaseRepository,
    OperationRepository,
)

_DETACHABLE_KINDS = frozenset(
    {
        OperationType.MODEL_ACTIVATE,
        OperationType.MODEL_ACQUIRE,
        OperationType.MODEL_IMPORT,
        OperationType.RUNTIME_UPDATE,
        OperationType.RUNTIME_ROLLBACK,
    }
)

_RECOVERY_REQUIRED_GUIDANCE = {
    OperationType.RUNTIME_UPDATE: (
        "runtime trees are retained pending verification; run "
        "`bc250 llamacpp status` and follow the repair guidance"
    ),
    OperationType.RUNTIME_ROLLBACK: (
        "runtime trees are retained pending verification; run "
        "`bc250 llamacpp status` and follow the repair guidance"
    ),
}
_DEFAULT_BARRIER_GUIDANCE = (
    "this operation needs manual inspection; see `operations show` for "
    "the retained evidence before deciding"
)


@dataclass(frozen=True)
class CommandResult:
    """Typed result of one control mutation."""

    action: str
    operation_id: str | None
    outcome: str  # ACCEPTED | REFUSED | DETACHED | DRIVEN | NOOP
    state: str | None = None
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome in {"ACCEPTED", "DETACHED", "DRIVEN", "NOOP"}

    @property
    def exit_code(self) -> int:
        """Stable CLI mapping (plan §7.4): 0 ok, 78 recovery gating, 1 fail."""
        if self.ok:
            return 0
        if (
            self.reason_code == "RECOVERY_BARRIER_MANUAL"
            or self.state == OperationState.RECOVERY_REQUIRED.value
        ):
            return 78  # repair/recovery gating
        return 1

    @property
    def reason_code(self) -> str:
        return str(self.detail.get("code") or self.outcome)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "action": self.action,
            "operation_id": self.operation_id,
            "outcome": self.outcome,
            "state": self.state,
            "reason": self.reason,
            **{k: v for k, v in self.detail.items() if k != "code"},
            "code": self.reason_code,
        }


class OperationCommandService:
    """Fenced control mutations over any durable operation."""

    def __init__(
        self,
        units: Any,
        *,
        engine_factory: Any,
        enqueue: Any | None = None,
        spawner: Any | None = None,
        worker_profile_dir: Any | None = None,
    ) -> None:
        self._units = units
        self._engine_factory = engine_factory
        # The ONE shared EnqueueService from composition; retry re-enqueues
        # through it so new operations share the frozen registry.
        self._enqueue = enqueue
        # spawner(argv) -> pid: injectable for tests; production binds
        # worker_service.spawn_detached so exactly ONE helper process runs.
        self._spawner = spawner
        # §7.5: the spawned worker must serve THIS profile's database.
        # Composition injects application.paths.app_dir here.
        self._worker_profile_dir = worker_profile_dir

    # -- cancel ---------------------------------------------------------------

    def cancel(
        self,
        operation_id: str,
        *,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> CommandResult:
        with self._units.begin() as conn:
            ops = OperationRepository(conn)
            record = ops.require(operation_id)
            if record.state in TERMINAL_STATES:
                return CommandResult(
                    "cancel", operation_id, "NOOP",
                    state=record.state.value,
                    reason="operation already reached a terminal state",
                    detail={"code": "ALREADY_TERMINAL"},
                )
            if record.state is OperationState.CANCEL_REQUESTED:
                return CommandResult(
                    "cancel", operation_id, "NOOP",
                    state=record.state.value,
                    reason=(
                        "cancellation was already requested and is pending "
                        "its safe point"
                    ),
                    detail={"code": "ALREADY_CANCEL_REQUESTED"},
                )
            try:
                updated = ops.request_cancel(
                    operation_id, expected_revision=expected_revision
                )
            except (InvalidTransition, OperationConflict) as exc:
                return CommandResult(
                    "cancel", operation_id, "REFUSED",
                    state=ops.require(operation_id).state.value,
                    reason=str(exc),
                    detail={"code": _refusal_code(exc)},
                )
        return CommandResult(
            "cancel", operation_id, "ACCEPTED",
            state=updated.state.value,
            reason=(
                "cancellation requested; the engine honors it at the next "
                "safe checkpoint"
            ),
            detail={"cancel_requested_at": updated.cancel_requested_at},
        )

    # -- resume ---------------------------------------------------------------

    def resume(self, operation_id: str) -> CommandResult:
        with self._units.begin() as conn:
            ops = OperationRepository(conn)
            record = ops.require(operation_id)
            if record.state not in (
                OperationState.PAUSED,
                OperationState.QUEUED,
            ):
                return CommandResult(
                    "resume", operation_id, "REFUSED",
                    state=record.state.value,
                    reason=(
                        "only queued or safely-paused operations can be "
                        "resumed"
                    ),
                    detail={"code": "NOT_RESUMABLE"},
                )
            if record.state is OperationState.PAUSED:
                updated = ops.compare_and_transition(
                    operation_id,
                    expected_state=OperationState.PAUSED,
                    expected_revision=record.state_revision,
                    target_state=OperationState.PREPARING,
                    event_code="OPERATION_RESUMED",
                    event_summary="paused work re-armed by operator command",
                )
                state_after = updated.state.value
            else:
                state_after = record.state.value
        outcome = self._engine_factory().execute_one(operation_id)
        final = self._require(operation_id)
        return CommandResult(
            "resume", operation_id, "DRIVEN",
            state=final.state.value,
            reason=None,
            detail={
                "rearmed_state": state_after,
                "engine_outcome": getattr(outcome, "kind", ""),
                "result_code": final.result_code,
                "error_code": final.error_code,
            },
        )

    # -- retry ------------------------------------------------------------------

    def retry(self, operation_id: str) -> CommandResult:
        """Create a NEW operation from the immutable stored request.

        History is never mutated and no forward-only effect is repeated
        outside the engine's probe protocol: the new operation walks the
        same workflow, whose probes make prior durable effects visible.
        """
        if self._enqueue is None:
            raise RuntimeError("OperationCommandService requires enqueue")
        with self._units.read() as conn:
            record = OperationRepository(conn).get(operation_id)
        if record is None:
            raise KeyError(operation_id)
        if record.state not in (
            OperationState.FAILED_SAFE,
            OperationState.FAILED_ROLLED_BACK,
            OperationState.CANCELLED,
        ):
            return CommandResult(
                "retry", operation_id, "REFUSED",
                state=record.state.value,
                reason=(
                    "only failed-safe, rolled-back or cancelled operations "
                    "can be retried"
                ),
                detail={"code": "NOT_RETRYABLE"},
            )
        payload = json.loads(record.request_json)
        new_record = self._enqueue.enqueue(
            operation_type=record.operation_type,
            payload=payload,
            surface=f"retry:{record.surface}",
            parent_operation_id=record.id,
        )
        return CommandResult(
            "retry", new_record.id, "ACCEPTED",
            state=new_record.state.value,
            reason="new operation created from the immutable request",
            detail={"parent_operation_id": record.id},
        )

    # -- recover -----------------------------------------------------------------

    def recover(
        self, operation_id: str, *, confirm: bool = False
    ) -> CommandResult:
        with self._units.read() as conn:
            record = OperationRepository(conn).require(operation_id)
            leases = LeaseRepository(conn).leases_for_operation(operation_id)
        now = _now_string()
        all_expired = all(lease.expires_at <= now for lease in leases)
        if record.state is OperationState.RECOVERY_REQUIRED:
            guidance = _RECOVERY_REQUIRED_GUIDANCE.get(
                record.operation_type, _DEFAULT_BARRIER_GUIDANCE
            )
            return CommandResult(
                "recover", operation_id, "REFUSED",
                state=record.state.value,
                reason=guidance,
                detail={"code": "RECOVERY_BARRIER_MANUAL"},
            )
        if record.active and not all_expired:
            return CommandResult(
                "recover", operation_id, "REFUSED",
                state=record.state.value,
                reason=(
                    "a live lease still owns this operation; recovery would "
                    "race the owner"
                ),
                detail={"code": "LEASE_HELD"},
            )
        if not confirm:
            return CommandResult(
                "recover", operation_id, "REFUSED",
                state=record.state.value,
                reason=(
                    "recovery takes over interrupted work; re-run with "
                    "--confirm after inspecting `operations show`"
                ),
                detail={"code": "CONFIRMATION_REQUIRED"},
            )
        if not record.active:
            return CommandResult(
                "recover", operation_id, "NOOP",
                state=record.state.value,
                reason="nothing to recover at a terminal state",
                detail={"code": "ALREADY_TERMINAL"},
            )
        outcome = self._engine_factory().execute_one(operation_id)
        final = self._require(operation_id)
        return CommandResult(
            "recover", operation_id, "DRIVEN",
            state=final.state.value,
            reason=None,
            detail={
                "engine_outcome": getattr(outcome, "kind", ""),
                "result_code": final.result_code,
                "error_code": final.error_code,
            },
        )

    # -- dismiss -------------------------------------------------------------------

    def dismiss(self, operation_id: str, *, restore: bool = False) -> CommandResult:
        with self._units.begin() as conn:
            ops = OperationRepository(conn)
            try:
                updated = ops.set_dismissed(operation_id, dismissed=not restore)
            except InvalidTransition as exc:
                current = ops.require(operation_id)
                return CommandResult(
                    "dismiss", operation_id, "REFUSED",
                    state=current.state.value,
                    reason=str(exc),
                    detail={"code": "NOT_TERMINAL"},
                )
        return CommandResult(
            "dismiss", operation_id, "ACCEPTED",
            state=updated.state.value,
            reason=(
                "restored to default views" if restore else "hidden from default views"
            ),
            detail={"dismissed_at": updated.dismissed_at},
        )

    # -- detach ----------------------------------------------------------------------

    def detach(self, operation_id: str, *, spawner: Any | None = None) -> CommandResult:
        """U1.4 §7.5: hand ONE queued operation to THE ONE worker host."""
        with self._units.read() as conn:
            record = OperationRepository(conn).require(operation_id)
        if record.operation_type not in _DETACHABLE_KINDS:
            return CommandResult(
                "detach", operation_id, "REFUSED",
                state=record.state.value,
                reason=(
                    f"{record.operation_type.value} does not declare detached "
                    "execution safe"
                ),
                detail={"code": "NOT_DETACHABLE"},
            )
        if record.state is not OperationState.QUEUED:
            return CommandResult(
                "detach", operation_id, "REFUSED",
                state=record.state.value,
                reason="only QUEUED operations can be handed to a worker",
                detail={"code": "NOT_QUEUED"},
            )
        with self._units.begin() as conn:
            OperationRepository(conn).events.append(
                operation_id,
                code="WORKER_HANDOFF",
                summary="operator requested detached execution",
                detail={"entry": "bc250_llm_mode.worker_main"},
            )
        spawn = spawner or self._spawner
        try:
            from ..worker_service import WORKER_MAIN_ARGV

            # Extras appended AFTER the fixed base argv (never a slice of
            # the base — duplicating any base element would change what
            # the entry point parses).
            extra: list[str] = []
            if self._worker_profile_dir is not None:
                extra.extend(["--profile", str(self._worker_profile_dir)])
            if spawn is None:
                # Production handoff: the ONE typed spawn helper.
                from ..worker_service import spawn_detached

                pid = int(spawn_detached(extra_argv=extra))
            else:
                # Injectable spawners mirror spawn_detached's recorder
                # contract: they receive the full fixed typed argv.
                pid = int(spawn(list(WORKER_MAIN_ARGV) + extra))
        except Exception as exc:  # noqa: BLE001 - typed mapping only
            return CommandResult(
                "detach", operation_id, "REFUSED",
                state=OperationState.QUEUED.value,
                reason=f"worker spawn failed ({type(exc).__name__})",
                detail={"code": "WORKER_SPAWN_FAILED"},
            )
        if pid <= 0:
            return CommandResult(
                "detach", operation_id, "REFUSED",
                state=OperationState.QUEUED.value,
                reason="worker spawn returned no valid pid",
                detail={"code": "WORKER_SPAWN_FAILED"},
            )
        return CommandResult(
            "detach", operation_id, "DETACHED",
            state=OperationState.QUEUED.value,
            reason="queued operation handed to the profile worker host",
            detail={"pid": pid, "continues_after_close": True},
        )

    # -- internals --------------------------------------------------------------------

    def _require(self, operation_id: str):
        with self._units.read() as conn:
            return OperationRepository(conn).require(operation_id)


def _refusal_code(exc: Exception) -> str:
    if isinstance(exc, InvalidTransition):
        text = str(exc)
        if "critical section" in text:
            return "CRITICAL_SECTION"
        if "terminal" in text:
            return "ALREADY_TERMINAL"
        return "INVALID_TRANSITION"
    if isinstance(exc, OperationConflict):
        return "STALE_REVISION"
    return "REFUSED"


def _now_string() -> str:
    from ..legacy_import import utcnow

    return utcnow()
