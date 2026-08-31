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


def _patch_cli(monkeypatch):
    monkeypatch.setattr(cli_module, "configure_logging", lambda *_a: None)
    runner = SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        emit=lambda *_l: None,
    )
    monkeypatch.setattr(cli_module, "CommandRunner", lambda *_a, **_k: runner)
    return runner


def _isolated_home(tmp_path, monkeypatch):
    """Compose against a temporary HOME; legacy JSON is never involved."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_llm_status_needs_no_ack_and_prints_json(tmp_path, monkeypatch, capsys):
    _patch_cli(monkeypatch)
    monkeypatch.setattr(cli_module, "service_status", lambda st, rn: {"active": False})
    _isolated_home(tmp_path, monkeypatch)
    assert main(["llm", "status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"active": False}
    # A read-only command must not write an acknowledgment to disk.
    from bc250_llm_mode.app import Application

    state = Application.compose().read_model()
    assert not state.get("disclaimer_ack")


def test_llm_start_requires_acknowledgment(tmp_path, monkeypatch):
    _isolated_home(tmp_path, monkeypatch)
    assert cli(["llm", "start"]) == 1


def test_llm_stop_with_ack_runs_and_prints_result(tmp_path, monkeypatch, capsys):
    _patch_cli(monkeypatch)
    stops = []
    monkeypatch.setattr(
        cli_module, "stop_service", lambda st, rn: stops.append(True) or {"active": False}
    )
    _isolated_home(tmp_path, monkeypatch)
    from bc250_llm_mode.app import Application

    app = Application.compose()
    state = app.read_model()
    app.commit_settings_changes(state, {**state, "disclaimer_ack": True})
    assert main(["llm", "stop"]) == 0
    assert stops == [True]
    assert json.loads(capsys.readouterr().out) == {"active": False}


def test_llm_handler_failures_exit_nonzero_with_message(tmp_path, monkeypatch, capsys):
    _patch_cli(monkeypatch)

    def broken(st, rn):
        raise RuntimeError("systemctl exploded")

    monkeypatch.setattr(cli_module, "restart_and_wait", broken)
    _isolated_home(tmp_path, monkeypatch)
    from bc250_llm_mode.app import Application

    app = Application.compose()
    state = app.read_model()
    app.commit_settings_changes(state, {**state, "disclaimer_ack": True})
    assert cli(["llm", "restart"]) == 1
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


def test_default_home_replaces_legacy_dashboard_refresh(tmp_path):
    """GUI-4 Home uses the composed snapshot, not the legacy probe panel."""
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.gui import Wizard
    from bc250_llm_mode.gui.home_page import HomePage
    from bc250_llm_mode.paths import AppPaths

    paths = AppPaths.temporary(tmp_path)
    application = Application.compose(paths)
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "setup_complete": True, "disclaimer_ack": True}
    )
    wizard = Wizard(application, management=True)
    assert isinstance(wizard._page, HomePage)
    assert "dashboard_status_vars" not in vars(wizard)
    wizard._page.refresh()
    assert wizard._page._view is not None


def test_cli_parser_accepts_every_documented_action():
    parser = _parser()
    table = [
        ["llm", "status"], ["llm", "ensure"], ["webui", "status"],
        ["webui", "update"],
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
