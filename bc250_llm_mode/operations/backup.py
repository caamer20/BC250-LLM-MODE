"""Durable ``BACKUP_CREATE v1`` / ``BACKUP_RESTORE v1`` workflows (ADR 006).

These make backup/restore real, durable, crash-recoverable operations (REL-004)
rather than pure manifest/dry-run contracts. Both workflows are registered in
the ONE frozen registry and driven by the shared engine factory, so creation,
publication, and rollback survive process death and lease takeover.

BACKUP_CREATE v1 (plan §C2.3): snapshot -> inventory -> stage -> publish
(no-replace) -> verify -> record. Secrets are excluded by construction; model/
runtime bytes are excluded unless explicitly included; a collision never
overwrites an existing archive.

BACKUP_RESTORE v1 (plan §C2.4): read/validate source -> stage a contained
candidate profile -> migrate staged DB -> validate staged -> acquire the
profile-exclusive publication barrier -> ONE atomic same-filesystem exchange ->
post-restore verification -> promote (retain prior) OR exchange back OR
RECOVERY_REQUIRED. Pre-publication failures leave the active profile unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import OperationType
from .recovery import RecoveryClass
from .workflow import (
    EffectContext,
    ProbeResult,
    StepDefinition,
    TerminalDecision,
    WorkflowDefinition,
)
from .validation import OperationValidationError

REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1

# Backup contends over the profile; restore additionally takes the exclusive
# publication barrier.
BACKUP_RESOURCE = "profile-backup"
RESTORE_RESOURCE = "profile-restore-staging"
PUBLISH_BARRIER_RESOURCE = "profile-publication"

CODE_BACKUP_CREATED = "BACKUP_CREATED"
CODE_BACKUP_COLLISION = "BACKUP_COLLISION"
CODE_RESTORE_PUBLISHED = "RESTORE_PUBLISHED"
CODE_RESTORE_ROLLED_BACK = "RESTORE_ROLLED_BACK"
CODE_ENCRYPTION_UNAVAILABLE = "ENCRYPTION_UNAVAILABLE"

MAX_DEST_LABEL_CHARS = 512


def _closed(payload: dict[str, Any], fields: frozenset) -> None:
    unknown = set(payload) - fields
    if unknown:
        raise OperationValidationError(
            f"unknown request fields: {sorted(unknown)}")


# -- BACKUP_CREATE v1 -------------------------------------------------------

@dataclass(frozen=True)
class BackupCreateRequestV1:
    destination_label: str
    include_models: bool = False
    include_runtime: bool = False
    encrypt: bool = False
    requested_by: str = "cli"


def decode_backup_create_request(payload: dict[str, Any]) -> BackupCreateRequestV1:
    _closed(payload, frozenset(
        {"destination_label", "include_models", "include_runtime",
         "encrypt", "requested_by"}))
    dest = payload.get("destination_label")
    if (not isinstance(dest, str) or not dest.strip()
            or len(dest) > MAX_DEST_LABEL_CHARS):
        raise OperationValidationError(
            "destination_label must be a short non-empty string")
    if payload.get("encrypt"):
        # ADR 006 D2: encryption is fail-closed until a reviewed crypto
        # dependency exists. Refuse BEFORE any effect.
        raise OperationValidationError(
            f"encryption is not available in this build ({CODE_ENCRYPTION_UNAVAILABLE})")
    return BackupCreateRequestV1(
        destination_label=dest,
        include_models=bool(payload.get("include_models", False)),
        include_runtime=bool(payload.get("include_runtime", False)),
        encrypt=False,
        requested_by=str(payload.get("requested_by", "cli")),
    )


class BackupCreateHost:
    """Typed port the production adapter satisfies (plan §C2.3)."""

    def snapshot_database(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_snapshot(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def inventory_and_stage(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_staged(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def publish_archive(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_published(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def verify_archive(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_verified(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def record_backup(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_record(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError


def _require_complete(result: ProbeResult, code: str) -> None:
    if result.classification is not RecoveryClass.COMPLETE:
        raise OperationValidationError(
            f"backup precondition not met: {code} ({result.reason_code})")


def _backup_terminal(request: Any, verified_outputs: dict) -> TerminalDecision:
    from .model import OperationState

    record = (verified_outputs.get("record_backup") or {}).get(
        "record") or {}
    evidence = record.get("evidence") or {}
    disposition = evidence.get("disposition") or CODE_BACKUP_CREATED
    return TerminalDecision(
        OperationState.SUCCEEDED,
        disposition,
        {"destination_label": request.destination_label},
        f"created backup at {request.destination_label!r}",
    )


def build_backup_create_workflow(host: BackupCreateHost) -> WorkflowDefinition:
    """Frozen ``BACKUP_CREATE v1``: five forward-only steps."""

    steps = (
        StepDefinition(
            step_key="snapshot_database",
            phase="snapshot",
            sequence=1,
            derive_input=lambda request, prior: {
                "destination_label": request.destination_label},
            probe=host.probe_snapshot,
            execute=lambda ctx: {
                "snapshot": {"evidence": host.snapshot_database(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_snapshot(ctx), "SNAPSHOT_MISSING"),
            effect_disposition="REVERSIBLE",
            resources=(BACKUP_RESOURCE,),
        ),
        StepDefinition(
            step_key="inventory_and_stage",
            phase="stage",
            sequence=2,
            derive_input=lambda request, prior: {
                "destination_label": request.destination_label,
                "include_models": request.include_models,
                "include_runtime": request.include_runtime},
            probe=host.probe_staged,
            execute=lambda ctx: {
                "staged": {"evidence": host.inventory_and_stage(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_staged(ctx), "STAGING_INCOMPLETE"),
            effect_disposition="REVERSIBLE",
            resources=(BACKUP_RESOURCE,),
        ),
        StepDefinition(
            step_key="publish_archive",
            phase="publish",
            sequence=3,
            derive_input=lambda request, prior: {
                "destination_label": request.destination_label},
            probe=host.probe_published,
            execute=lambda ctx: {
                "published": {"evidence": host.publish_archive(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_published(ctx), "PUBLISH_INCOMPLETE"),
            effect_disposition="FORWARD_ONLY",
            critical=True,
            resources=(BACKUP_RESOURCE,),
        ),
        StepDefinition(
            step_key="verify_archive",
            phase="verify",
            sequence=4,
            derive_input=lambda request, prior: {
                "destination_label": request.destination_label},
            probe=host.probe_verified,
            execute=lambda ctx: {
                "verified": {"evidence": host.verify_archive(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_verified(ctx), "VERIFY_FAILED"),
            effect_disposition="HIDDEN_DURABLE",
            resources=(BACKUP_RESOURCE,),
        ),
        StepDefinition(
            step_key="record_backup",
            phase="finalize",
            sequence=5,
            derive_input=lambda request, prior: {
                "destination_label": request.destination_label},
            probe=host.probe_record,
            execute=lambda ctx: {
                "record": {"evidence": host.record_backup(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_record(ctx), "RECORD_MISSING"),
            effect_disposition="HIDDEN_DURABLE",
            resources=(BACKUP_RESOURCE,),
        ),
    )

    return WorkflowDefinition(
        operation_type=OperationType.BACKUP_CREATE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_backup_create_request,
        steps=steps,
        summary=lambda request:
            f"Create backup at {request.destination_label!r}",
        terminal_decision=_backup_terminal,
    )


# -- BACKUP_RESTORE v1 ------------------------------------------------------

@dataclass(frozen=True)
class BackupRestoreRequestV1:
    backup_id: str
    confirmation_digest: str
    requested_by: str = "cli"


def decode_backup_restore_request(payload: dict[str, Any]) -> BackupRestoreRequestV1:
    _closed(payload, frozenset(
        {"backup_id", "confirmation_digest", "requested_by"}))
    backup_id = payload.get("backup_id")
    if not isinstance(backup_id, str) or not backup_id.strip():
        raise OperationValidationError("backup_id must be a non-empty string")
    digest = payload.get("confirmation_digest")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)):
        raise OperationValidationError(
            "confirmation_digest must be a full sha256 hex bound to the "
            "dry-run result")
    return BackupRestoreRequestV1(
        backup_id=backup_id,
        confirmation_digest=digest,
        requested_by=str(payload.get("requested_by", "cli")),
    )


class BackupRestoreHost:
    """Typed port the production adapter satisfies (plan §C2.4)."""

    def validate_source(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_source_valid(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def stage_candidate(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_staged_candidate(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def validate_staged(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_staged_validated(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def publish_exchange(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_restore_published(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def verify_post_restore(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_post_verified(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def promote_or_rollback(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_terminal(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError


def _restore_terminal(request: Any, verified_outputs: dict) -> TerminalDecision:
    from .model import OperationState

    terminal = (verified_outputs.get("promote_or_rollback") or {}).get(
        "terminal") or {}
    evidence = terminal.get("evidence") or {}
    disposition = evidence.get("disposition") or CODE_RESTORE_PUBLISHED
    if disposition == CODE_RESTORE_ROLLED_BACK:
        return TerminalDecision(
            OperationState.FAILED_SAFE,
            disposition,
            {"backup_id": request.backup_id},
            "restore verification failed; exchanged back to the prior profile",
        )
    return TerminalDecision(
        OperationState.SUCCEEDED,
        disposition,
        {"backup_id": request.backup_id},
        f"restored backup {request.backup_id!r} and verified the profile",
    )


def build_backup_restore_workflow(host: BackupRestoreHost) -> WorkflowDefinition:
    """Frozen ``BACKUP_RESTORE v1``: six steps to the publication barrier."""

    steps = (
        StepDefinition(
            step_key="validate_source",
            phase="validate",
            sequence=1,
            derive_input=lambda request, prior: {
                "backup_id": request.backup_id,
                "confirmation_digest": request.confirmation_digest},
            probe=host.probe_source_valid,
            execute=lambda ctx: {
                "source": {"evidence": host.validate_source(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_source_valid(ctx), "SOURCE_INVALID"),
            effect_disposition="NONE",
            resources=(RESTORE_RESOURCE,),
        ),
        StepDefinition(
            step_key="stage_candidate",
            phase="stage",
            sequence=2,
            derive_input=lambda request, prior: {
                "backup_id": request.backup_id},
            probe=host.probe_staged_candidate,
            execute=lambda ctx: {
                "staged": {"evidence": host.stage_candidate(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_staged_candidate(ctx), "STAGING_INCOMPLETE"),
            effect_disposition="REVERSIBLE",
            resources=(RESTORE_RESOURCE,),
        ),
        StepDefinition(
            step_key="validate_staged",
            phase="validate",
            sequence=3,
            derive_input=lambda request, prior: {
                "backup_id": request.backup_id},
            probe=host.probe_staged_validated,
            execute=lambda ctx: {
                "staged_validated": {"evidence": host.validate_staged(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_staged_validated(ctx), "STAGED_INVALID"),
            effect_disposition="HIDDEN_DURABLE",
            resources=(RESTORE_RESOURCE,),
        ),
        StepDefinition(
            step_key="publish_exchange",
            phase="publish",
            sequence=4,
            derive_input=lambda request, prior: {
                "backup_id": request.backup_id},
            probe=host.probe_restore_published,
            execute=lambda ctx: {
                "published": {"evidence": host.publish_exchange(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_restore_published(ctx), "PUBLISH_INCOMPLETE"),
            effect_disposition="FORWARD_ONLY",
            critical=True,
            resources=(RESTORE_RESOURCE, PUBLISH_BARRIER_RESOURCE),
        ),
        StepDefinition(
            step_key="verify_post_restore",
            phase="verify",
            sequence=5,
            derive_input=lambda request, prior: {
                "backup_id": request.backup_id},
            probe=host.probe_post_verified,
            execute=lambda ctx: {
                "post_verified": {"evidence": host.verify_post_restore(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_post_verified(ctx), "POST_VERIFY_FAILED"),
            effect_disposition="HIDDEN_DURABLE",
            critical=True,
            resources=(RESTORE_RESOURCE, PUBLISH_BARRIER_RESOURCE),
        ),
        StepDefinition(
            step_key="promote_or_rollback",
            phase="finalize",
            sequence=6,
            derive_input=lambda request, prior: {
                "backup_id": request.backup_id},
            probe=host.probe_terminal,
            execute=lambda ctx: {
                "terminal": {"evidence": host.promote_or_rollback(ctx)}},
            verify=lambda ctx: _require_complete(
                host.probe_terminal(ctx), "TERMINAL_MISSING"),
            effect_disposition="HIDDEN_DURABLE",
            resources=(RESTORE_RESOURCE, PUBLISH_BARRIER_RESOURCE),
        ),
    )

    return WorkflowDefinition(
        operation_type=OperationType.BACKUP_RESTORE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_backup_restore_request,
        steps=steps,
        summary=lambda request: f"Restore backup {request.backup_id!r}",
        terminal_decision=_restore_terminal,
    )
