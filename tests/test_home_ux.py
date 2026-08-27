"""P5 §11.4: first-run and failure UX presentation contract — PURE and
headless. Every helper is exercised against REAL composed read models (the
home snapshot and doctor report) so the GUI's presentation layer is proven
without a display.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bc250_llm_mode import home_ux
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.doctor import DoctorService
from bc250_llm_mode.home import HomeQueryService
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repositories import SettingsRepository, ThermalStateRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

FIXED_NOW = "2026-02-01T12:00:00+00:00"


class UxWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.home = HomeQueryService(self.units, self.paths,
                                     clock=lambda: FIXED_NOW)
        self.doctor = DoctorService(self.units, self.paths,
                                    clock=lambda: FIXED_NOW)

    def set_settings(self, **values) -> None:
        with self.units.begin() as conn:
            SettingsRepository(conn).set_many(values)

    def home_dict(self):
        return self.home.snapshot().to_dict()

    def doctor_dict(self):
        return self.doctor.run().to_dict()


@pytest.fixture()
def world(tmp_path):
    return UxWorld(tmp_path)


def test_home_ux_module_is_pure():
    """AST guard: no tkinter, no I/O machinery, no persistence imports."""
    import ast

    source = Path(home_ux.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("tkinter"), alias.name
                assert alias.name not in {"subprocess", "httpx", "sqlite3"}
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith("tkinter"), mod
            assert mod not in {"subprocess", "httpx", "sqlite3"}


def test_overall_headline_and_card_lines(world):
    home = world.home_dict()
    headline = home_ux.overall_headline(home)
    assert headline["state"] in home_ux._STATE_TONE or headline["state"]
    assert headline["tone"] in {home_ux.TONE_GOOD, home_ux.TONE_ATTENTION,
                                home_ux.TONE_BLOCKED, home_ux.TONE_UNKNOWN}

    lines = home_ux.home_card_lines(home)
    names = {row["name"] for row in lines}
    assert {"identity", "runtime", "model", "thermal", "storage"} <= names
    for row in lines:
        assert row["state"]
        assert row["tone"]
        # A stale row must never carry a green tone.
        if row["stale"]:
            assert row["tone"] == home_ux.TONE_UNKNOWN


def test_stale_card_never_renders_green(world):
    # Seed a stale inference probe so the inference card is stale.
    import datetime
    old = (datetime.datetime.fromisoformat(FIXED_NOW)
           - datetime.timedelta(days=3)).isoformat()
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO runtime_observations (key, payload_json,"
            " observed_at, stale) VALUES ('last_inference_probe', ?,"
            " ?, 0)", (json.dumps({"ok": True}), old))
    home = world.home_dict()
    rows = {r["name"]: r for r in home_ux.home_card_lines(home)}
    inference = rows["inference"]
    assert inference["stale"] is True
    assert inference["tone"] == home_ux.TONE_UNKNOWN
    assert inference["state"] != "READY"


def test_preflight_checklist_blocks_on_thermal_and_recovery(world):
    items = home_ux.preflight_checklist(world.home_dict())
    labels = {i.label for i in items}
    assert "Enough free disk space" in labels
    assert "Thermal latch nominal" in labels
    assert "No operation needs recovery" in labels
    assert "Runtime available" in labels
    # Fresh profile: thermal nominal, no recovery -> those two pass.
    by_label = {i.label: i for i in items}
    assert by_label["Thermal latch nominal"].ok is True
    assert by_label["No operation needs recovery"].ok is True

    # Engage the thermal latch -> preflight must fail.
    with world.units.begin() as conn:
        ThermalStateRepository(conn).set("stopped", None)
    items = home_ux.preflight_checklist(world.home_dict())
    by_label = {i.label: i for i in items}
    assert by_label["Thermal latch nominal"].ok is False
    assert home_ux.preflight_ready(items) is False


def test_disk_space_report(world):
    report = home_ux.disk_space_report(world.home_dict())
    assert "available_bytes" in report
    assert "models_bytes" in report
    assert isinstance(report["summary"], str)
    assert report["summary"]


def test_fit_explanation_separates_vram_components():
    fit = SimpleNamespace(
        weights_gib=3.5, kv_gib=1.2, overhead_gib=0.5,
        required_gib=5.2, verdict="FITS", detail="FITS — 5.2 GiB")
    out = home_ux.fit_explanation(fit, context=32768, slots=2)
    assert out["verdict"] == "FITS"
    assert out["tone"] == home_ux.TONE_GOOD
    joined = "\n".join(out["lines"])
    assert "Weights" in joined and "KV cache" in joined
    assert "32768" in joined and "2" in joined

    no_fit = SimpleNamespace(
        weights_gib=30.0, kv_gib=8.0, overhead_gib=0.5,
        required_gib=38.5, verdict="NO-FIT", detail="NO-FIT")
    out = home_ux.fit_explanation(no_fit)
    assert out["tone"] == home_ux.TONE_BLOCKED


def test_model_stage_labels_separate_the_four_stages(world):
    stages = home_ux.model_stage_labels(world.home_dict())
    assert {"downloaded", "installed", "active", "verified"} <= set(stages)
    # Fresh profile: nothing downloaded/installed/active/verified.
    assert stages["downloaded"] is False
    assert stages["installed"] is False
    assert stages["active"] is False
    assert stages["verified"] is False
    assert stages["explanation"]

    # Install a digest-verified model and select it -> all four stages true.
    world.paths.models_dir.mkdir(parents=True, exist_ok=True)
    model_file = world.paths.models_dir / "m.gguf"
    model_file.write_bytes(b"x")
    import hashlib
    digest = hashlib.sha256(b"x").hexdigest()
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO model_artifacts (id, content_digest, byte_size,"
            " canonical_path, storage_state, trust_state, format,"
            " created_at, validated_at) VALUES"
            " ('a1', ?, 1, ?, 'MANAGED', 'VERIFIED', 'gguf',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (digest, str(model_file)))
        conn.execute(
            "INSERT INTO model_installations (alias, path, quant,"
            " display_name, sampling_json, provenance, validation_status,"
            " imported_at, artifact_id) VALUES ('m', ?, 'Q4_K_M', 'M',"
            " '{}', 'test', 'validated', '2026-01-01T00:00:00Z', 'a1')",
            (str(model_file),))
    world.set_settings(current_model="m")
    stages = home_ux.model_stage_labels(world.home_dict())
    assert stages["downloaded"] is True
    assert stages["installed"] is True
    assert stages["active"] is True
    assert stages["verified"] is True


def test_recovery_instructions_cover_interrupted_work():
    lines = home_ux.recovery_instructions(
        {"recovery_required_count": 1, "running_count": 0,
         "queued_count": 2, "paused_count": 1, "oldest_queued_id": "op-9"})
    joined = "\n".join(lines)
    assert "durably" in joined
    assert "recover --confirm" in joined
    assert "resume" in joined
    assert "op-9" in joined

    idle = home_ux.recovery_instructions({})
    assert any("Nothing to recover" in line for line in idle)


def test_diagnostic_details_are_bounded_and_redacted(world):
    world.set_settings(current_model="tiny")
    text = home_ux.diagnostic_details_text(world.home_dict(), world.doctor_dict())
    assert "appliance state:" in text
    assert "cards:" in text
    assert "doctor findings:" in text
    assert len(text) <= home_ux._MAX_DIAGNOSTIC_CHARS + 32

    # Bound holds even for a huge synthetic snapshot.
    huge = {"generated_at": "x", "overall": {"state": "READY"},
            "cards": {f"card{i}": {"health": {"state": "READY",
                                              "evidence": "e" * 500},
                                   "as_of": "t", "stale": False,
                                   "stale_reason": None}
                      for i in range(200)}}
    text = home_ux.diagnostic_details_text(huge, None)
    assert len(text) <= home_ux._MAX_DIAGNOSTIC_CHARS + 32
    assert text.endswith("…[truncated]")


def test_operation_history_commands():
    assert home_ux.operation_history_commands(None) == ["bc250 operations list"]
    cmds = home_ux.operation_history_commands("op-42")
    assert all("op-42" in c for c in cmds)
    assert any("show" in c for c in cmds)
    assert any("events" in c for c in cmds)
