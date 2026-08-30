"""Evidence-derived, preview-bound Undo for retained cleanup effects.

There is deliberately no generic inverse.  The only v1 candidate is a
successful ``STORAGE_CLEANUP/QUARANTINE`` effect whose verified receipt can be
restored by a new child ``STORAGE_CLEANUP/RESTORE`` operation.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .legacy_import import utcnow
from .operations.model import OperationState, OperationType
from .operations.repositories import LeaseRepository, OperationRepository
from .operations.storage_cleanup import CLEANUP_RESOURCES
from .storage_cleanup_command import PREVIEW_SECONDS, _canonical, _parse

MAX_UNDO_CANDIDATES = 32
UNDO_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class UndoCandidate:
    undo_id: str
    source_operation_id: str
    source_revision: int
    target_id: str
    title: str
    deadline: str
    receipt_digest: str
    resource_generations: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": UNDO_CONTRACT_VERSION,
            "undo_id": self.undo_id,
            "source_operation_id": self.source_operation_id,
            "source_revision": self.source_revision,
            "target_id": self.target_id,
            "title": self.title,
            "deadline": self.deadline,
            "receipt_digest": self.receipt_digest,
            "resource_generations": dict(self.resource_generations),
            "inverse_operation": "STORAGE_CLEANUP/RESTORE",
            "prior_state_survives": True,
        }


@dataclass(frozen=True)
class UndoPreview:
    undo_id: str
    outcome: str
    reason_code: str | None
    candidate: UndoCandidate | None
    preview_digest: str
    confirmation_token: str
    expires_at: str

    @property
    def ready(self) -> bool:
        return self.outcome == "READY" and self.candidate is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "contract_version": UNDO_CONTRACT_VERSION,
            "undo_id": self.undo_id,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "mutation_steps": [
                "revalidate retained cleanup receipt and identity",
                "enqueue a child STORAGE_CLEANUP/RESTORE operation",
                "verify the exact staging identity was restored",
            ],
            "preview_digest": self.preview_digest,
            "confirmation_token": self.confirmation_token,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class UndoResult:
    undo_id: str
    outcome: str
    result_code: str
    operation_id: str | None = None
    source_operation_id: str | None = None
    prior_state_survives: bool = True
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == "SUCCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "contract_version": UNDO_CONTRACT_VERSION,
            "undo_id": self.undo_id,
            "outcome": self.outcome,
            "result_code": self.result_code,
            "operation_id": self.operation_id,
            "source_operation_id": self.source_operation_id,
            "prior_state_survives": self.prior_state_survives,
            **self.detail,
        }


class UndoService:
    def __init__(
        self,
        *,
        units: Any,
        cleanup: Any,
        adapter: Any,
        clock: Callable[[], str] = utcnow,
    ) -> None:
        self._units = units
        self._cleanup = cleanup
        self._adapter = adapter
        self._clock = clock

    def list(self) -> list[dict[str, Any]]:
        """Return only currently executable Undo candidates, bounded to 32."""
        rendered: list[dict[str, Any]] = []
        for item in self._adapter.discover():
            if item.get("kind") != "CLEANUP_QUARANTINE":
                continue
            qop = str(item.get("quarantine_operation_id") or "")
            target = str(item.get("target_id") or "")
            candidate, _reason = self._candidate(qop, target)
            if candidate is not None:
                rendered.append(candidate.to_dict())
            if len(rendered) >= MAX_UNDO_CANDIDATES:
                break
        return sorted(rendered, key=lambda item: item["undo_id"])

    def preview(self, undo_id: str) -> UndoPreview:
        qop, target, parse_reason = self._parse_undo_id(undo_id)
        candidate = None
        reason = parse_reason
        if reason is None:
            candidate, reason = self._candidate(qop, target)
        window, expires = self._window()
        payload = {
            "contract_version": UNDO_CONTRACT_VERSION,
            "undo_id": str(undo_id),
            "candidate": candidate.to_dict() if candidate else None,
            "reason_code": reason,
            "window": window,
            "expires_at": expires,
        }
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        return UndoPreview(
            str(undo_id), "READY" if candidate else "UNAVAILABLE", reason,
            candidate, digest, f"UNDO-{digest[:12].upper()}", expires,
        )

    def run(
        self, undo_id: str, *, preview_digest: str, confirmation_token: str
    ) -> UndoResult:
        current = self.preview(undo_id)
        if not current.ready:
            return UndoResult(
                str(undo_id), "REFUSED", current.reason_code or "UNDO_SUPERSEDED"
            )
        if (
            preview_digest != current.preview_digest
            or confirmation_token != current.confirmation_token
        ):
            return UndoResult(str(undo_id), "REFUSED", "PREVIEW_STALE")
        repeated = self.preview(undo_id)
        if repeated.preview_digest != current.preview_digest or not repeated.ready:
            return UndoResult(
                str(undo_id), "REFUSED",
                repeated.reason_code or "PREVIEW_STALE",
            )
        candidate = repeated.candidate
        assert candidate is not None
        outcome = self._cleanup.restore_target(
            candidate.target_id,
            requested_by="undo",
            parent_operation_id=candidate.source_operation_id,
        )
        if not outcome.ok:
            return UndoResult(
                str(undo_id), outcome.status, outcome.result_code,
                outcome.operation_id, candidate.source_operation_id,
            )
        evidence = self._adapter.inspect_receipt(
            candidate.source_operation_id, candidate.target_id
        )
        verified = evidence.get("state") == "RESTORED"
        return UndoResult(
            str(undo_id), "SUCCEEDED" if verified else "RECOVERY_REQUIRED",
            "UNDO_RESTORE_VERIFIED" if verified else "UNDO_PROBE_FAILED",
            outcome.operation_id, candidate.source_operation_id,
        )

    def _candidate(
        self, source_operation_id: str, target_id: str
    ) -> tuple[UndoCandidate | None, str | None]:
        try:
            evidence = self._adapter.inspect_receipt(
                source_operation_id, target_id
            )
        except Exception:
            return None, "UNDO_SUPERSEDED"
        reason = evidence.get("reason_code")
        if evidence.get("status") != "AVAILABLE":
            return None, str(reason or "UNDO_SUPERSEDED")
        with self._units.read() as conn:
            operations = OperationRepository(conn)
            source = operations.get(source_operation_id)
            if (
                source is None
                or source.operation_type is not OperationType.STORAGE_CLEANUP
                or source.request_version != 1
                or source.recovery_policy_version != 1
                or source.state is not OperationState.SUCCEEDED
            ):
                return None, "UNDO_SUPERSEDED"
            try:
                request = json.loads(source.request_json)
            except (TypeError, ValueError):
                return None, "UNDO_SUPERSEDED"
            if request.get("mode") != "QUARANTINE" or not any(
                item.get("target_id") == target_id
                for item in (request.get("targets") or ())
                if isinstance(item, dict)
            ):
                return None, "UNDO_SUPERSEDED"
            if operations.list_children(
                source_operation_id,
                operation_type=OperationType.STORAGE_CLEANUP.value,
                limit=1,
            ):
                return None, "UNDO_SUPERSEDED"
            leases = LeaseRepository(conn, clock=self._clock)
            occupied = leases.availability(CLEANUP_RESOURCES)
            if any(occupied.values()):
                return None, "LEASE_HELD"
            generation_rows = []
            for key in CLEANUP_RESOURCES:
                lease = leases.get(key)
                generation_rows.append((
                    key, lease.lease_revision if lease is not None else 0
                ))
            generations = tuple(generation_rows)
        undo_id = self._undo_id(source_operation_id, target_id)
        return UndoCandidate(
            undo_id=undo_id,
            source_operation_id=source_operation_id,
            source_revision=source.state_revision,
            target_id=target_id,
            title="Restore quarantined application staging",
            deadline=str(evidence["retention_until"]),
            receipt_digest=str(evidence["receipt_digest"]),
            resource_generations=generations,
        ), None

    @staticmethod
    def _undo_id(source_operation_id: str, target_id: str) -> str:
        return f"cleanup:{source_operation_id}:{target_id}"

    @staticmethod
    def _parse_undo_id(undo_id: str) -> tuple[str, str, str | None]:
        value = str(undo_id)
        parts = value.split(":", 2)
        if (
            len(parts) != 3
            or parts[0] != "cleanup"
            or not parts[1]
            or not parts[2]
            or len(value) > 300
        ):
            return "", "", "UNDO_SUPERSEDED"
        return parts[1], parts[2], None

    def _window(self) -> tuple[int, str]:
        now = _parse(self._clock())
        window = int(now.timestamp()) // PREVIEW_SECONDS
        expires = _datetime.datetime.fromtimestamp(
            (window + 1) * PREVIEW_SECONDS,
            tz=_datetime.timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return window, expires


__all__ = [
    "MAX_UNDO_CANDIDATES", "UNDO_CONTRACT_VERSION", "UndoCandidate",
    "UndoPreview", "UndoResult", "UndoService",
]
