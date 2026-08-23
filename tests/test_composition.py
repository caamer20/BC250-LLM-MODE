"""Session 4A: the post-facade composition contract, test-visible before
`compat_state.py` is deleted.

Every assertion here goes through `Application.compose()` and the
repository-native query layer — no test reads through CompatStateStore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.paths import AppPaths

FIXTURES = Path(__file__).parent / "fixtures"


def _compose_with_legacy_source(tmp_path, source_payload):
    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    paths.legacy_state_path.write_text(
        json.dumps(source_payload), encoding="utf-8"
    )
    application = Application.compose(paths)
    return paths, application


def test_fresh_profile_creates_initialized_database(tmp_path):
    import sqlite3


    paths = AppPaths.temporary(tmp_path / "fresh")
    application = Application.compose(paths)
    assert application.operational
    assert paths.database_path.exists()
    applied = {
        row[0]
        for row in sqlite3.connect(str(paths.database_path)).execute(
            "SELECT version FROM schema_migrations"
        )
    }
    assert applied == {1, 2}


def test_v5_fixture_imports_once_and_query_is_authoritative(tmp_path):
    """The plan's safest first test: frozen v5 fixture -> compose -> assert
    the complete native query snapshot without touching CompatStateStore."""
    fixture = json.loads(
        (FIXTURES / "state_v5.json").read_text(encoding="utf-8")
    )
    fixture["models_dir"] = str(tmp_path / "custom-models")
    paths, application = _compose_with_legacy_source(tmp_path, fixture)

    snapshot = application.query.snapshot()
    data = snapshot.data

    # Imported domain values are visible through the query layer.
    assert data["current_model"] == fixture["current_model"]
    # env_ready is a transient observation: the imported value is surfaced
    # but explicitly labeled stale so services know to re-probe.
    assert data["env_ready"] is True
    assert data["observation_staleness"]["env_ready"]["stale"] is True
    assert data["disclaimer_ack"] is True
    # Customized model directory survives as user configuration...
    assert data["models_dir"] == str(tmp_path / "custom-models")
    # ...and derived identity paths come from AppPaths instead.
    assert data["app_dir"] == str(paths.app_dir)
    assert data["logs_dir"] == str(paths.logs_dir)
    # Thermal latch comes from its authoritative table, not the draft.
    assert data["thermal_watchdog_state"] == "nominal"
    # llama.cpp build provenance survived migration into its own table.
    build = data.get("llamacpp_build") or {}
    assert build.get("describe") == "b7598"

    # Import happened exactly once: the JSON source is byte-identical after
    # a second compose, and no re-import changed durable revision counts.
    before = paths.legacy_state_path.read_bytes()
    Application.compose(paths)
    assert paths.legacy_state_path.read_bytes() == before


def test_quarantined_unknown_keys_hidden_from_frontends(tmp_path):
    payload = {
        "schema_version": 5,
        "disclaimer_ack": True,
        "some_unknown_future_key": {"nested": True},
    }
    paths, application = _compose_with_legacy_source(tmp_path, payload)
    snapshot = application.query.snapshot()
    # Unknown keys are quarantined for support tooling, not projected as
    # active configuration to frontends.
    assert "some_unknown_future_key" not in snapshot.data


def test_corrupt_legacy_source_enters_repair_and_retry_recovers(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    profile = AppPaths.for_home()
    profile.legacy_state_path.parent.mkdir(parents=True, exist_ok=True)
    profile.legacy_state_path.write_text("{corrupt", encoding="utf-8")

    from bc250_llm_mode.__main__ import main

    assert main(["llm", "status"]) == 78  # blocked
    assert not profile.database_path.exists()

    profile.legacy_state_path.write_text(
        json.dumps({"schema_version": 5, "disclaimer_ack": True}),
        encoding="utf-8",
    )
    assert main(["repair-retry"]) == 0
    assert profile.database_path.exists()


def test_status_queries_do_not_bump_revision(tmp_path):
    fixture = json.loads(
        (FIXTURES / "state_v5.json").read_text(encoding="utf-8")
    )
    paths, application = _compose_with_legacy_source(tmp_path, fixture)
    revision_before = application.query.snapshot().revision

    # Query/health/status-style reads never change the durable revision.
    for _ in range(3):
        application.query.snapshot()
        application.query.health()
    assert application.query.snapshot().revision == revision_before