"""C2 §C2.3/§C2.4: BACKUP_CREATE/BACKUP_RESTORE workflow contracts (pure).

Tests the versioned request decoders (closed fields, fail-closed encryption
refusal, digest-bound restore confirmation) and the frozen workflow shapes
(step order, resources, terminal decisions) without the engine.
"""

from __future__ import annotations

import pytest

from bc250_llm_mode.operations.backup import (
    BACKUP_RESOURCE,
    CODE_RESTORE_PUBLISHED,
    CODE_RESTORE_ROLLED_BACK,
    PUBLISH_BARRIER_RESOURCE,
    RESTORE_RESOURCE,
    BackupCreateHost,
    BackupCreateRequestV1,
    BackupRestoreHost,
    BackupRestoreRequestV1,
    build_backup_create_workflow,
    build_backup_restore_workflow,
    decode_backup_create_request,
    decode_backup_restore_request,
)
from bc250_llm_mode.operations.model import OperationState, OperationType
from bc250_llm_mode.operations.validation import OperationValidationError

_SHA = "a" * 64


# -- BACKUP_CREATE request --------------------------------------------------

def test_backup_create_decode_valid():
    req = decode_backup_create_request(
        {"destination_label": "backups/bk-1.tar",
         "include_models": False, "requested_by": "gui"})
    assert isinstance(req, BackupCreateRequestV1)
    assert req.destination_label == "backups/bk-1.tar"
    assert req.include_models is False and req.include_runtime is False
    assert req.encrypt is False


def test_backup_create_rejects_unknown_field():
    with pytest.raises(OperationValidationError):
        decode_backup_create_request(
            {"destination_label": "x", "bogus": 1})


def test_backup_create_rejects_bad_destination():
    with pytest.raises(OperationValidationError):
        decode_backup_create_request({"destination_label": ""})
    with pytest.raises(OperationValidationError):
        decode_backup_create_request({"destination_label": "x" * 600})


def test_backup_create_encryption_refused_fail_closed():
    """ADR 006 D2: requesting encryption refuses BEFORE any effect until a
    reviewed crypto dependency exists — never silently downgraded."""
    with pytest.raises(OperationValidationError) as exc:
        decode_backup_create_request(
            {"destination_label": "x", "encrypt": True})
    assert "ENCRYPTION_UNAVAILABLE" in str(exc.value)


# -- BACKUP_RESTORE request -------------------------------------------------

def test_backup_restore_decode_valid():
    req = decode_backup_restore_request(
        {"backup_id": "bk-1", "confirmation_digest": _SHA})
    assert isinstance(req, BackupRestoreRequestV1)
    assert req.backup_id == "bk-1" and req.confirmation_digest == _SHA


def test_backup_restore_rejects_bad_digest():
    with pytest.raises(OperationValidationError):
        decode_backup_restore_request(
            {"backup_id": "bk-1", "confirmation_digest": "short"})
    with pytest.raises(OperationValidationError):
        decode_backup_restore_request(
            {"backup_id": "bk-1", "confirmation_digest": "Z" * 64})


def test_backup_restore_rejects_unknown_and_bad_id():
    with pytest.raises(OperationValidationError):
        decode_backup_restore_request(
            {"backup_id": "bk-1", "confirmation_digest": _SHA, "x": 1})
    with pytest.raises(OperationValidationError):
        decode_backup_restore_request(
            {"backup_id": "", "confirmation_digest": _SHA})


# -- workflow shapes --------------------------------------------------------

def test_backup_create_workflow_shape():
    wf = build_backup_create_workflow(BackupCreateHost())
    assert wf.operation_type is OperationType.BACKUP_CREATE
    keys = [s.step_key for s in wf.steps]
    assert keys == ["snapshot_database", "inventory_and_stage",
                    "publish_archive", "verify_archive", "record_backup"]
    assert wf.all_resources() == (BACKUP_RESOURCE,)
    # publish is the forward-only critical boundary
    publish = wf.steps[2]
    assert publish.critical and publish.effect_disposition == "FORWARD_ONLY"


def test_backup_restore_workflow_shape():
    wf = build_backup_restore_workflow(BackupRestoreHost())
    assert wf.operation_type is OperationType.BACKUP_RESTORE
    keys = [s.step_key for s in wf.steps]
    assert keys == ["validate_source", "stage_candidate", "validate_staged",
                    "publish_exchange", "verify_post_restore",
                    "promote_or_rollback"]
    # the publication barrier joins at the exchange boundary
    assert PUBLISH_BARRIER_RESOURCE in wf.resources_for("publish_exchange")
    assert PUBLISH_BARRIER_RESOURCE not in wf.resources_for("validate_source")
    assert wf.resources_for("stage_candidate") == (RESTORE_RESOURCE,)


def test_restore_terminal_rolled_back_is_failed_safe():
    wf = build_backup_restore_workflow(BackupRestoreHost())
    req = BackupRestoreRequestV1(backup_id="bk-1", confirmation_digest=_SHA)
    decision = wf.terminal_decision(
        req, {"promote_or_rollback": {
            "terminal": {"evidence": {"disposition": CODE_RESTORE_ROLLED_BACK}}}})
    assert decision.state is OperationState.FAILED_SAFE

    ok = wf.terminal_decision(
        req, {"promote_or_rollback": {
            "terminal": {"evidence": {"disposition": CODE_RESTORE_PUBLISHED}}}})
    assert ok.state is OperationState.SUCCEEDED
