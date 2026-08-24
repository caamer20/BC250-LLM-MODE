"""Program 0/1/4 production gates: isolation, transactions, tiers, hardening."""

import re
import json
import sys
import threading
from pathlib import Path

import pytest

from bc250_llm_mode.catalog import model_by_id, validation_tier
from bc250_llm_mode.openwebui import IMAGE_REF, install_open_webui
from bc250_llm_mode.paths import AppPaths
from support_legacy_store import LegacyStateStore as StateStore


# --- Program 1.1: explicit application paths -------------------------------


def test_app_paths_temporary_is_isolated_from_home(tmp_path):
    paths = AppPaths.temporary(tmp_path)
    assert str(Path.home()) not in str(paths.app_dir)
    assert paths.state_path == tmp_path / ".bc250-llm-mode" / "state.json"
    paths.ensure_directories()
    assert paths.logs_dir.is_dir() and paths.models_dir.is_dir()


def test_app_paths_rejects_symlinked_owned_directory(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = tmp_path / ".bc250-llm-mode"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        AppPaths.temporary(tmp_path).validate()


def test_gui_contract_fixture_never_writes_home(tmp_path, monkeypatch):
    """Regression: the dashboard fixture must keep logs_dir inside tmp_path."""
    from _gui_stubs import install

    install()

    home_probe = tmp_path / "fake-home"
    home_probe.mkdir()
    monkeypatch.setenv("HOME", str(home_probe))

    from bc250_llm_mode.app import Application
    from bc250_llm_mode.gui import Wizard

    paths = AppPaths.temporary(tmp_path / "isolated")
    application = Application.compose(paths)
    state = application.read_model()
    application.commit_settings_changes(
        state,
        {**state, "setup_complete": True, "disclaimer_ack": True},
    )
    wizard = Wizard(application, management=True)

    assert str(home_probe) not in str(wizard.state_data["logs_dir"])
    assert str(tmp_path) in str(wizard.state_data["logs_dir"])


# --- Program 1.2: transactional state, no lost updates ---------------------


def test_transaction_increments_revision_and_serializes(tmp_path):
    store = StateStore(tmp_path / "state.json")
    result = store.transaction(lambda st: {**st, "current_ctx": 16384})
    assert result["revision"] == 1
    assert store.load()["current_ctx"] == 16384


def test_transaction_cancel_keeps_revision(tmp_path):
    store = StateStore(tmp_path / "state.json")
    before = store.load()

    def cancel(_st):
        return None

    after = store.transaction(cancel)
    assert after["revision"] == before["revision"]


def test_concurrent_append_only_writers_cannot_lose_updates(tmp_path):
    """The exact lost-update scenario: GUI + watchdog append concurrently."""
    store = StateStore(tmp_path / "state.json")
    store.save({"bench_history": []})

    errors = []

    def append_history(tag):
        try:
            def mutate(current):
                history = [i for i in (current.get("bench_history") or []) if isinstance(i, dict)]
                current["bench_history"] = [*history, {"tag": tag}][-20:]
                return current
            store.transaction(mutate)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=append_history, args=(f"writer-{n}",))
        for n in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    final = store.load()["bench_history"]
    assert len(final) == 8, f"lost updates: only {len(final)}/8 entries survived"
    assert len({entry["tag"] for entry in final}) == 8


# --- Program 5.3: release-tier metadata ------------------------------------


def test_v070_catalog_entries_are_supported_and_newer_are_preview():
    assert validation_tier(model_by_id("lfm25-26b")) == "supported"
    assert validation_tier(model_by_id("phi4-14b")) == "supported"
    # Round 2/4 additions have no on-card evidence yet.
    assert validation_tier(model_by_id("ornith-1.5-9b")) == "preview"
    assert validation_tier(model_by_id("qwen38-9b-distill")) == "preview"


def test_explicit_tier_overrides_the_auto_default():
    from bc250_llm_mode.catalog import ModelEntry

    entry = ModelEntry(
        id="x", display_name="X", family="f", task_tags=(), repo="r/x",
        allow_globs={"Q4_K_M": "*"}, params_b=1.0,
        weights_gib_by_quant={"Q4_K_M": 0.6}, kv_kib_per_token=32.0,
        notes="test entry", validation_tier="hardware-validated",
    )
    assert validation_tier(entry) == "hardware-validated"


# --- Program 4.3: Open WebUI container hardening ---------------------------


class RecordingRunner:
    def __init__(self):
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        self.commands.append([str(c) for c in command])
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    def emit(self, message):
        self.messages.append(message)


from types import SimpleNamespace  # noqa: E402


def test_openwebui_image_is_pinned_not_mutable_main():
    assert ":main" not in IMAGE_REF
    assert IMAGE_REF.startswith("ghcr.io/open-webui/open-webui:")


def test_elevated_call_sites_frozen():
    """R1.3 guard: elevation is frozen at the audited count until the R5
    allowlisted helper replaces it (see docs/command_audit.md)."""
    audited = 45
    total = 0
    for path in Path("bc250_llm_mode").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # privilege.py contains the definition; count call sites elsewhere.
        if path.name == "privilege.py":
            continue
        total += len(re.findall(r"\belevated\(", text))
    assert total == audited, (
        f"elevated() call sites changed: {total} != {audited}. "
        "Update docs/command_audit.md and this guard together."
    )


def test_openwebui_create_uses_security_posture(monkeypatch):
    from bc250_llm_mode import openwebui

    monkeypatch.setattr(openwebui.shutil, "which", lambda name: "/usr/bin/podman")
    runner = RecordingRunner()
    state = {"openwebui_container": openwebui.CONTAINER}
    openwebui.install_open_webui(state, runner)
    create = next(c for c in runner.commands if c[:2] == ["podman", "create"])
    for flag in ("--security-opt", "no-new-privileges", "--cap-drop", "all",
                 "--memory", "--pids-limit"):
        assert flag in create, flag
    assert openwebui.IMAGE_REF in create
    assert ":main" not in create
