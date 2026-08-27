"""Foreground model-remove command (P6 §12.2).

``ModelRemoveCommandService`` is the ONE entry every frontend reaches for
model removal. ``dry_run`` is a query-only impact summary (accurate, with
blockers); ``remove`` refuses-or-enqueues according to durable truth and
drives ONE ``MODEL_REMOVE v1`` operation in the current process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .operations.model import OperationState, OperationType
from .operations.repositories import LeaseRepository, OperationRepository


@dataclass(frozen=True)
class RemoveOutcome:
    operation_id: str | None
    status: str  # REMOVED | RETAINED | BLOCKED | BUSY | FAILED_SAFE |
    # RECOVERY_REQUIRED | PAUSED | UNKNOWN_ALIAS
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("REMOVED", "RETAINED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation_id": self.operation_id,
            **self.detail,
        }


_STATUS_MAP = {
    "MODEL_REMOVED": "REMOVED",
    "QUARANTINE_RETAINED": "RETAINED",
    "REMOVAL_BLOCKED": "BLOCKED",
    "ALIAS_UNKNOWN": "UNKNOWN_ALIAS",
}


class ModelRemoveCommandService:
    def __init__(
        self,
        *,
        units: Any,
        enqueue: Any,
        engine_factory: Callable[[], Any],
    ) -> None:
        self._units = units
        self._enqueue = enqueue
        self._engine_factory = engine_factory

    # --- dry run (query-only) ---------------------------------------------

    def dry_run(self, alias: str) -> dict[str, Any]:
        """Accurate impact summary + blockers; never mutates (P6 §12.2.3)."""
        with self._units.read() as conn:
            row = conn.execute(
                "SELECT i.alias AS alias, i.path AS path, "
                "i.artifact_id AS artifact_id, "
                "a.content_digest AS content_digest, "
                "a.byte_size AS byte_size "
                "FROM model_installations i "
                "LEFT JOIN model_artifacts a ON a.id = i.artifact_id "
                "WHERE i.alias = ?",
                (alias,),
            ).fetchone()
            if row is None:
                return {
                    "alias": alias,
                    "found": False,
                    "blockers": ["alias-unknown"],
                    "removable": False,
                    "impact": {},
                }
            blockers: list[str] = []
            kg = conn.execute(
                "SELECT model_alias FROM known_good_runtime WHERE id = 1"
            ).fetchone()
            if kg is not None and kg["model_alias"] == alias:
                blockers.append("active-known-good-model")
            artifact_id = row["artifact_id"]
            other_aliases = 0
            if artifact_id:
                refs = conn.execute(
                    "SELECT COUNT(*) AS n FROM model_installations "
                    "WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
                other_aliases = max(0, (refs["n"] if refs else 1) - 1)
                if other_aliases > 0:
                    blockers.append("artifact-referenced-by-other-aliases")
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM operations WHERE state IN "
                "('QUEUED', 'PREPARING', 'RUNNING', 'PAUSED', "
                "'RECOVERY_REQUIRED') AND operation_type != 'MODEL_REMOVE'"
            ).fetchone()
            if pending is not None and pending["n"] > 0:
                blockers.append("pending-operations-in-flight")
            bytes_freed = 0 if other_aliases > 0 else (row["byte_size"] or 0)
            return {
                "alias": alias,
                "found": True,
                "artifact_id": artifact_id,
                "content_digest": row["content_digest"],
                "blockers": blockers,
                "removable": not blockers,
                "impact": {
                    "aliases_removed": 1,
                    "other_aliases_sharing_artifact": other_aliases,
                    "bytes_to_quarantine": bytes_freed,
                    "bytes_deleted": 0,
                    "disposition": "quarantine" if not blockers else "refused",
                },
            }

    # --- remove ------------------------------------------------------------

    def _scan_existing(self, alias: str):
        with self._units.begin() as conn:
            ops = OperationRepository(conn)
            leases = LeaseRepository(conn)
            lease = leases.get("model-storage")
            if lease is not None:
                owner = ops.get(lease.operation_id)
                if (
                    owner is not None
                    and owner.operation_type is OperationType.MODEL_REMOVE
                    and owner.state is OperationState.RECOVERY_REQUIRED
                ):
                    return ("barrier", lease.operation_id)
            for record in ops.list_active():
                if record.operation_type is not OperationType.MODEL_REMOVE:
                    continue
                from .legacy_import import utcnow

                expired = bool(
                    lease is not None
                    and lease.operation_id == record.id
                    and lease.expires_at <= utcnow()
                )
                return ("active", record.id, expired)
            return ("none",)

    def remove(
        self,
        alias: str,
        requested_by: str = "cli",
        progress_observer=None,
    ) -> RemoveOutcome:
        # Refuse before any mutation: dry-run blockers are authoritative.
        plan = self.dry_run(alias)
        if not plan["found"]:
            return RemoveOutcome(
                None, "UNKNOWN_ALIAS", {"reason": "alias not installed"}
            )
        if plan["blockers"]:
            return RemoveOutcome(
                None,
                "BLOCKED",
                {"reason": "; ".join(plan["blockers"]), "dry_run": plan},
            )

        found = self._scan_existing(alias)
        if found[0] == "barrier":
            return RemoveOutcome(
                found[1],
                "RECOVERY_REQUIRED",
                {"reason": "A previous removal needs repair before continuing."},
            )
        if found[0] == "active":
            operation_id, expired = found[1], found[2]
            if not expired:
                return RemoveOutcome(
                    operation_id, "BUSY", {"reason": "A removal is running."}
                )
            return self._map(operation_id, self._run(operation_id, None))

        record = self._enqueue.enqueue(
            operation_type="MODEL_REMOVE",
            payload={"alias": alias, "requested_by": requested_by},
            surface=requested_by,
        )
        return self._map(record.id, self._run(record.id, progress_observer))

    def _run(self, operation_id: str, observer):
        engine = self._engine_factory()
        return engine.execute_one(operation_id)

    def _map(self, operation_id: str, outcome: Any) -> RemoveOutcome:
        if outcome.kind == "SKIPPED_BUSY":
            return RemoveOutcome(
                operation_id, "BUSY", {"reason": "storage resource held"}
            )
        if outcome.kind == "LOST_LEASE":
            return RemoveOutcome(
                operation_id,
                "RECOVERY_REQUIRED",
                {"reason": "lease lost mid-run; inspect before retrying."},
            )
        with self._units.begin() as conn:
            record = OperationRepository(conn).require(operation_id)
        detail = {
            "result_code": record.result_code,
            "error_code": record.error_code,
        }
        state = record.state
        code = record.result_code or record.error_code or ""
        mapped = _STATUS_MAP.get(code)
        if state is OperationState.SUCCEEDED and mapped:
            return RemoveOutcome(operation_id, mapped, detail)
        if state is OperationState.FAILED_SAFE:
            return RemoveOutcome(
                operation_id, mapped or "FAILED_SAFE", detail
            )
        if state is OperationState.RECOVERY_REQUIRED:
            return RemoveOutcome(
                operation_id,
                "RECOVERY_REQUIRED",
                {**detail, "reason": "Repair required before this finishes."},
            )
        if state is OperationState.PAUSED:
            return RemoveOutcome(operation_id, "PAUSED", detail)
        return RemoveOutcome(
            operation_id, "BUSY", {**detail, "reason": f"state {state.value}"}
        )
