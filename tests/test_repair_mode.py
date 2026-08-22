"""R2 hardening P0-1: failed legacy import must enter an explicit repair
gate, never publish an empty database, and block normal commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.paths import AppPaths


def _corrupt_legacy(root: Path) -> AppPaths:
    paths = AppPaths.temporary(root)
    paths.ensure_directories()
    paths.legacy_state_path.write_text("{definitely not json", encoding="utf-8")
    return paths


def test_failed_import_publishes_nothing(tmp_path):
    paths = _corrupt_legacy(tmp_path / "root")
    app = Application.compose(paths)
    assert app.store is None, "repair mode must not construct a store"
    assert app.repair_reason, "repair mode must carry an explicit reason"
    assert not paths.database_path.exists(), (
        "failed import must not create or publish a database"
    )
    # The JSON backup is preserved exactly as it was.
    assert paths.legacy_state_path.read_text(encoding="utf-8") == "{definitely not json"


def test_repair_mode_blocks_normal_commands_and_allows_retry(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("bc250_llm_mode.__main__.configure_logging", lambda *_a: None)
    profile = AppPaths.for_home()
    profile.legacy_state_path.parent.mkdir(parents=True, exist_ok=True)
    profile.legacy_state_path.write_text("{broken", encoding="utf-8")
    root = profile.legacy_state_path.parent

    from bc250_llm_mode.__main__ import main

    # Normal commands are refused with the dedicated exit code.
    assert main(["llm", "status"]) == 78
    assert not (root / "state.db").exists()
    assert main(["setup"]) == 78

    # Diagnosis of the migration failure itself stays available.
    assert main(["repair-status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["repair_required"] is True
    assert report["reason"]

    # Retry after the source is repaired succeeds and publishes the db.
    (root / "state.json").write_text(json.dumps({"disclaimer_ack": True}), encoding="utf-8")
    assert main(["repair-retry"]) == 0
    assert (root / "state.db").exists()

    # Normal composition works again afterwards.
    fresh = Application.compose(AppPaths.for_home())
    assert fresh.store is not None
    assert fresh.repair_reason is None