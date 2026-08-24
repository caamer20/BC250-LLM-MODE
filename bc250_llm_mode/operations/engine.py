"""Deterministic single-operation executor (Session 5B §7-§8).

``ExecutionEngine.execute_one`` owns orchestration, never threads. The
central safety rule: commit intent and close the transaction BEFORE an
external effect runs; inspect reality afterwards; open a NEW transaction to
checkpoint. Every owner-sensitive transaction begins by fencing all held
leases; a lost fence aborts the run (``LOST_LEASE`` outcome) and the stale
worker performs no further writes or effects.

Simulated process death arrives as a ``BaseException`` from the fake effect;
the engine never converts it into compensation, and it propagates out
unchanged so no lease release or failure event is fabricated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .model import (
    InvalidTransition,
    OperationConflict,
    OperationState,
    StepState,
)
from .recovery import RecoveryAction, RecoveryClass, decide_recovery
from .repositories import (
    EventRepository,
    LeaseRepository,
    OperationRepository,
    StepRepository,
)
from .validation import sanitize_payload
from .workflow import (
    EffectContext,
    StepDefinition,
    WorkflowDefinition,
    WorkflowVersionUnavailable,
)


class LostLease(Exception):
    """Internal signal: a lease fence was lost mid-run."""


@dataclass(frozen=True)
class ExecutionOutcome:
    """Typed result of one ``execute_one`` call. Never frontend text."""

    kind: str  # COMPLETED | SKIPPED_BUSY | SKIPPED_TERMINAL | SKIPPED_PAUSED |
    # PAUSED | LOST_LEASE | RECOVERY_REQUIRED_OUTCOME | SHUTDOWN_CHECKPOINT |
    # WORKFLOW_VERSION_UNAVAILABLE | NO_SUCH_OPERATION
    operation_id: str | None
    reason_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _row_inputs(row) -> dict[str, Any]:
    import json as _json

    return _json.loads(row.input_json) if row.input_json else {}


_TERMINAL_SKIP_STATES = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.CANCELLED,
        OperationState.FAILED_SAFE,
        OperationState.FAILED_ROLLED_BACK,
    }
)


class ExecutionEngine:
    def __init__(
        self,
        units: Any,
        registry: WorkflowRegistry,
        *,
        clock: Callable[[], str],
        uuid_factory: Callable[[], str],
        worker_id: str = "engine",
        lease_ttl_seconds: int = 60,
        shutdown_requested: Callable[[], bool] | None = None,
        on_lease_acquired: Callable[[str], None] | None = None,
        crash_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        self.units = units
        self.registry = registry
        self.clock = clock
        self.uuid_factory = uuid_factory
        self.worker_id = worker_id
        self.lease_ttl_seconds = lease_ttl_seconds
        self.shutdown_requested = shutdown_requested or (lambda: False)
        self.on_lease_acquired = on_lease_acquired
        self.crash_hook = crash_hook
        self._crash = self.crash_hook or (lambda step_key, point: None)

    # -- infrastructure --------------------------------------------------------

    def _repos(self, conn):
        ops = OperationRepository(
            conn, clock=self.clock, uuid_factory=self.uuid_factory
        )
        steps = StepRepository(conn, clock=self.clock)
        leases = LeaseRepository(conn, clock=self.clock)
        events = EventRepository(conn, clock=self.clock)
        return ops, steps, leases, events

    def _now(self) -> str:
        return self.clock()

    def _fence(
        self, leases: LeaseRepository, operation_id: str, held: dict[str, int]
    ) -> None:
        now = self._now()
        for key in sorted(held):
            leases.assert_owned(
                key,
                operation_id,
                owner=self.worker_id,
                lease_revision=held[key],
                now=now,
            )

    def execute_one(self, operation_id: str) -> ExecutionOutcome:
        try:
            return self._execute(operation_id)
        except LostLease as lost:
            return ExecutionOutcome(
                "LOST_LEASE",
                operation_id,
                reason_code="LOST_LEASE",
                detail={"reason": str(lost)[:256]},
            )

    # -- claim -----------------------------------------------------------------

    def _claim(self, operation_id: str):
        """Resolve the workflow, acquire sorted resources, run preflight.

        Returns ``(definition, decoded_request, held)`` or an
        :class:`ExecutionOutcome` explaining why the operation was skipped.
        """
        with self.units.begin() as conn:
            ops, _steps, _leases, _events = self._repos(conn)
            record = ops.get(operation_id)
            if record is None:
                return ExecutionOutcome("NO_SUCH_OPERATION", operation_id)
            state = record.state
            request_json = record.request_json

        try:
            definition = self.registry.lookup(
                record.operation_type,
                record.request_version,
                record.recovery_policy_version,
            )
        except WorkflowVersionUnavailable:
            if state in (OperationState.QUEUED, OperationState.PREPARING):
                with self.units.begin() as conn:
                    ops, _s, _l, _e = self._repos(conn)
                    current = ops.require(operation_id)
                    ops.compare_and_transition(
                        operation_id,
                        expected_state=current.state,
                        expected_revision=current.state_revision,
                        target_state=OperationState.PAUSED,
                        event_code="WORKFLOW_VERSION_UNAVAILABLE",
                        event_summary="no executor for this durable version",
                    )
                return ExecutionOutcome(
                    "WORKFLOW_VERSION_UNAVAILABLE", operation_id
                )
            # Effecting rows are left untouched for a newer application.
            return ExecutionOutcome(
                "WORKFLOW_VERSION_UNAVAILABLE",
                operation_id,
                reason_code="REQUIRES_NEWER_APPLICATION",
            )

        if state in _TERMINAL_SKIP_STATES:
            return ExecutionOutcome("SKIPPED_TERMINAL", operation_id, state.value)
        if state is OperationState.RECOVERY_REQUIRED:
            return ExecutionOutcome(
                "SKIPPED_TERMINAL",
                operation_id,
                reason_code="RECOVERY_REQUIRED_BARRIER",
            )
        if state is OperationState.PAUSED:
            return ExecutionOutcome("SKIPPED_PAUSED", operation_id)

        decoded = definition.decode_request(json.loads(request_json))

        held: dict[str, int] = {}
        with self.units.begin() as conn:
            _ops, _steps, leases, _events = self._repos(conn)
            try:
                for key in definition.all_resources():  # sorted inside
                    lease = leases.acquire(
                        key,
                        operation_id=operation_id,
                        owner=self.worker_id,
                        ttl_seconds=self.lease_ttl_seconds,
                    )
                    held[key] = lease.lease_revision
                    if self.on_lease_acquired is not None:
                        self.on_lease_acquired(key)
            except OperationConflict:
                # Another live owner holds one of these resources; nothing to
                # roll back (single transaction) — report busy deterministically.
                if held:
                    raise
                return ExecutionOutcome(
                    "SKIPPED_BUSY", operation_id, reason_code="RESOURCE_HELD"
                )

        try:
            definition.preflight(decoded)
        except Exception as exc:
            return self._fail_safe(operation_id, held, "preflight", exc)
        return definition, decoded, held

    # -- orchestration -----------------------------------------------------------

    def _execute(self, operation_id: str) -> ExecutionOutcome:
        claimed = self._claim(operation_id)
        if isinstance(claimed, ExecutionOutcome):
            return claimed
        definition, decoded, held = claimed
        effected: list[StepDefinition] = []

        with self.units.begin() as conn:
            ops, _s, leases, _e = self._repos(conn)
            state_now = ops.require(operation_id).state

        # §3.2: a row left ROLLING_BACK by a dead executor resumes its
        # durable compensation under this executor; it never restarts work.
        if state_now is OperationState.ROLLING_BACK:
            return self._compensate_and_finalize(
                operation_id,
                workflow=definition,
                decoded=decoded,
                held=held,
                reason_code="RESUMED_ROLLBACK",
                cancelled=False,
            )

        while True:
            with self.units.begin() as conn:
                ops, steps_repo, leases, _events = self._repos(conn)
                operation = ops.require(operation_id)
                self._fence(leases, operation_id, held)
                rows = {s.step_key: s for s in steps_repo.list(operation_id)}

            pending = next(
                (
                    s
                    for s in definition.steps
                    if rows[s.step_key].state is not StepState.VERIFIED
                ),
                None,
            )
            if pending is None:
                return self._complete(operation_id, held)

            # An accepted cancellation (durable CANCEL_REQUESTED) is honored
            # at the next safe point of the loop — never inside COMMITTING.
            safe_to_cancel = not (
                (
                    pending.critical
                    and rows[pending.step_key].state is StepState.RUNNING
                )
                or operation.state is OperationState.COMMITTING
            )
            if (
                operation.cancel_requested_at is not None
                and operation.state is OperationState.CANCEL_REQUESTED
                and safe_to_cancel
            ):
                return self._honor_cancellation(
                    operation_id, workflow=definition, decoded=decoded,
                    held=held, effected=effected,
                )
            if (
                self.shutdown_requested()
                and operation.state
                in (OperationState.RUNNING, OperationState.PREPARING, OperationState.VERIFYING)
            ):
                return self._pause(operation_id, held, "SHUTDOWN_REQUESTED")

            result = self._advance_step(
                operation_id, definition, decoded, pending,
                rows[pending.step_key], held, effected,
            )
            if isinstance(result, ExecutionOutcome):
                return result
            effected = result

    # -- step protocol -----------------------------------------------------------

    def _ensure_running(self, operation_id: str, held: dict[str, int]) -> None:
        """Move QUEUED/PREPARING to RUNNING through the closed table."""
        chain = {
            OperationState.QUEUED: [OperationState.PREPARING, OperationState.RUNNING],
            OperationState.PREPARING: [OperationState.RUNNING],
        }
        while True:
            with self.units.begin() as conn:
                ops, _s, leases, _e = self._repos(conn)
                record = ops.require(operation_id)
                self._fence(leases, operation_id, held)
                targets = chain.get(record.state)
                if not targets:
                    return
                ops.compare_and_transition(
                    operation_id,
                    expected_state=record.state,
                    expected_revision=record.state_revision,
                    target_state=targets[0],
                )

    def _intent_transaction(
        self,
        operation_id: str,
        step: StepDefinition,
        decoded: Any,
        held: dict[str, int],
        *,
        reclaim: bool,
        prior_outputs: dict[str, Any],
    ) -> EffectContext:
        """§7.3 (corrected §3.4): fence, resolve effect id and canonical
        input, persist intent.

        A reclaim reuses the SAME external-effect id and the durably stored
        input; ``derive_input`` runs exactly once per NEW attempt, never on
        a reclaim.
        """
        self._ensure_running(operation_id, held)
        self._crash(step.step_key, "before_step_start")
        effect_id: str | None = None
        inputs: dict[str, Any] | None = None
        with self.units.begin() as conn:
            _ops, steps_repo, leases, _events = self._repos(conn)
            self._fence(leases, operation_id, held)
            row = steps_repo.require(operation_id, step.step_key)
            if reclaim and row.state is not StepState.RUNNING:
                raise LostLease(
                    f"{operation_id}:{step.step_key} reclaim requires RUNNING"
                )
            if not reclaim and row.state is not StepState.PENDING:
                raise OperationConflict(
                    f"step {step.step_key!r} is {row.state.value}, not PENDING"
                )
            if reclaim and row.external_effect_id:
                # Recovery reuses the SAME external-effect id: probe/verify
                # identity depends on it.
                effect_id = row.external_effect_id
                if row.input_json:
                    inputs = json.loads(row.input_json)
        if effect_id is None:
            effect_id = self.uuid_factory()
        if inputs is None:
            inputs = dict(step.derive_input(request=decoded, prior=prior_outputs))
        with self.units.begin() as conn:
            _ops, steps_repo, leases, events = self._repos(conn)
            self._fence(leases, operation_id, held)
            started = steps_repo.start(
                operation_id,
                step.step_key,
                external_effect_id=effect_id,
                reclaim=reclaim,
                input_payload=inputs,
            )
            events.append(
                operation_id,
                code="STEP_RECLAIMED" if reclaim else "STEP_STARTED",
                summary=f"step {step.step_key} "
                + ("reclaimed" if reclaim else "started")
                + f" (attempt {started.attempts})",
                detail={
                    "external_effect_id": effect_id,
                    "phase": step.phase,
                },
            )
        return EffectContext(
            operation_id=operation_id,
            step_key=step.step_key,
            external_effect_id=effect_id,
            inputs=inputs,
            prior_outputs=dict(prior_outputs),
            request=decoded,
            pulse=self._make_pulse(operation_id, held),
        )

    def _make_pulse(self, operation_id: str, held: dict[str, int]):
        """Fenced heartbeat/progress callable handed to step effects (§3.6)."""

        def pulse(
            *,
            phase: str | None = None,
            current: int | None = None,
            total: int | None = None,
            unit: str | None = None,
            summary: str | None = None,
        ) -> None:
            try:
                with self.units.begin() as conn:
                    ops, _s, leases, _e = self._repos(conn)
                    record = ops.require(operation_id)
                    self._fence(leases, operation_id, held)
                    for key in sorted(held):
                        leases.heartbeat(
                            key,
                            owner=self.worker_id,
                            expected_revision=held[key],
                            ttl_seconds=self.lease_ttl_seconds,
                        )
                    if phase is not None or current is not None:
                        ops.update_progress(
                            operation_id,
                            phase=phase or record.progress_phase or "running",
                            current=int(current or 0),
                            total=total,
                            unit=unit,
                            summary=summary,
                        )
            except OperationConflict as lost:
                raise LostLease(f"fenced pulse lost: {lost}") from lost

        return pulse

    def _checkpoint_transaction(
        self,
        operation_id: str,
        step: StepDefinition,
        held: dict[str, int],
        ctx: EffectContext,
        output: dict[str, Any] | None,
        *,
        recovered: bool,
    ) -> None:
        """§7.5: fence; CAS RUNNING -> CHECKPOINTED with sanitized output."""
        with self.units.begin() as conn:
            ops, steps_repo, leases, events = self._repos(conn)
            operation = ops.require(operation_id)
            del operation
            self._fence(leases, operation_id, held)
            row = steps_repo.require(operation_id, step.step_key)
            if row.state is not StepState.RUNNING:
                raise LostLease(
                    f"{operation_id}:{step.step_key} checkpoint expected RUNNING,"
                    f" found {row.state.value}"
                )
            checked = steps_repo.checkpoint(
                operation_id, step.step_key, output=output
            )
            events.append(
                operation_id,
                code="STEP_CHECKPOINTED",
                summary=(
                    f"step {step.step_key} checkpointed"
                    + (" from recovery evidence" if recovered else "")
                ),
                detail={
                    "output": (
                        json.loads(checked.output_json)
                        if checked.output_json
                        else None
                    )
                },
                progress={"phase": step.phase, "current": step.sequence},
            )

    def _verify_transaction(
        self, operation_id: str, step: StepDefinition, held: dict[str, int]
    ) -> None:
        with self.units.begin() as conn:
            ops, steps_repo, leases, events = self._repos(conn)
            operation = ops.require(operation_id)
            del operation
            self._fence(leases, operation_id, held)
            row = steps_repo.require(operation_id, step.step_key)
            if row.state is not StepState.CHECKPOINTED:
                raise LostLease(
                    f"{operation_id}:{step.step_key} verify expected CHECKPOINTED,"
                    f" found {row.state.value}"
                )
            steps_repo.verify(operation_id, step.step_key)
            events.append(
                operation_id,
                code="STEP_VERIFIED",
                summary=f"step {step.step_key} postcondition verified",
            )

    def _advance_step(
        self,
        operation_id: str,
        workflow: WorkflowDefinition,
        decoded: Any,
        step: StepDefinition,
        row: Any,
        held: dict[str, int],
        effected: list[StepDefinition],
    ) -> "ExecutionOutcome | list[StepDefinition]":
        prior_outputs = {
            s.step_key: json.loads(s.output_json)
            for s in ()
        }  # replaced below by durable read
        with self.units.begin() as conn:
            steps_repo = StepRepository(conn, clock=self.clock)
            collected: dict[str, Any] = {}
            for done in steps_repo.list(operation_id):
                if done.output_json:
                    collected[done.step_key] = json.loads(done.output_json)
        prior_outputs = collected

        if row.state is StepState.VERIFIED:  # defensive; loop filters these
            return effected

        if row.state is StepState.CHECKPOINTED:
            # §8.7: repeat ONLY verification.
            ctx = EffectContext(
                operation_id=operation_id,
                step_key=step.step_key,
                external_effect_id=row.external_effect_id or "",
                inputs=_row_inputs(row),
                prior_outputs=prior_outputs,
                request=decoded,
            )
            step.verify(ctx)
            self._verify_transaction(operation_id, step, held)
            return effected

        reclaim = row.state is StepState.RUNNING
        ctx = self._intent_transaction(
            operation_id,
            step,
            decoded,
            held,
            reclaim=reclaim,
            prior_outputs=prior_outputs,
        )

        if reclaim:
            self._crash(step.step_key, "before_probe")
            probe = step.probe(ctx)
            self._crash(step.step_key, "after_probe")
            decision = decide_recovery(
                probe.classification,
                operation_state=OperationState.RUNNING,
                safe_if_uncertain=step.safe_if_uncertain,
            )
            with self.units.begin() as conn:
                ops, _s, leases, events = self._repos(conn)
                ops.require(operation_id)
                self._fence(leases, operation_id, held)
                events.append(
                    operation_id,
                    code="RECOVERY_DECISION",
                    summary=(
                        f"{step.step_key}: {probe.reason_code} -> "
                        f"{decision.action.value}"
                    ),
                    detail={"classification": decision.classification.value},
                )
            return self._apply_recovery_decision(
                operation_id, workflow, decoded, step, ctx,
                probe, decision, held, effected,
            )

        self._crash(step.step_key, "after_step_start")
        return self._execute_effect(
            operation_id, step, ctx, held, effected,
            workflow=workflow, decoded=decoded,
        )

    # -- effect / failure / cancellation ------------------------------------------

    def _enter_critical(self, operation_id: str, held: dict[str, int]) -> bool:
        """CAS into the critical section immediately before a critical effect.

        Returns False when a durable cancellation won the race instead; the
        caller must then honor the cancellation without any effect.
        """
        with self.units.begin() as conn:
            ops, _s, leases, events = self._repos(conn)
            record = ops.require(operation_id)
            self._fence(leases, operation_id, held)
            if record.state is OperationState.COMMITTING:
                return True  # resumed inside the section (reclaim path)
            if record.state is OperationState.CANCEL_REQUESTED:
                return False
            ops.compare_and_transition(
                operation_id,
                expected_state=record.state,
                expected_revision=record.state_revision,
                target_state=OperationState.COMMITTING,
                event_code="ENTERING_CRITICAL",
                event_summary="critical step begins; cancellation deferred",
            )
        return True

    def _exit_critical(self, operation_id: str, held: dict[str, int]) -> None:
        """After a critical step's verification commits, cycle to VERIFYING."""
        with self.units.begin() as conn:
            ops, _s, leases, events = self._repos(conn)
            record = ops.require(operation_id)
            self._fence(leases, operation_id, held)
            if record.state is not OperationState.COMMITTING:
                raise LostLease(
                    f"{operation_id} expected COMMITTING after critical "
                    f"verification, found {record.state.value}"
                )
            ops.compare_and_transition(
                operation_id,
                expected_state=OperationState.COMMITTING,
                expected_revision=record.state_revision,
                target_state=OperationState.VERIFYING,
                event_code="CRITICAL_STEP_RESOLVED",
                event_summary="critical step verified; leaving critical section",
            )

    def _execute_effect(
        self,
        operation_id: str,
        step: StepDefinition,
        ctx: EffectContext,
        held: dict[str, int],
        effected: list[StepDefinition],
        *,
        workflow: WorkflowDefinition | None = None,
        decoded: Any = None,
    ) -> "ExecutionOutcome | list[StepDefinition]":
        cancel_won = False
        if step.critical and not self._enter_critical(operation_id, held):
            cancel_won = True
        if cancel_won:
            return self._honor_cancellation(
                operation_id,
                workflow=workflow,
                decoded=decoded,
                held=held,
                effected=effected,
            )
        try:
            output = step.execute(ctx)
            self._crash(step.step_key, "before_step_checkpoint")
            import json as _json

            self._checkpoint_transaction(
                operation_id,
                step,
                held,
                ctx,
                _json.loads(sanitize_payload(output or {})),
                recovered=False,
            )
            self._crash(step.step_key, "after_step_checkpoint")
            self._crash(step.step_key, "before_step_verification")
            step.verify(ctx)
            self._crash(step.step_key, "after_step_verification")
            self._verify_transaction(operation_id, step, held)
            if step.critical:
                self._exit_critical(operation_id, held)
        except LostLease:
            raise
        except Exception as exc:  # BaseException (process death) propagates
            return self._handle_failure(
                operation_id, workflow=workflow, step=step, ctx=ctx,
                held=held, effected=effected,
                error=exc,
            )
        if step.externally_visible:
            effected.append(step)
        return effected

    def _apply_recovery_decision(
        self,
        operation_id: str,
        workflow: WorkflowDefinition,
        decoded: Any,
        step: StepDefinition,
        ctx: EffectContext,
        probe: Any,
        decision: Any,
        held: dict[str, int],
        effected: list[StepDefinition],
    ) -> "ExecutionOutcome | list[StepDefinition]":
        if decision.action is RecoveryAction.VERIFY:
            recovered = probe.output or {}
            self._checkpoint_transaction(
                operation_id, step, held, ctx, recovered, recovered=True
            )
            step.verify(ctx)
            self._verify_transaction(operation_id, step, held)
            if step.externally_visible:
                effected.append(step)
            return effected
        if decision.action in (
            RecoveryAction.EXECUTE,
            RecoveryAction.RESUME,
            RecoveryAction.DISCARD_AND_RETRY,
        ):
            cancel_won = False
            if step.critical and not self._enter_critical(operation_id, held):
                cancel_won = True
            if cancel_won:
                return self._honor_cancellation(
                    operation_id,
                    workflow=workflow,
                    decoded=decoded,
                    held=held,
                    effected=effected,
                )
            try:
                output = step.execute(ctx)
                import json as _json

                self._checkpoint_transaction(
                    operation_id,
                    step,
                    held,
                    ctx,
                    _json.loads(sanitize_payload(output or {})),
                    recovered=False,
                )
                step.verify(ctx)
                self._verify_transaction(operation_id, step, held)
                if step.critical:
                    self._exit_critical(operation_id, held)
            except LostLease:
                raise
            except Exception as exc:
                return self._handle_failure(
                    operation_id, workflow=workflow, step=step, ctx=ctx,
                    held=held, effected=effected, error=exc,
                )
            if step.externally_visible:
                effected.append(step)
            return effected
        if decision.action is RecoveryAction.ROLL_BACK:
            with self.units.begin() as conn:
                ops, _s, leases, events = self._repos(conn)
                record = ops.require(operation_id)
                self._fence(leases, operation_id, held)
                ops.compare_and_transition(
                    operation_id,
                    expected_state=record.state,
                    expected_revision=record.state_revision,
                    target_state=OperationState.ROLLING_BACK,
                    event_code="ROLLBACK_STARTED",
                    event_summary=f"revertible interruption of {step.step_key}",
                )
            return self._compensate_and_finalize(
                operation_id,
                workflow=workflow,
                decoded=decoded,
                held=held,
                reason_code="REVERTIBLE_INTERRUPTION",
                cancelled=False,
            )
        if decision.action is RecoveryAction.PAUSE:
            return self._pause(operation_id, held, decision.reason_code)
        # REQUIRE_RECOVERY: transition, keep lease rows as durable barriers.
        with self.units.begin() as conn:
            ops, _s, leases, events = self._repos(conn)
            record = ops.require(operation_id)
            del record
            self._fence(leases, operation_id, held)
            ops.record_terminal_result(
                operation_id,
                terminal_state=OperationState.RECOVERY_REQUIRED,
                error_code=decision.reason_code,
                error_detail={
                    "step": step.step_key,
                    "classification": decision.classification.value,
                },
                event_summary=(
                    f"uncertain external state at {step.step_key}; barrier held"
                ),
            )
        return ExecutionOutcome(
            "RECOVERY_REQUIRED_OUTCOME",
            operation_id,
            reason_code=decision.reason_code,
        )

    def _durable_compensation_set(
        self, operation_id: str, workflow: WorkflowDefinition | None
    ) -> "list[tuple[StepDefinition, Any]]":
        """§3.3: reconstruct effected steps from DURABLE rows, never memory."""
        with self.units.begin() as conn:
            steps_repo = StepRepository(conn, clock=self.clock)
            rows = {s.step_key: s for s in steps_repo.list(operation_id)}
        if workflow is None:
            return []
        compensable = []
        for step in workflow.steps:
            row = rows.get(step.step_key)
            if row is None or step.compensate is None:
                continue
            if not step.externally_visible and not step.critical:
                continue
            if row.state in (
                StepState.RUNNING,
                StepState.CHECKPOINTED,
                StepState.VERIFIED,
                StepState.COMPENSATING,
            ):
                compensable.append((step, row))
        return compensable

    def _prior_outputs(self, operation_id: str) -> dict[str, Any]:
        with self.units.begin() as conn:
            steps_repo = StepRepository(conn, clock=self.clock)
            collected: dict[str, Any] = {}
            for done in steps_repo.list(operation_id):
                if done.output_json:
                    collected[done.step_key] = json.loads(done.output_json)
        return collected

    def _handle_failure(
        self,
        operation_id: str,
        *,
        workflow: WorkflowDefinition | None,
        step: StepDefinition,
        ctx: EffectContext,
        held: dict[str, int],
        effected: list[StepDefinition],
        error: Exception,
    ) -> "ExecutionOutcome | list[StepDefinition]":
        """Classify a normal Exception: safe failure vs compensation.

        Raw exception text is never persisted — only the class name and a
        bounded generic summary.
        """
        probe = step.probe(ctx)
        # Durable reconstruction (§3.3) EXCLUDING the failing step itself:
        # an intent that never mutated (probe ABSENT) is not evidence.
        durable_pairs = [
            (s, r)
            for (s, r) in self._durable_compensation_set(operation_id, workflow)
            if s.step_key != step.step_key
        ]
        mutation_happened = (
            probe.classification
            not in (RecoveryClass.ABSENT, RecoveryClass.DISCARDABLE)
            or bool(durable_pairs)
        )
        if mutation_happened:
            with self.units.begin() as conn:
                ops, _s, leases, events = self._repos(conn)
                record = ops.require(operation_id)
                self._fence(leases, operation_id, held)
                ops.compare_and_transition(
                    operation_id,
                    expected_state=record.state,
                    expected_revision=record.state_revision,
                    target_state=OperationState.ROLLING_BACK,
                    event_level="warn",
                    event_code="ROLLBACK_STARTED",
                    event_summary=f"failure at {step.step_key}; compensating",
                )
            return self._compensate_and_finalize(
                operation_id,
                workflow=workflow,
                decoded=ctx.request,
                held=held,
                reason_code=f"FAILED_AT_{step.step_key.upper()}",
                cancelled=False,
                exception_class=type(error).__name__,
                also=[step],
            )
        return self._fail_safe(operation_id, held, step, error)

    def _fail_safe(
        self,
        operation_id: str,
        held: dict[str, int],
        step: "StepDefinition | str",
        error: Exception,
    ) -> ExecutionOutcome:
        step_label = (
            step.step_key if isinstance(step, StepDefinition) else str(step)
        )
        with self.units.begin() as conn:
            ops, _s, leases, _e = self._repos(conn)
            record = ops.require(operation_id)
            del record
            self._fence(leases, operation_id, held)
            ops.record_terminal_result(
                operation_id,
                terminal_state=OperationState.FAILED_SAFE,
                error_code="STEP_FAILED_SAFE",
                error_detail={
                    "step": step_label,
                    "exception_class": type(error).__name__,
                    "probe": "absent",
                },
                event_summary=(
                    f"failed before any visible mutation ({type(error).__name__})"
                ),
            )
            for key in sorted(held):
                leases.release(
                    key, owner=self.worker_id, expected_revision=held[key]
                )
        return ExecutionOutcome("COMPLETED", operation_id, reason_code="FAILED_SAFE")

    def _compensate_and_finalize(
        self,
        operation_id: str,
        *,
        workflow: WorkflowDefinition | None = None,
        decoded: Any = None,
        held: dict[str, int],
        reason_code: str,
        cancelled: bool,
        exception_class: str | None = None,
        also: "list[StepDefinition] | None" = None,
    ) -> ExecutionOutcome:
        """§3.2/§9: durable reverse compensation with restoration probes.

        The compensation set is reconstructed from durable ``operation_steps``
        rows, never from this worker's memory. An interrupted COMPENSATING
        step probes its restoration postcondition first: COMPLETE checkpoints
        without a second effect; ABSENT/REVERTIBLE re-runs the idempotent
        effect with the SAME external-effect id; UNCERTAIN_MANUAL enters
        RECOVERY_REQUIRED retaining leases.
        """
        compensable = self._durable_compensation_set(operation_id, workflow)
        if also:
            known = {s.step_key for s, _r in compensable}
            with self.units.begin() as conn:
                steps_repo = StepRepository(conn, clock=self.clock)
                extra = []
                for step in also:
                    if step.step_key in known or step.compensate is None:
                        continue
                    row = steps_repo.get(operation_id, step.step_key)
                    if row is not None and row.state in (
                        StepState.RUNNING,
                        StepState.CHECKPOINTED,
                        StepState.VERIFIED,
                        StepState.COMPENSATING,
                    ):
                        extra.append((step, row))
            compensable = compensable + extra
        prior_outputs = self._prior_outputs(operation_id)
        compensation_failures: list[str] = []
        uncertain = False
        for step, row in reversed(compensable):
            try:
                ctx = EffectContext(
                    operation_id=operation_id,
                    step_key=step.step_key,
                    external_effect_id=row.external_effect_id or "",
                    inputs=_row_inputs(row),
                    prior_outputs=prior_outputs,
                    request=decoded,
                )
                assert step.compensate is not None
                if row.state is StepState.COMPENSATING:
                    # Restoration probe decides before any repeat effect.
                    self._crash(step.step_key, "before_probe_restoration")
                    if step.probe_restoration is not None:
                        result = step.probe_restoration(ctx)
                        if (
                            result.classification
                            is RecoveryClass.UNCERTAIN_MANUAL
                        ):
                            uncertain = True
                            break
                        if result.classification is RecoveryClass.COMPLETE:
                            with self.units.begin() as conn:
                                ops, steps_repo, leases, events = (
                                    self._repos(conn)
                                )
                                ops.require(operation_id)
                                self._fence(leases, operation_id, held)
                                steps_repo.complete_compensation(
                                    operation_id, step.step_key
                                )
                                events.append(
                                    operation_id,
                                    code="COMPENSATION_COMPLETED",
                                    summary=(
                                        f"restoration already proven for "
                                        f"{step.step_key}; checkpointed"
                                    ),
                                )
                            continue
                        # ABSENT/REVERTIBLE/PARTIALLY_RESUMABLE: re-run below.
                    with self.units.begin() as conn:
                        ops, steps_repo, leases, events = self._repos(conn)
                        ops.require(operation_id)
                        self._fence(leases, operation_id, held)
                        events.append(
                            operation_id,
                            code="COMPENSATION_RECLAIMED",
                            summary=f"resuming compensation of {step.step_key}",
                        )
                else:
                    with self.units.begin() as conn:
                        ops, steps_repo, leases, events = self._repos(conn)
                        ops.require(operation_id)
                        self._fence(leases, operation_id, held)
                        steps_repo.begin_compensation(
                            operation_id,
                            step.step_key,
                            expected_state=row.state.value,
                        )
                        events.append(
                            operation_id,
                            code="COMPENSATION_STARTED",
                            summary=f"compensating {step.step_key}",
                        )
                step.compensate(ctx)
                self._crash(step.step_key, "after_compensation_effect")
                if step.verify_restoration is not None:
                    step.verify_restoration(ctx)
                self._crash(step.step_key, "before_compensation_checkpoint")
                with self.units.begin() as conn:
                    ops, steps_repo, leases, events = self._repos(conn)
                    ops.require(operation_id)
                    self._fence(leases, operation_id, held)
                    steps_repo.complete_compensation(operation_id, step.step_key)
                    events.append(
                        operation_id,
                        code="COMPENSATION_COMPLETED",
                        summary=f"compensated {step.step_key}",
                    )
            except Exception as exc:
                compensation_failures.append(type(exc).__name__)

        terminal = (
            OperationState.RECOVERY_REQUIRED
            if (compensation_failures or uncertain)
            else (
                OperationState.CANCELLED
                if cancelled
                else OperationState.FAILED_ROLLED_BACK
            )
        )
        with self.units.begin() as conn:
            ops, _s, leases, _e = self._repos(conn)
            record = ops.require(operation_id)
            self._fence(leases, operation_id, held)
            self._crash("operation", "before_terminal_transition")
            ops.record_terminal_result(
                operation_id,
                terminal_state=terminal,
                result_code=(
                    reason_code
                    if terminal is not OperationState.RECOVERY_REQUIRED
                    else None
                ),
                error_code=(
                    reason_code
                    if terminal is OperationState.RECOVERY_REQUIRED
                    else None
                ),
                error_detail={
                    "compensation_failures": compensation_failures,
                    **(
                        {"exception_class": exception_class}
                        if exception_class
                        else {}
                    ),
                },
                event_summary=f"operation reached {terminal.value}",
            )
            if terminal is not OperationState.RECOVERY_REQUIRED:
                for key in sorted(held):
                    leases.release(
                        key, owner=self.worker_id, expected_revision=held[key]
                    )
        kind = (
            "RECOVERY_REQUIRED_OUTCOME"
            if terminal is OperationState.RECOVERY_REQUIRED
            else "COMPLETED"
        )
        return ExecutionOutcome(kind, operation_id, reason_code=terminal.value)

    def _honor_cancellation(
        self,
        operation_id: str,
        *,
        workflow: WorkflowDefinition,
        decoded: Any,
        held: dict[str, int],
        effected: list[StepDefinition],
    ) -> ExecutionOutcome:
        del effected
        # Mutation evidence comes from DURABLE step rows, never from this
        # worker's memory (§3.3): after process death the new worker still
        # sees what the previous worker effected.
        durable_effected = self._durable_compensation_set(
            operation_id, workflow
        )
        if durable_effected:
            return self._compensate_and_finalize(
                operation_id,
                workflow=workflow,
                decoded=decoded,
                held=held,
                reason_code="CANCELLED_AFTER_COMPENSATION",
                cancelled=True,
            )
        with self.units.begin() as conn:
            ops, _s, leases, _e = self._repos(conn)
            record = ops.require(operation_id)
            del record
            self._fence(leases, operation_id, held)
            ops.record_terminal_result(
                operation_id,
                terminal_state=OperationState.CANCELLED,
                result_code="CANCELLED_NO_EFFECT",
                event_summary="cancelled with no externally visible mutation",
            )
            for key in sorted(held):
                leases.release(
                    key, owner=self.worker_id, expected_revision=held[key]
                )
        return ExecutionOutcome("COMPLETED", operation_id, reason_code="CANCELLED")

    def _pause(
        self, operation_id: str, held: dict[str, int], reason_code: str
    ) -> ExecutionOutcome:
        with self.units.begin() as conn:
            ops, _s, leases, events = self._repos(conn)
            record = ops.require(operation_id)
            self._fence(leases, operation_id, held)
            ops.compare_and_transition(
                operation_id,
                expected_state=record.state,
                expected_revision=record.state_revision,
                target_state=OperationState.PAUSED,
                event_code="OPERATION_PAUSED",
                event_summary=f"paused safely: {reason_code}",
            )
            for key in sorted(held):
                leases.release(
                    key, owner=self.worker_id, expected_revision=held[key]
                )
        return ExecutionOutcome("PAUSED", operation_id, reason_code=reason_code)

    def _complete(self, operation_id: str, held: dict[str, int]) -> ExecutionOutcome:
        with self.units.begin() as conn:
            ops, _s, leases, _e = self._repos(conn)
            record = ops.require(operation_id)
            self._fence(leases, operation_id, held)
            # Success enters the critical section before the terminal.
            if record.state is not OperationState.COMMITTING:
                ops.compare_and_transition(
                    operation_id,
                    expected_state=record.state,
                    expected_revision=record.state_revision,
                    target_state=OperationState.COMMITTING,
                    event_code="ENTERING_COMMIT",
                    event_summary="all steps verified; entering critical section",
                )
                record = ops.require(operation_id)
            ops.record_terminal_result(
                operation_id,
                terminal_state=OperationState.SUCCEEDED,
                result_code="ALL_STEPS_VERIFIED",
                event_summary="all steps verified; operation succeeded",
            )
            for key in sorted(held):
                leases.release(
                    key, owner=self.worker_id, expected_revision=held[key]
                )
        return ExecutionOutcome("COMPLETED", operation_id, reason_code="SUCCEEDED")