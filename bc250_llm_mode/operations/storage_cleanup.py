"""Durable ``STORAGE_CLEANUP v1`` workflow (ADR 012 D4/D5)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .model import OperationState, OperationType
from .recovery import RecoveryClass
from .validation import OperationValidationError
from .workflow import (
    EffectContext,
    ProbeResult,
    StepDefinition,
    TerminalDecision,
    WorkflowDefinition,
)

REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1
MAX_TARGETS = 32
CLEANUP_RESOURCES = ("model-storage", "storage-cleanup")
MODES = frozenset({"QUARANTINE", "RESTORE", "PURGE"})
TARGET_KINDS = frozenset({"ABANDONED_STAGING", "CLEANUP_QUARANTINE"})
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class CleanupTargetV1:
    target_id: str
    kind: str
    relative_name: str
    expected_tree_digest: str
    expected_bytes: int
    expected_files: int
    owner_operation_id: str
    quarantine_operation_id: str | None = None
    retention_until: str | None = None


@dataclass(frozen=True)
class StorageCleanupRequestV1:
    mode: str
    targets: tuple[CleanupTargetV1, ...]
    preview_digest: str
    requested_by: str = "cli"


def _closed(value: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise OperationValidationError(f"unknown cleanup fields: {sorted(unknown)}")


def _opaque(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
        raise OperationValidationError(f"{label} is not a safe opaque identity")
    return value


def decode_cleanup_request(payload: dict[str, Any]) -> StorageCleanupRequestV1:
    _closed(payload, frozenset({"mode", "targets", "preview_digest", "requested_by"}))
    mode = str(payload.get("mode") or "").upper()
    if mode not in MODES:
        raise OperationValidationError("cleanup mode is unknown")
    preview_digest = str(payload.get("preview_digest") or "")
    if not _DIGEST.fullmatch(preview_digest):
        raise OperationValidationError("cleanup preview digest is invalid")
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= MAX_TARGETS:
        raise OperationValidationError("cleanup requires 1-32 targets")
    targets: list[CleanupTargetV1] = []
    seen: set[str] = set()
    allowed = frozenset({
        "target_id", "kind", "relative_name", "expected_tree_digest",
        "expected_bytes", "expected_files", "owner_operation_id",
        "quarantine_operation_id", "retention_until",
    })
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise OperationValidationError("cleanup target must be an object")
        _closed(raw, allowed)
        target_id = _opaque(raw.get("target_id"), "target_id")
        if target_id in seen:
            raise OperationValidationError("cleanup target IDs must be unique")
        seen.add(target_id)
        kind = str(raw.get("kind") or "")
        if kind not in TARGET_KINDS:
            raise OperationValidationError("cleanup target kind is unknown")
        digest = str(raw.get("expected_tree_digest") or "")
        if not _DIGEST.fullmatch(digest):
            raise OperationValidationError("cleanup tree digest is invalid")
        size = raw.get("expected_bytes")
        files = raw.get("expected_files")
        if not isinstance(size, int) or not 0 <= size <= 1 << 50:
            raise OperationValidationError("cleanup byte count is invalid")
        if not isinstance(files, int) or not 0 <= files <= 4096:
            raise OperationValidationError("cleanup file count is invalid")
        qop = raw.get("quarantine_operation_id")
        if qop is not None:
            qop = _opaque(qop, "quarantine_operation_id")
        retention = raw.get("retention_until")
        if retention is not None and (
            not isinstance(retention, str) or len(retention) > 32
        ):
            raise OperationValidationError("cleanup retention timestamp is invalid")
        targets.append(CleanupTargetV1(
            target_id=target_id,
            kind=kind,
            relative_name=_opaque(raw.get("relative_name"), "relative_name"),
            expected_tree_digest=digest,
            expected_bytes=size,
            expected_files=files,
            owner_operation_id=_opaque(
                raw.get("owner_operation_id"), "owner_operation_id"),
            quarantine_operation_id=qop,
            retention_until=retention,
        ))
    if mode == "QUARANTINE" and any(
        item.kind != "ABANDONED_STAGING" or item.quarantine_operation_id
        for item in targets
    ):
        raise OperationValidationError("quarantine accepts abandoned staging only")
    if mode in {"RESTORE", "PURGE"} and any(
        item.kind != "CLEANUP_QUARANTINE" or not item.quarantine_operation_id
        for item in targets
    ):
        raise OperationValidationError("restore/purge require cleanup receipts")
    return StorageCleanupRequestV1(
        mode, tuple(targets), preview_digest,
        _opaque(payload.get("requested_by", "cli"), "requested_by"),
    )


class StorageCleanupHost:
    def resolve(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_resolved(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def apply_mode(self, ctx: EffectContext, mode: str) -> dict[str, Any]:
        raise NotImplementedError

    def probe_mode(self, ctx: EffectContext, mode: str) -> ProbeResult:
        raise NotImplementedError

    def compensate_mode(self, ctx: EffectContext, mode: str) -> dict[str, Any]:
        raise NotImplementedError

    def probe_compensation(self, ctx: EffectContext, mode: str) -> ProbeResult:
        raise NotImplementedError


def _complete(probe: ProbeResult, code: str) -> None:
    if probe.classification is not RecoveryClass.COMPLETE:
        raise OperationValidationError(f"{code}: {probe.reason_code}")


def build_storage_cleanup_workflow(host: StorageCleanupHost) -> WorkflowDefinition:
    def resolve_execute(ctx: EffectContext) -> dict[str, Any]:
        return {"resolution": host.resolve(ctx)}

    def mode_step(mode: str, sequence: int, disposition: str) -> StepDefinition:
        def execute(ctx: EffectContext) -> dict[str, Any]:
            return {"effect": host.apply_mode(ctx, mode)}

        def verify(ctx: EffectContext) -> dict[str, Any]:
            _complete(host.probe_mode(ctx, mode), f"{mode}_NOT_VERIFIED")
            return {}

        def compensate(ctx: EffectContext) -> dict[str, Any]:
            return {"restoration": host.compensate_mode(ctx, mode)}

        def verify_restoration(ctx: EffectContext) -> dict[str, Any]:
            _complete(
                host.probe_compensation(ctx, mode),
                f"{mode}_RESTORATION_NOT_VERIFIED",
            )
            return {}

        return StepDefinition(
            step_key=f"{mode.lower()}_targets",
            phase=mode.lower(),
            sequence=sequence,
            derive_input=lambda request, prior: {
                "mode": request.mode,
                "target_count": len(request.targets),
            },
            probe=lambda ctx: host.probe_mode(ctx, mode),
            execute=execute,
            verify=verify,
            implementation_version=STEP_VERSION,
            resources=CLEANUP_RESOURCES,
            externally_visible=True,
            critical=True,
            effect_disposition=disposition,
            compensate=compensate if disposition == "REVERSIBLE" else None,
            verify_restoration=(
                verify_restoration if disposition == "REVERSIBLE" else None
            ),
            probe_restoration=(
                (lambda ctx: host.probe_compensation(ctx, mode))
                if disposition == "REVERSIBLE" else None
            ),
        )

    steps = (
        StepDefinition(
            step_key="resolve_targets",
            phase="resolve",
            sequence=1,
            derive_input=lambda request, prior: {
                "mode": request.mode,
                "target_count": len(request.targets),
                "preview_digest": request.preview_digest,
            },
            probe=host.probe_resolved,
            execute=resolve_execute,
            verify=lambda ctx: _complete(
                host.probe_resolved(ctx), "CLEANUP_RESOLUTION_NOT_VERIFIED"),
            resources=CLEANUP_RESOURCES,
        ),
        mode_step("QUARANTINE", 2, "REVERSIBLE"),
        mode_step("RESTORE", 3, "REVERSIBLE"),
        mode_step("PURGE", 4, "FORWARD_ONLY"),
    )

    def terminal(request: StorageCleanupRequestV1, outputs: dict) -> TerminalDecision:
        relevant = outputs.get(f"{request.mode.lower()}_targets") or {}
        effect = relevant.get("effect") or {}
        retained = int(effect.get("retained_count") or 0)
        code = f"CLEANUP_{request.mode}D"
        if request.mode == "PURGE":
            code = "CLEANUP_PURGED" if retained == 0 else "CLEANUP_PURGE_PARTIAL"
        return TerminalDecision(
            OperationState.SUCCEEDED,
            code,
            {
                "mode": request.mode,
                "target_count": len(request.targets),
                "completed_count": int(effect.get("completed_count") or 0),
                "retained_count": retained,
                "bytes_selected": sum(item.expected_bytes for item in request.targets),
            },
            f"storage cleanup {request.mode.lower()} completed",
        )

    return WorkflowDefinition(
        operation_type=OperationType.STORAGE_CLEANUP,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_cleanup_request,
        steps=steps,
        summary=lambda request: (
            f"Storage cleanup {request.mode.lower()} for {len(request.targets)} target(s)"
        ),
        terminal_decision=terminal,
    )


__all__ = [
    "CLEANUP_RESOURCES", "CleanupTargetV1", "MAX_TARGETS", "MODES",
    "StorageCleanupHost", "StorageCleanupRequestV1",
    "build_storage_cleanup_workflow", "decode_cleanup_request",
]
