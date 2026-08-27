"""Production host adapter for ``MODEL_REMOVE v1`` (P6 §12.2).

ONE production host satisfies the :class:`ModelRemoveHost` port. It resolves
the alias to its immutable artifact identity, refuses blocked removals before
any mutation, detaches the alias in a transaction, and MOVES unreferenced
bytes to the operation-owned quarantine (never deletes them) so a bounded undo
exists while the retention window holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .legacy_import import utcnow
from .operations.model_remove import (
    CODE_ALIAS_UNKNOWN,
    CODE_MODEL_REMOVED,
    CODE_QUARANTINE_RETAINED,
    CODE_REMOVAL_BLOCKED,
    EffectContext,
    ModelRemoveHost,
    ModelRemoveRequestV1,
    ProbeResult,
    RecoveryClass,
)
from .operations.validation import OperationValidationError
from .paths import AppPaths
from .unit_of_work import UnitOfWorkFactory


class ModelRemoveHostAdapter(ModelRemoveHost):
    def __init__(
        self,
        units: UnitOfWorkFactory,
        paths: AppPaths,
        *,
        clock=utcnow,
    ) -> None:
        self._units = units
        self._paths = paths
        self._clock = clock

    # --- resolve -----------------------------------------------------------

    def _lookup(self, conn, alias: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT i.alias AS alias, i.path AS path, i.artifact_id AS "
            "artifact_id, a.content_digest AS content_digest, "
            "a.byte_size AS byte_size, a.storage_state AS storage_state "
            "FROM model_installations i "
            "LEFT JOIN model_artifacts a ON a.id = i.artifact_id "
            "WHERE i.alias = ?",
            (alias,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _blockers(self, conn, alias: str, artifact_id: str | None) -> list[str]:
        blockers: list[str] = []
        kg = conn.execute(
            "SELECT model_alias FROM known_good_runtime WHERE id = 1"
        ).fetchone()
        if kg is not None and kg["model_alias"] == alias:
            blockers.append("active-known-good-model")
        if artifact_id:
            refs = conn.execute(
                "SELECT COUNT(*) AS n FROM model_installations "
                "WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if refs is not None and refs["n"] > 1:
                blockers.append("artifact-referenced-by-other-aliases")
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM operations WHERE state IN "
            "('QUEUED', 'PREPARING', 'RUNNING', 'PAUSED', "
            "'RECOVERY_REQUIRED') AND operation_type != 'MODEL_REMOVE'"
        ).fetchone()
        if pending is not None and pending["n"] > 0:
            blockers.append("pending-operations-in-flight")
        return blockers

    def resolve_identity(self, request: ModelRemoveRequestV1) -> dict[str, Any]:
        with self._units.read() as conn:
            row = self._lookup(conn, request.alias)
            if row is None:
                raise OperationValidationError(
                    f"{CODE_ALIAS_UNKNOWN}: alias {request.alias!r} not found"
                )
            blockers = self._blockers(conn, request.alias, row.get("artifact_id"))
        if blockers:
            raise OperationValidationError(
                f"{CODE_REMOVAL_BLOCKED}: {', '.join(blockers)}"
            )
        return {
            "alias": request.alias,
            "artifact_id": row.get("artifact_id"),
            "content_digest": row.get("content_digest"),
            "path": row.get("path"),
            "byte_size": row.get("byte_size"),
            "resolved_at": self._clock(),
        }

    def probe_identity(self, ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("resolve_identity") or {}
        identity = (output.get("identity") or {}).get("evidence") or {}
        if identity.get("alias"):
            return ProbeResult(
                RecoveryClass.COMPLETE, "IDENTITY_RESOLVED", output
            )
        return ProbeResult(RecoveryClass.ABSENT, "IDENTITY_UNRESOLVED")

    # --- detach ------------------------------------------------------------

    def detach_alias(self, ctx: EffectContext) -> dict[str, Any]:
        alias = ctx.request.alias
        with self._units.begin() as conn:
            row = self._lookup(conn, alias)
            if row is None:
                # Already detached (idempotent re-run after takeover).
                return {"alias": alias, "detached": True, "already": True}
            blockers = self._blockers(conn, alias, row.get("artifact_id"))
            if blockers:
                raise OperationValidationError(
                    f"{CODE_REMOVAL_BLOCKED}: {', '.join(blockers)}"
                )
            conn.execute(
                "DELETE FROM model_installations WHERE alias = ?", (alias,)
            )
        return {
            "alias": alias,
            "detached": True,
            "artifact_id": row.get("artifact_id"),
            "detached_at": self._clock(),
        }

    def probe_detachment(self, ctx: EffectContext) -> ProbeResult:
        alias = ctx.request.alias
        with self._units.read() as conn:
            row = self._lookup(conn, alias)
        if row is None:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "ALIAS_DETACHED",
                ctx.prior_outputs.get("detach_alias"),
            )
        return ProbeResult(RecoveryClass.ABSENT, "ALIAS_STILL_PRESENT")

    # --- quarantine --------------------------------------------------------

    def _identity_from(self, ctx: EffectContext) -> dict[str, Any]:
        return (
            (ctx.prior_outputs.get("resolve_identity") or {})
            .get("identity", {})
            .get("evidence", {})
        )

    def _quarantine_dest(self, ctx: EffectContext, digest: str) -> Path:
        root = self._paths.model_quarantine_dir / "removals"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{ctx.operation_id}.{digest[:16]}.gguf"

    def quarantine_bytes(self, ctx: EffectContext) -> dict[str, Any]:
        identity = self._identity_from(ctx)
        artifact_id = identity.get("artifact_id")
        digest = identity.get("content_digest")
        source = identity.get("path")

        # Re-verify references immediately before moving bytes: forward-only
        # publication rules mean a referenced/active artifact is retained.
        with self._units.read() as conn:
            if artifact_id:
                refs = conn.execute(
                    "SELECT COUNT(*) AS n FROM model_installations "
                    "WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
                if refs is not None and refs["n"] > 0:
                    return {
                        "disposition": CODE_QUARANTINE_RETAINED,
                        "reason": "artifact-still-referenced",
                    }
            kg = conn.execute(
                "SELECT model_alias FROM known_good_runtime WHERE id = 1"
            ).fetchone()
            if kg is not None and kg["model_alias"] == ctx.request.alias:
                return {
                    "disposition": CODE_QUARANTINE_RETAINED,
                    "reason": "active-known-good-model",
                }

        if not source or not digest:
            return {
                "disposition": CODE_QUARANTINE_RETAINED,
                "reason": "no-managed-bytes",
            }
        src = Path(source)
        if not src.exists():
            return {
                "disposition": CODE_QUARANTINE_RETAINED,
                "reason": "bytes-already-absent",
            }
        dest = self._quarantine_dest(ctx, digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
        return {
            "disposition": CODE_MODEL_REMOVED,
            "quarantine_path": str(dest),
            "content_digest": digest,
        }

    def probe_quarantine(self, ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("quarantine_bytes") or {}
        quarantine = (output.get("quarantine") or {}).get("evidence") or {}
        if quarantine.get("disposition"):
            return ProbeResult(
                RecoveryClass.COMPLETE, "QUARANTINE_DECIDED", output
            )
        return ProbeResult(RecoveryClass.ABSENT, "QUARANTINE_UNDECIDED")

    # --- record ------------------------------------------------------------

    def _receipt_path(self, ctx: EffectContext) -> Path:
        root = self._paths.model_quarantine_dir / "removals"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{ctx.operation_id}.removal-receipt.json"

    def record_removal(self, ctx: EffectContext) -> dict[str, Any]:
        identity = self._identity_from(ctx)
        quarantine = (
            (ctx.prior_outputs.get("quarantine_bytes") or {})
            .get("quarantine", {})
            .get("evidence", {})
        )
        artifact_id = identity.get("artifact_id")
        disposition = quarantine.get("disposition") or CODE_MODEL_REMOVED
        receipt = {
            "operation_id": ctx.operation_id,
            "alias": ctx.request.alias,
            "artifact_id": artifact_id,
            "content_digest": identity.get("content_digest"),
            "quarantine_path": quarantine.get("quarantine_path"),
            "disposition": disposition,
            "removed_at": self._clock(),
        }
        receipt_path = self._receipt_path(ctx)
        receipt_path.write_text(json.dumps(receipt, indent=2))
        if artifact_id and disposition == CODE_MODEL_REMOVED:
            with self._units.begin() as conn:
                conn.execute(
                    "UPDATE model_artifacts SET storage_state = 'QUARANTINED',"
                    " quarantine_reason_code = 'removed-by-operator' "
                    "WHERE id = ?",
                    (artifact_id,),
                )
        return {"disposition": disposition, "receipt_path": str(receipt_path)}

    def probe_removal_record(self, ctx: EffectContext) -> ProbeResult:
        receipt_path = self._receipt_path(ctx)
        if receipt_path.exists():
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "REMOVAL_RECORDED",
                ctx.prior_outputs.get("record_removal"),
            )
        return ProbeResult(RecoveryClass.ABSENT, "REMOVAL_UNRECORDED")
