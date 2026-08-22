"""Phase 0: llm CLI branch, run_gui export, dashboard deferred imports, parser."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode import __main__ as cli_module  # noqa: E402
from bc250_llm_mode.__main__ import _parser, cli, main  # noqa: E402
from bc250_llm_mode.state import StateStore  # noqa: E402


def _patch_cli(monkeypatch):
    monkeypatch.setattr(cli_module, "configure_logging", lambda *_a: None)
    runner = SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        emit=lambda *_l: None,
    )
    monkeypatch.setattr(cli_module, "CommandRunner", lambda *_a, **_k: runner)
    return runner


def test_llm_status_needs_no_ack_and_prints_json(tmp_path, monkeypatch, capsys):
    _patch_cli(monkeypatch)
    monkeypatch.setattr(cli_module, "service_status", lambda st, rn: {"active": False})
    state_file = tmp_path / "state.json"
    assert main(["--state", str(state_file), "llm", "status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"active": False}
    # A read-only command must not write an acknowledgment to disk.
    saved = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    assert not saved.get("disclaimer_ack")


def test_llm_start_requires_acknowledgment(tmp_path):
    assert cli(["--state", str(tmp_path / "state.json"), "llm", "start"]) == 1


def test_llm_stop_with_ack_runs_and_prints_result(tmp_path, monkeypatch, capsys):
    _patch_cli(monkeypatch)
    stops = []
    monkeypatch.setattr(
        cli_module, "stop_service", lambda st, rn: stops.append(True) or {"active": False}
    )
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    state = store.load()
    state["disclaimer_ack"] = True
    store.save(state)
    assert main(["--state", str(state_file), "llm", "stop"]) == 0
    assert stops == [True]
    assert json.loads(capsys.readouterr().out) == {"active": False}


def test_llm_handler_failures_exit_nonzero_with_message(tmp_path, monkeypatch, capsys):
    _patch_cli(monkeypatch)

    def broken(st, rn):
        raise RuntimeError("systemctl exploded")

    monkeypatch.setattr(cli_module, "restart_and_wait", broken)
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    state = store.load()
    state["disclaimer_ack"] = True
    store.save(state)
    assert cli(["--state", str(state_file), "llm", "restart"]) == 1
    assert "systemctl exploded" in capsys.readouterr().err


def test_run_gui_reaches_mainloop_of_the_composed_wizard(monkeypatch):
    from bc250_llm_mode import gui

    loops = []

    class FakeWizard:
        def __init__(self, application=None, management=False):
            self.application = application
            self.management = management

        def mainloop(self):
            loops.append((self.application, self.management))

    monkeypatch.setattr(gui, "Wizard", FakeWizard)
    gui.run_gui("application-sentinel", management=True)
    assert loops and loops[-1][1] is True


def test_run_gui_translates_missing_display(monkeypatch):
    import tkinter as tk

    from bc250_llm_mode import gui

    class BrokenWizard:
        def __init__(self, *a, **k):
            raise tk.TclError("no display")

    monkeypatch.setattr(gui, "Wizard", BrokenWizard)
    with pytest.raises(RuntimeError, match="graphical display"):
        gui.run_gui("application-sentinel")


def test_dashboard_refresh_executes_thermal_import(monkeypatch, tmp_path):
    """The refresh path must import ..thermals successfully at runtime."""
    from _gui_stubs import install as _install  # noqa: F401

    import bc250_llm_mode.gui.dashboard as dashboard
    import bc250_llm_mode.thermals as thermals_module
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.gui import Wizard
    from bc250_llm_mode.paths import AppPaths

    paths = AppPaths.temporary(tmp_path)
    paths.ensure_directories()
    store = StateStore(paths.state_path)
    state = store.load()
    state["setup_complete"] = True
    store.save(state)
    wizard = Wizard(Application.wrap(store), management=True)

    monkeypatch.setattr(dashboard, "service_status", lambda st, rn: {"active": False, "enabled": False})
    monkeypatch.setattr(dashboard, "open_webui_status", lambda st, rn: {"installed": False, "running": False})
    monkeypatch.setattr(dashboard, "tailscale_status", lambda rn: {
        "installed": False, "connected": False, "daemon_active": False, "backend_state": "offline",
    })
    monkeypatch.setattr(dashboard, "https_sharing_status", lambda st, rn: {"available": False, "status": "Off"})
    hardware = SimpleNamespace(to_dict=lambda: {
        "valid": True, "supported": True, "risk": "none",
        "estimated_bios_split": "~12/4", "detected_host_usable_gib": 3.6,
    }, valid=True)
    monkeypatch.setattr(dashboard, "detect_hardware", lambda *a, **k: hardware)
    monkeypatch.setattr(dashboard, "analyze_memory_profile", lambda hw: SimpleNamespace(to_dict=lambda: hw.to_dict()))
    monkeypatch.setattr(thermals_module, "read_gpu_temperature", lambda: 55.0)

    captured = {}

    class RecordingVar:
        def set(self, value):
            captured.setdefault("last", []).append(value)

    for key in list(wizard.dashboard_status_vars):
        wizard.dashboard_status_vars[key] = RecordingVar()

    monkeypatch.setattr(type(wizard), "_work", lambda self, action, done: (action(), done()))
    wizard._refresh_dashboard()

    assert "GPU 55" in captured["last"][-1]


def test_cli_parser_accepts_every_documented_action():
    parser = _parser()
    table = [
        ["llm", "status"], ["llm", "ensure"], ["webui", "status"],
        ["tailscale", "status"], ["serve", "status"],
        ["models", "list"], ["models", "search", "code"], ["models", "recommend"],
        ["ctx", "8192"], ["slots", "4"], ["boot-policy"], ["boot-policy", "desktop"],
        ["logs"], ["logs", "setup", "--lines", "10"],
        ["bench", "--max-tokens", "32"], ["thermals", "status"], ["thermals", "reset"],
        ["llamacpp", "status"], ["llamacpp", "update", "--tag", "b8000"],
        ["llamacpp", "rollback"],
    ]
    for argv in table:
        assert parser.parse_args(argv) is not None, argv
