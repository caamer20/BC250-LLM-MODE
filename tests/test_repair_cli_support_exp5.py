from __future__ import annotations

import json
from pathlib import Path

from bc250_llm_mode import __main__ as entry
from bc250_llm_mode.app import Application
from bc250_llm_mode.operations.model import OperationState, OperationType
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repair_commands import RepairProbe, RepairResult


def _app(tmp_path):
    app = Application.compose(AppPaths.temporary(tmp_path))
    app.setup.acknowledge_safety()
    return app


def _patch_app(monkeypatch, app):
    monkeypatch.setattr(
        Application, "compose", classmethod(lambda cls, *args, **kwargs: app)
    )


def _stage(app):
    with app.units.begin() as conn:
        repository = OperationRepository(conn)
        repository.create(
            operation_type=OperationType.MODEL_ACTIVATE,
            request={}, operation_id="cli-cleanup-owner", surface="test",
        )
        repository.record_terminal_result(
            "cli-cleanup-owner", terminal_state=OperationState.FAILED_SAFE,
            error_code="TEST_TERMINAL",
        )
    root = app.paths.model_staging_dir / "cli-cleanup-owner"
    root.mkdir(parents=True)
    (root / "partial").write_bytes(b"bytes")


def test_repair_cli_list_preview_and_unknown_are_typed(
    tmp_path, monkeypatch, capsys
):
    app = _app(tmp_path)
    _patch_app(monkeypatch, app)
    assert entry.main(["repair", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 15
    assert all("preview_digest" in item for item in listed)

    assert entry.main([
        "repair", "preview", "rebuild-support-bundle",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["action_id"] == "rebuild-support-bundle"
    assert preview["outcome"] == "READY"

    assert entry.main(["repair", "preview", "not-an-action"]) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["result_code"] == "UNKNOWN_REPAIR_ACTION"


def test_storage_and_undo_cli_share_durable_services(
    tmp_path, monkeypatch, capsys
):
    app = _app(tmp_path)
    _stage(app)
    _patch_app(monkeypatch, app)
    assert entry.main(["storage", "cleanup", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["selected_count"] == 1
    assert preview["deleted_anything"] is False
    assert entry.main([
        "storage", "cleanup", "--apply",
        "--preview", preview["preview_digest"],
        "--confirm", preview["confirmation_token"],
    ]) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["result_code"] == "CLEANUP_QUARANTINED"

    assert entry.main(["undo", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    undo_id = listed[0]["undo_id"]
    assert entry.main(["undo", "preview", undo_id]) == 0
    undo_preview = json.loads(capsys.readouterr().out)
    assert entry.main([
        "undo", "run", undo_id,
        "--preview", undo_preview["preview_digest"],
        "--confirm", undo_preview["confirmation_token"],
    ]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["result_code"] == "UNDO_RESTORE_VERIFIED"


def test_support_handoff_is_bounded_closed_and_secret_free():
    canary = "hf_secret-canary"
    result = RepairResult(
        action_id="recover-durable-operation",
        target_id="operation-1",
        outcome="RECOVERY_REQUIRED",
        result_code="RECOVERY_BARRIER_MANUAL",
        probe=RepairProbe(False, "OPERATION_RECOVERY_PENDING", True),
        operation_id="operation-1",
        support_relevance="OPERATIONS",
        one_time_secret=canary,
    )
    payload = result.to_dict()
    serialized = json.dumps(payload, sort_keys=True)
    assert canary not in serialized
    handoff = payload["support_handoff"]
    assert handoff["support_bundle_available"] is True
    assert handoff["support_bundle_uploaded"] is False
    assert handoff["prior_state_survives"] is True
    assert len(handoff["offline_commands"]) <= 3
    assert handoff["offline_commands"][0] == [
        "bc250-llm-mode", "repair", "verify",
        "recover-durable-operation", "operation-1",
    ]
    assert all(
        not any(character in argument for character in ";|&`$\n")
        for command in handoff["offline_commands"] for argument in command
    )


def test_native_repair_route_and_page_are_real_package_code():
    from bc250_llm_mode.gui.routes import Route

    assert Route.REPAIR.value == "maintenance/repair"
    package = Path(__file__).parents[1] / "bc250_llm_mode" / "gui"
    source = (package / "shell.py").read_text(encoding="utf-8")
    assert "if target is Route.REPAIR:" in source
    repair_source = (package / "repair_page.py").read_text(encoding="utf-8")
    assert "class RepairPage" in repair_source
    assert "This page is being converted" not in repair_source
