from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.artifact_storage import read_receipt, write_receipt
from bc250_llm_mode.db import SCHEMA_VERSION
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState, OperationType
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.workflow import EnqueueService
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.storage_cleanup_command import StorageCleanupCommandService


class ProcessDeath(BaseException):
    pass


class Clock:
    def __init__(self):
        self.value = datetime.datetime(
            2026, 8, 30, 12, 0, tzinfo=datetime.timezone.utc)

    def now(self):
        return self.value.strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self, seconds):
        self.value += datetime.timedelta(seconds=seconds)


def _app(tmp_path):
    app = Application.compose(AppPaths.temporary(tmp_path))
    app.setup.acknowledge_safety()
    return app


def _terminal_owner(app, operation_id):
    with app.units.begin() as conn:
        repo = OperationRepository(conn)
        repo.create(
            operation_type=OperationType.MODEL_ACTIVATE,
            request={},
            operation_id=operation_id,
            surface="test",
        )
        repo.record_terminal_result(
            operation_id,
            terminal_state=OperationState.FAILED_SAFE,
            error_code="TEST_TERMINAL",
        )


def _stage(app, operation_id, content=b"model-part"):
    _terminal_owner(app, operation_id)
    root = app.paths.model_staging_dir / operation_id
    root.mkdir(parents=True)
    (root / "partial.bin").write_bytes(content)
    return root


def test_cleanup_uses_schema_13_and_one_registered_operation(tmp_path):
    app = _app(tmp_path)
    assert SCHEMA_VERSION == 13
    definition = app.registry.definition_for_type(OperationType.STORAGE_CLEANUP)
    assert definition.request_version == 1
    assert definition.recovery_policy_version == 1
    assert definition.all_resources() == ("model-storage", "storage-cleanup")
    assert [step.step_key for step in definition.steps] == [
        "resolve_targets", "quarantine_targets", "restore_targets", "purge_targets"]


def test_default_cleanup_quarantines_only_terminal_operation_staging(tmp_path):
    app = _app(tmp_path)
    staged = _stage(app, "finished-stage")
    active = app.paths.model_staging_dir / "unknown-stage"
    active.mkdir(parents=True)
    (active / "keep.bin").write_bytes(b"keep")
    external = app.paths.models_dir / "external-model"
    external.mkdir()
    (external / "model.gguf").write_bytes(b"external")

    preview = app.storage_cleanup.preview()
    assert preview.ready
    assert len(preview.selected) == 1
    assert preview.selected[0]["relative_name"] == "finished-stage"
    assert preview.selected[0]["default_selected"] is True
    assert all(item["relative_name"] != "unknown-stage" for item in preview.candidates)

    outcome = app.storage_cleanup.apply(
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert outcome.ok
    assert outcome.result_code == "CLEANUP_QUARANTINED"
    assert not staged.exists()
    assert active.exists()
    assert external.exists()
    assert (external / "model.gguf").read_bytes() == b"external"

    restore = app.storage_cleanup.preview(
        mode="RESTORE", target_ids=(preview.selected[0]["target_id"],))
    assert restore.ready
    restored = app.storage_cleanup.apply(
        mode="RESTORE", target_ids=(preview.selected[0]["target_id"],),
        preview_digest=restore.preview_digest,
        confirmation_token=restore.confirmation_token,
    )
    assert restored.ok and restored.result_code == "CLEANUP_RESTORED"
    assert staged.is_dir()
    assert (staged / "partial.bin").read_bytes() == b"model-part"


def test_apply_requires_ack_and_exact_unexpired_preview(tmp_path):
    app = Application.compose(AppPaths.temporary(tmp_path))
    _stage(app, "stale-preview-stage")
    preview = app.storage_cleanup.preview()
    refused = app.storage_cleanup.apply(
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert refused.result_code == "SAFETY_ACKNOWLEDGMENT_REQUIRED"
    app.setup.acknowledge_safety()
    (app.paths.model_staging_dir / "stale-preview-stage" / "changed").write_bytes(b"x")
    stale = app.storage_cleanup.apply(
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert stale.result_code == "CLEANUP_PREVIEW_STALE"
    assert (app.paths.model_staging_dir / "stale-preview-stage").exists()


def test_symlink_and_external_paths_are_never_candidates(tmp_path):
    app = _app(tmp_path)
    _terminal_owner(app, "linked-stage")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable").write_text("keep", encoding="utf-8")
    app.paths.model_staging_dir.mkdir(parents=True, exist_ok=True)
    (app.paths.model_staging_dir / "linked-stage").symlink_to(
        outside, target_is_directory=True)
    assert app.storage_cleanup.preview().selected == ()
    assert (outside / "valuable").read_text(encoding="utf-8") == "keep"


def test_expired_quarantine_requires_explicit_purge_and_keeps_receipt(tmp_path):
    app = _app(tmp_path)
    _stage(app, "purge-stage", b"purge-me")
    first = app.storage_cleanup.preview()
    quarantined = app.storage_cleanup.apply(
        preview_digest=first.preview_digest,
        confirmation_token=first.confirmation_token,
    )
    assert quarantined.ok
    target_id = first.selected[0]["target_id"]
    operation_root = app.paths.model_quarantine_dir / "cleanup" / quarantined.operation_id
    receipt_path = operation_root / f"{target_id}.cleanup.json"
    receipt = read_receipt(receipt_path)
    receipt["retention_until"] = "2000-01-01T00:00:00Z"
    write_receipt(receipt_path, receipt)

    dry = app.storage_cleanup.preview(mode="PURGE", target_ids=(target_id,))
    assert dry.ready
    assert dry.selected[0]["default_selected"] is False
    purged = app.storage_cleanup.apply(
        mode="PURGE", target_ids=(target_id,),
        preview_digest=dry.preview_digest,
        confirmation_token=dry.confirmation_token,
    )
    assert purged.ok
    assert purged.result_code == "CLEANUP_PURGED"
    assert not (operation_root / target_id).exists()
    assert read_receipt(receipt_path)["state"] == "PURGED"


def test_partial_permanent_delete_is_recovery_required_not_failed_safe(
    tmp_path, monkeypatch
):
    app = _app(tmp_path)
    _stage(app, "uncertain-purge", b"one")
    initial = app.storage_cleanup.preview()
    quarantined = app.storage_cleanup.apply(
        preview_digest=initial.preview_digest,
        confirmation_token=initial.confirmation_token,
    )
    target_id = initial.selected[0]["target_id"]
    operation_root = app.paths.model_quarantine_dir / "cleanup" / quarantined.operation_id
    receipt_path = operation_root / f"{target_id}.cleanup.json"
    receipt = read_receipt(receipt_path)
    receipt["retention_until"] = "2000-01-01T00:00:00Z"
    write_receipt(receipt_path, receipt)
    preview = app.storage_cleanup.preview(mode="PURGE", target_ids=(target_id,))

    def partial_delete(path):
        child = next(Path(path).iterdir())
        child.unlink()
        raise OSError("simulated partial delete")

    from bc250_llm_mode import storage_cleanup_adapter
    monkeypatch.setattr(storage_cleanup_adapter.shutil, "rmtree", partial_delete)
    # Function attributes are consulted by the adapter before the call.
    partial_delete.avoids_symlink_attacks = True
    outcome = app.storage_cleanup.apply(
        mode="PURGE", target_ids=(target_id,),
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert outcome.status == "RECOVERY_REQUIRED"
    assert outcome.result_code == "CLEANUP_PURGE_PARTIAL_UNCERTAIN"
    with app.units.read() as conn:
        row = OperationRepository(conn).require(outcome.operation_id)
    assert row.state is OperationState.RECOVERY_REQUIRED
    assert row.error_code == "CLEANUP_PURGE_PARTIAL_UNCERTAIN"


@pytest.mark.parametrize(
    "point",
    ["after_step_start", "before_step_checkpoint", "after_step_checkpoint",
     "before_step_verification", "after_step_verification"],
)
def test_quarantine_crash_points_converge_without_duplicate_effect(tmp_path, point):
    app = _app(tmp_path)
    staged = _stage(app, f"crash-{point.replace('_', '-')}")
    preview = app.storage_cleanup.preview()
    selected = preview.selected[0]
    clock = Clock()
    enqueue = EnqueueService(
        app.units, app.registry, clock=clock.now, uuid_factory=lambda: "cleanup-crash")
    record = enqueue.enqueue(
        operation_type=OperationType.STORAGE_CLEANUP,
        payload={
            "mode": "QUARANTINE",
            "targets": [StorageCleanupCommandService._request_target(selected)],
            "preview_digest": preview.preview_digest,
            "requested_by": "test",
        },
        surface="test",
        operation_id="cleanup-crash",
    )
    armed = {"yes": True}

    def crash(step, observed):
        if armed["yes"] and step == "quarantine_targets" and observed == point:
            armed["yes"] = False
            raise ProcessDeath(point)

    first = ExecutionEngine(
        app.units, app.registry, clock=clock.now,
        uuid_factory=lambda: "effect-a", worker_id="worker-a",
        lease_ttl_seconds=60, crash_hook=crash,
    )
    with pytest.raises(ProcessDeath):
        first.execute_one(record.id)
    clock.advance(61)
    second = ExecutionEngine(
        app.units, app.registry, clock=clock.now,
        uuid_factory=lambda: "effect-b", worker_id="worker-b",
        lease_ttl_seconds=60,
    )
    outcome = second.execute_one(record.id)
    assert outcome.reason_code == "CLEANUP_QUARANTINED"
    with app.units.read() as conn:
        final = OperationRepository(conn).require(record.id)
    assert final.state is OperationState.SUCCEEDED
    assert final.result_code == "CLEANUP_QUARANTINED"
    assert not staged.exists()
    destination = (
        app.paths.model_quarantine_dir / "cleanup" / record.id
        / selected["target_id"]
    )
    assert destination.is_dir()
    assert (destination / "partial.bin").read_bytes() == b"model-part"
