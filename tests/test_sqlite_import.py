"""R2.2: legacy fixture imports and field-mapping semantics."""

import json
from pathlib import Path

import pytest

from bc250_llm_mode.legacy_import import import_legacy_state
from bc250_llm_mode.db import connect
from bc250_llm_mode.paths import AppPaths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def paths(tmp_path):
    paths = AppPaths.temporary(tmp_path)
    paths.ensure_directories()
    return paths


def _write_legacy(paths, payload):
    source = paths.app_dir / "state.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source


def _import_fixture(paths, fixture_name):
    payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    source = _write_legacy(paths, payload)
    report = import_legacy_state(paths, source=source)
    assert report["published"] is True
    return report, source


def test_v5_fixture_imports_into_typed_tables(paths):
    _report, source = _import_fixture(paths, "state_v5.json")
    conn = connect(paths.database_path)
    settings = {
        r["key"]: json.loads(r["value_json"])
        for r in conn.execute("SELECT key, value_json FROM settings")
    }
    assert settings["disclaimer_ack"] is True
    assert settings["optimizations"]["parallel_slots"] == 4
    config = conn.execute("SELECT * FROM runtime_config WHERE id=1").fetchone()
    assert config["model_alias"] == "lfm25-26b"
    assert config["context"] == 8192 and config["slots"] == 4
    models = conn.execute(
        "SELECT alias, provenance, validation_status FROM model_installations"
    ).fetchall()
    assert models[0]["alias"] == "lfm25-26b"
    assert models[0]["provenance"] == "legacy-import"
    assert models[0]["validation_status"] == "unverified"
    assert conn.execute("SELECT COUNT(*) c FROM bench_history").fetchone()["c"] == 1
    provenance = conn.execute(
        "SELECT describe FROM component_provenance WHERE component='llamacpp'"
    ).fetchone()
    assert provenance["describe"] == "b7598"
    assert source.exists(), "legacy JSON must be retained read-only"


def test_v4_fixture_canonicalizes_through_v5_then_imports(paths):
    _report, _source = _import_fixture(paths, "state_v4.json")
    conn = connect(paths.database_path)
    config = conn.execute(
        "SELECT context, slots FROM runtime_config WHERE id=1"
    ).fetchone()
    assert config["context"] == 8192 and config["slots"] == 4
    assert conn.execute("SELECT COUNT(*) c FROM model_installations").fetchone()["c"] == 1


def test_custom_models_dir_preserved_as_setting(paths):
    payload = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    payload["models_dir"] = "/mnt/custom/models"
    _write_legacy(paths, payload)
    import_legacy_state(paths)
    conn = connect(paths.database_path)
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key='models_dir_custom'"
    ).fetchone()
    assert row and json.loads(row["value_json"]) == "/mnt/custom/models"


def test_derived_paths_are_not_stored_as_settings(paths):
    payload = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    payload.update({
        "app_dir": "/home/user/.bc250-llm-mode",
        "logs_dir": "/home/user/.bc250-llm-mode/logs",
        "download_dir": "/home/user/.bc250-llm-mode/models/lfm/source",
    })
    _write_legacy(paths, payload)
    import_legacy_state(paths)
    conn = connect(paths.database_path)
    keys = {r["key"] for r in conn.execute("SELECT key FROM settings")}
    assert not keys & {"app_dir", "logs_dir", "download_dir"}
    stale = conn.execute(
        "SELECT stale FROM runtime_observations WHERE key='download_dir'"
    ).fetchone()
    assert stale and stale["stale"] == 1


def test_latched_thermal_stop_survives_migration(paths):
    payload = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    payload["thermal_watchdog_state"] = "stopped"
    payload["thermal_watchdog_baseline"] = {"gpu_max_mhz": 1850}
    _write_legacy(paths, payload)
    report = import_legacy_state(paths)
    conn = connect(paths.database_path)
    row = conn.execute(
        "SELECT latch_state, baseline_json FROM thermal_state WHERE id=1"
    ).fetchone()
    assert row["latch_state"] == "stopped"
    assert json.loads(row["baseline_json"]) == {"gpu_max_mhz": 1850}
    assert any("latched" in w for w in report["warnings"])


def test_transient_observations_import_marked_stale(paths):
    payload = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    payload["env_ready"] = True
    _write_legacy(paths, payload)
    import_legacy_state(paths)
    conn = connect(paths.database_path)
    row = conn.execute(
        "SELECT stale, payload_json FROM runtime_observations WHERE key='env_ready'"
    ).fetchone()
    assert row["stale"] == 1
    assert json.loads(row["payload_json"]) is True


def test_unknown_keys_preserved_outside_active_configuration(paths):
    payload = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    payload["some_future_field"] = {"anything": [1, 2, 3]}
    _write_legacy(paths, payload)
    import_legacy_state(paths)
    conn = connect(paths.database_path)
    row = conn.execute(
        "SELECT payload_json FROM legacy_import_extras WHERE key='some_future_field'"
    ).fetchone()
    assert json.loads(row["payload_json"]) == {"anything": [1, 2, 3]}


def test_secret_like_keys_are_refused(paths):
    payload = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    payload["hf_api_token"] = "hf_SUPERSECRET"
    payload["webui_password"] = "hunter2"
    _write_legacy(paths, payload)
    report = import_legacy_state(paths)
    conn = connect(paths.database_path)
    all_text = json.dumps([
        dict(r) for r in conn.execute(
            "SELECT key, value_json FROM settings UNION ALL "
            "SELECT key, payload_json FROM legacy_import_extras"
        )
    ])
    assert "hf_SUPERSECRET" not in all_text
    assert "hunter2" not in all_text
    assert any("secret-like" in w for w in report["warnings"])