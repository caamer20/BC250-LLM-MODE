"""Foreground runtime lifecycle command (U1.2 §15.1).

``RuntimeLifecycleCommandService`` is the ONE entry every frontend
reaches for llama.cpp update / rollback / status / resume: it refuses-
or-resumes conflicting runtime operations according to DURABLE state,
enqueues through the ONE shared ``EnqueueService``, drives ONE operation
via the shared engine factory in the FOREGROUND, and maps the durable
terminal row to a typed result. It never starts a worker/thread and
never performs host effects itself.

If the frontend exits mid-operation the operation stays durably paused/
interrupted for explicit resume — this is reported honestly; nothing
continues in the background until U1.3.
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Any

from .operations.engine import ExecutionOutcome
from .operations.model import (
    OperationState,
    OperationType,
)
from .operations.repositories import (
    LeaseRepository,
    OperationRepository,
)
from .operations.runtime_lifecycle import (
    RUNTIME_ACTIVE_RESOURCE,
    RUNTIME_INSTALLATION_RESOURCE,
)


@dataclass(frozen=True)
class RuntimeLifecycleOutcome:
    """Typed terminal result; frontends render text from stable codes."""

    operation_id: str | None
    status: str  # SUCCEEDED | FAILED_ROLLED_BACK | FAILED_SAFE |
    # CANCELLED | RECOVERY_REQUIRED | BUSY | PAUSED
    action: str  # UPDATE | ROLLBACK
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "SUCCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status,
            "operation_id": self.operation_id,
            **self.detail,
        }


_STATUS_MAP = {
    OperationState.SUCCEEDED: "SUCCEEDED",
    OperationState.FAILED_ROLLED_BACK: "FAILED_ROLLED_BACK",
    OperationState.FAILED_SAFE: "FAILED_SAFE",
    OperationState.CANCELLED: "CANCELLED",
    OperationState.RECOVERY_REQUIRED: "RECOVERY_REQUIRED",
}


class RuntimeLifecycleCommandService:
    """Enqueue + foreground execute + terminal mapping (no detach)."""

    def __init__(self, *, units: Any, enqueue: Any,
                 engine_factory: Any) -> None:
        self._units = units
        self._enqueue = enqueue
        self._engine_factory = engine_factory

    # -- durable-state inspection ---------------------------------------------

    def _scan_existing(self):
        with self._units.begin() as conn:
            ops = OperationRepository(conn)
            leases = LeaseRepository(conn)
            for resource in (RUNTIME_ACTIVE_RESOURCE,
                             RUNTIME_INSTALLATION_RESOURCE):
                lease = leases.get(resource)
                if lease is not None:
                    owner = ops.get(lease.operation_id)
                    if (
                        owner is not None
                        and owner.operation_type in (
                            OperationType.RUNTIME_UPDATE,
                            OperationType.RUNTIME_ROLLBACK,
                        )
                        and owner.state is OperationState.RECOVERY_REQUIRED
                    ):
                        return ("barrier", lease.operation_id)
            for record in ops.list_active():
                if record.operation_type not in (
                    OperationType.RUNTIME_UPDATE,
                    OperationType.RUNTIME_ROLLBACK,
                ):
                    continue
                from .legacy_import import utcnow

                expired = False
                for resource in (RUNTIME_ACTIVE_RESOURCE,
                                 RUNTIME_INSTALLATION_RESOURCE):
                    lease = leases.get(resource)
                    if (
                        lease is not None
                        and lease.operation_id == record.id
                        and lease.expires_at <= utcnow()
                    ):
                        expired = True
                return ("active", record.id, expired)
            return ("none",)

    # -- public API ---------------------------------------------------------------

    def update(
        self,
        *,
        requested_ref: str | None = None,
        expected_active_build_id: str | None = None,
        requested_by: str = "cli",
        detach: bool = False,
        spawner: Any | None = None,
    ) -> RuntimeLifecycleOutcome:
        payload: dict[str, Any] = {"requested_by": requested_by}
        if requested_ref is not None:
            payload["requested_ref"] = requested_ref
        if expected_active_build_id is not None:
            payload["expected_active_build_id"] = expected_active_build_id
        found = self._scan_existing()
        blocked = self._blocked(found)
        if blocked is not None:
            return blocked
        record = self._enqueue.enqueue(
            operation_type=OperationType.RUNTIME_UPDATE,
            payload=payload,
            surface=requested_by,
        )
        if detach:
            return self._detach(record.id, action="UPDATE", spawner=spawner)
        outcome = self._drive(record.id)
        return self._map(record.id, outcome, action="UPDATE")

    def rollback(self, *, requested_by: str = "cli") -> RuntimeLifecycleOutcome:
        selection = self._select_rollback_target()
        if selection is None:
            return RuntimeLifecycleOutcome(
                None, "BUSY", "ROLLBACK",
                {"reason":
                 "No verified prior runtime is retained; nothing to roll "
                 "back to yet."},
            )
        target, expected = selection
        payload = {
            "requested_by": requested_by,
            "target_build_id": target,
            "expected_active_build_id": expected,
        }
        found = self._scan_existing()
        blocked = self._blocked(found)
        if blocked is not None:
            return blocked
        record = self._enqueue.enqueue(
            operation_type=OperationType.RUNTIME_ROLLBACK,
            payload=payload,
            surface=requested_by,
        )
        outcome = self._drive(record.id)
        return self._map(record.id, outcome, action="ROLLBACK")

    def _detach(self, operation_id: str, *, action: str,
                spawner: Any | None) -> RuntimeLifecycleOutcome:
        """U1.3: hand the queued operation to ONE detached worker host."""
        from .worker_service import spawn_detached

        try:
            pid = spawn_detached(spawner=spawner)
        except Exception as exc:  # noqa: BLE001 - typed mapping only
            return RuntimeLifecycleOutcome(
                operation_id, "BUSY", action,
                {"reason": f"worker spawn failed ({exc.__class__.__name__})",
                 "foreground_only": True},
            )
        return RuntimeLifecycleOutcome(
            operation_id, "DETACHED", action,
            {"pid": pid, "continues_after_close": True},
        )

    def resume(self, operation_id: str) -> RuntimeLifecycleOutcome:
        """Explicit foreground resume of an interrupted operation."""
        outcome = self._drive(operation_id)
        return self._map(operation_id, outcome, action="RESUME")

    def _drive(self, operation_id: str, *, max_resumes: int = 8):
        """Foreground execution honoring Ctrl-C as DURABLE cancellation.

        §15.3: the first Ctrl-C requests cancellation through the durable
        row and KEEPS DRIVING with the SAME worker identity — the engine
        honors the request at safe checkpoints and defers inside
        swap/restart/compensation, so nothing is ever killed mid-effect.
        A second Ctrl-C (or an exhausted resume budget) re-raises; the CLI
        then reports the paused, resumable operation honestly.
        """
        with self._units.read() as conn:
            row = OperationRepository(conn).require(operation_id)
        if row.state in (
            OperationState.SUCCEEDED,
            OperationState.CANCELLED,
            OperationState.FAILED_SAFE,
            OperationState.FAILED_ROLLED_BACK,
            OperationState.RECOVERY_REQUIRED,
        ):
            # Terminal rows need no engine pass; mapping reads durable truth.
            return ExecutionOutcome("SKIPPED_TERMINAL", operation_id)

        engine = self._engine_factory()
        interrupts = 0
        resumes = 0
        while True:
            try:
                return engine.execute_one(operation_id)
            except KeyboardInterrupt:
                interrupts += 1
                if interrupts > 1 or resumes >= max_resumes:
                    raise
                resumes += 1
                with self._units.begin() as conn:
                    OperationRepository(conn).request_cancel(operation_id)

    def status(self) -> dict[str, Any]:
        """Read-only lineage/progress snapshot; never mutates anything."""
        with self._units.read() as conn:
            from .repositories import KnownGoodRuntimeRepository
            from .runtime_builds import (
                RuntimeBuildRepository,
                RuntimeComponentRepository,
                RuntimeTreeRepository,
            )

            components = RuntimeComponentRepository(conn)
            builds = RuntimeBuildRepository(conn)
            trees = RuntimeTreeRepository(conn)
            component = components.current()
            result: dict[str, Any] = {
                "generation": (component or {}).get("generation"),
                "promoted": None,
                "rollback": None,
                "known_good_identity": (
                    KnownGoodRuntimeRepository(conn).get()
                    or {}
                ).get("runtime_component_identity"),
                "recovery_barrier": None,
                "active_operation": None,
            }
            for key in ("promoted", "rollback"):
                build_id = (component or {}).get(f"{key}_build_id")
                if not build_id:
                    continue
                try:
                    record = builds.require(build_id)
                except Exception:  # noqa: BLE001 - status never raises
                    record = {}
                tree_row = None
                tree_id = (component or {}).get(f"{key}_tree_id")
                if tree_id:
                    tree_row = trees.get(tree_id)
                result[key] = {
                    "build_id": build_id,
                    "short": build_id.rsplit(":", 1)[-1][:12],
                    "requested_ref": record.get("requested_ref"),
                    "provenance_class": record.get("provenance_class"),
                    "source_commit": record.get("source_commit"),
                    "locator": (tree_row or {}).get("locator"),
                }
            result["rollback_available"] = bool(result["rollback"])
            leases = LeaseRepository(conn)
            ops = OperationRepository(conn)
            for resource in (RUNTIME_ACTIVE_RESOURCE,
                             RUNTIME_INSTALLATION_RESOURCE):
                lease = leases.get(resource)
                if lease is None:
                    continue
                owner = ops.get(lease.operation_id)
                if owner is not None \
                        and owner.state is OperationState.RECOVERY_REQUIRED \
                        and owner.operation_type in (
                            OperationType.RUNTIME_UPDATE,
                            OperationType.RUNTIME_ROLLBACK,
                        ):
                    result["recovery_barrier"] = {
                        "operation_id": owner.id,
                        "error_code": owner.error_code,
                    }
            active_ops = [
                r for r in ops.list_active()
                if r.operation_type in (
                    OperationType.RUNTIME_UPDATE,
                    OperationType.RUNTIME_ROLLBACK,
                )
            ]
            if active_ops:
                latest = active_ops[-1]
                result["active_operation"] = {
                    "operation_id": latest.id,
                    "type": latest.operation_type.value,
                    "state": latest.state.value,
                    "phase": latest.progress_phase,
                    "current": latest.progress_current,
                    "total": latest.progress_total,
                    "foreground_only": True,
                }
            return result

    # -- helpers --------------------------------------------------------------------

    def _select_rollback_target(self) -> tuple[str, str] | None:
        with self._units.read() as conn:
            component = RuntimeComponentRepository(conn).current()
        if not component:
            return None
        target = component.get("rollback_build_id")
        expected = component.get("promoted_build_id")
        if not target or not expected:
            return None
        return target, expected

    def _blocked(self, found) -> RuntimeLifecycleOutcome | None:
        if found[0] == "barrier":
            return RuntimeLifecycleOutcome(
                found[1], "RECOVERY_REQUIRED", "BLOCKED",
                {"reason":
                 "A previous runtime operation needs repair; resolve the "
                 "recovery barrier before starting another."},
            )
        if found[0] == "active":
            operation_id, expired = found[1], found[2]
            if expired:
                outcome = self.resume(operation_id)
                return RuntimeLifecycleOutcome(
                    outcome.operation_id, outcome.status, "RESUME",
                    outcome.detail,
                )
            return RuntimeLifecycleOutcome(
                operation_id, "BUSY", "BLOCKED",
                {"reason": "A runtime operation is already running."},
            )
        return None

    def _map(self, operation_id: str, outcome: Any,
             *, action: str) -> RuntimeLifecycleOutcome:
        if outcome.kind == "SKIPPED_BUSY":
            return RuntimeLifecycleOutcome(
                operation_id, "BUSY", action,
                {"reason": "resource held"},
            )
        if outcome.kind == "LOST_LEASE":
            return RuntimeLifecycleOutcome(
                operation_id, "RECOVERY_REQUIRED", action,
                {"reason": "lease lost mid-run; inspect before retrying."},
            )
        with self._units.begin() as conn:
            record = OperationRepository(conn).require(operation_id)
        state = record.state
        detail: dict[str, Any] = {
            "result_code": record.result_code,
            "error_code": record.error_code,
            "foreground_only": True,
        }
        mapping = dict(_STATUS_MAP)
        status = mapping.get(state)
        if status is None:
            return RuntimeLifecycleOutcome(
                operation_id,
                "BUSY" if state is OperationState.PAUSED else "RECOVERY_REQUIRED",
                action,
                {**detail,
                 "reason": f"durable state {state.value}"},
            )
        if status == "SUCCEEDED" and record.result_code == "RUNTIME_ALREADY_ACTIVE":
            detail["already_active"] = True
        elif status == "FAILED_ROLLED_BACK":
            detail["reason"] = (
                "The runtime change failed; the previous working runtime "
                "was restored and verified."
            )
        elif status == "RECOVERY_REQUIRED":
            detail["reason"] = (
                "The runtime operation could not be proven safe; repair is "
                "required. Every potentially useful tree was retained."
            )
            # §11.5: surface the persisted remediation evidence verbatim
            # (step / classification / probe) so users can act on it.
            # Callers may pass either a mapping (engine path) or
            # pre-serialized JSON (tests/tools) — decode defensively.
            remediation = record.error_detail or "{}"
            try:
                for _ in range(2):
                    remediation = json.loads(remediation)
                    if not isinstance(remediation, str):
                        break
            except ValueError:
                remediation = {}
            if not isinstance(remediation, dict):
                remediation = {}
            if isinstance(remediation, dict) and remediation:
                detail["remediation"] = {
                    "step": remediation.get("step"),
                    "classification": remediation.get("classification"),
                    "probe": remediation.get("probe"),
                }
        return RuntimeLifecycleOutcome(operation_id, status, action, detail)
