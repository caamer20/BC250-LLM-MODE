from __future__ import annotations

from bc250_llm_mode.app import Application
from bc250_llm_mode.artifact_storage import read_receipt, write_receipt
from bc250_llm_mode.operations.model import OperationState, OperationType
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.paths import AppPaths


def _app(tmp_path):
    app = Application.compose(AppPaths.temporary(tmp_path))
    app.setup.acknowledge_safety()
    return app


def _stage(app, operation_id="undo-owner"):
    with app.units.begin() as conn:
        repository = OperationRepository(conn)
        repository.create(
            operation_type=OperationType.MODEL_ACTIVATE,
            request={},
            operation_id=operation_id,
            surface="test",
        )
        repository.record_terminal_result(
            operation_id,
            terminal_state=OperationState.FAILED_SAFE,
            error_code="TEST_TERMINAL",
        )
    root = app.paths.model_staging_dir / operation_id
    root.mkdir(parents=True)
    (root / "partial.bin").write_bytes(b"retained")
    return root


def _quarantine(app):
    staged = _stage(app)
    preview = app.storage_cleanup.preview()
    result = app.storage_cleanup.apply(
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
        requested_by="test",
    )
    assert result.ok
    return staged, preview.selected[0]["target_id"], result.operation_id


def test_undo_is_derived_and_runs_as_child_cleanup_operation(tmp_path):
    app = _app(tmp_path)
    staged, target_id, source_id = _quarantine(app)

    candidates = app.undo.list()
    assert len(candidates) == 1
    assert candidates[0]["source_operation_id"] == source_id
    assert candidates[0]["target_id"] == target_id
    assert candidates[0]["inverse_operation"] == "STORAGE_CLEANUP/RESTORE"
    preview = app.undo.preview(candidates[0]["undo_id"])
    assert preview.ready
    result = app.undo.run(
        preview.undo_id,
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert result.ok
    assert result.result_code == "UNDO_RESTORE_VERIFIED"
    assert staged.is_dir()
    assert (staged / "partial.bin").read_bytes() == b"retained"
    with app.units.read() as conn:
        child = OperationRepository(conn).require(result.operation_id)
    assert child.parent_operation_id == source_id
    assert child.operation_type is OperationType.STORAGE_CLEANUP
    assert app.undo.list() == []
    assert app.undo.preview(preview.undo_id).reason_code == "UNDO_SUPERSEDED"


def test_undo_requires_exact_preview_and_never_mutates_on_stale_token(tmp_path):
    app = _app(tmp_path)
    staged, _target_id, _source_id = _quarantine(app)
    undo_id = app.undo.list()[0]["undo_id"]
    preview = app.undo.preview(undo_id)
    result = app.undo.run(
        undo_id,
        preview_digest="0" * 64,
        confirmation_token=preview.confirmation_token,
    )
    assert result.outcome == "REFUSED"
    assert result.result_code == "PREVIEW_STALE"
    assert not staged.exists()
    assert app.undo.preview(undo_id).ready


def test_expired_cleanup_is_not_undoable_and_reports_expiry(tmp_path):
    app = _app(tmp_path)
    staged, target_id, source_id = _quarantine(app)
    receipt_path = (
        app.paths.model_quarantine_dir / "cleanup" / source_id
        / f"{target_id}.cleanup.json"
    )
    receipt = read_receipt(receipt_path)
    receipt["retention_until"] = "2000-01-01T00:00:00Z"
    write_receipt(receipt_path, receipt)
    undo_id = f"cleanup:{source_id}:{target_id}"
    assert app.undo.list() == []
    preview = app.undo.preview(undo_id)
    assert not preview.ready
    assert preview.reason_code == "UNDO_EXPIRED"
    result = app.undo.run(
        undo_id,
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert result.result_code == "UNDO_EXPIRED"
    assert not staged.exists()


def test_unknown_or_malformed_undo_never_becomes_a_generic_inverse(tmp_path):
    app = _app(tmp_path)
    for undo_id in ("anything", "cleanup:missing:target", "cleanup:../x:y"):
        preview = app.undo.preview(undo_id)
        assert not preview.ready
        assert preview.reason_code == "UNDO_SUPERSEDED"

