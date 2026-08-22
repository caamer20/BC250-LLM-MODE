"""R0.1/R1.1: path-authority injection — composition must be the only root."""

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
from bc250_llm_mode.__main__ import cli, main  # noqa: E402
from bc250_llm_mode.app import Application, load_state_with_paths  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402
from bc250_llm_mode.state import StateStore  # noqa: E402


def test_composition_never_writes_outside_the_injected_root(tmp_path):
    """R0.1 acceptance: with a sentinel HOME, composing the application and
    constructing the wizard creates nothing beneath HOME."""
    from _gui_stubs import install as _install_gui

    _install_gui()

    sentinel_home = tmp_path / "sentinel-home"
    sentinel_home.mkdir()

    import os

    real_home = os.environ.get("HOME")
    os.environ["HOME"] = str(sentinel_home)
    try:
        from bc250_llm_mode.gui import Wizard

        paths = AppPaths.temporary(tmp_path / "approot")
        paths.ensure_directories()
        store = StateStore(paths.state_path)
        state = store.load()
        state.update(
            setup_complete=True,
            logs_dir=str(paths.logs_dir),
            models_dir=str(paths.models_dir),
            app_dir=str(paths.app_dir),
            disclaimer_ack=True,
        )
        store.save(state)

        application = Application.compose(paths)
        wizard = Wizard(store, management=True, paths=paths)
        handler_target = str(wizard.runner().logger.handlers[0].baseFilename)
    finally:
        if real_home is not None:
            import os as _os

            _os.environ["HOME"] = real_home

    # A handler targeting THIS root must exist (the global logger may carry
    # handlers from other tests/roots; ours must be present, not first).
    assert any(
        str(paths.logs_dir) in str(getattr(h, "baseFilename", ""))
        for h in wizard.runner().logger.handlers
    ), "no log handler targets the injected logs_dir"
    # Nothing may exist beneath the sentinel home.
    created = [str(p) for p in sentinel_home.rglob("*")]
    assert not any("bc250-llm-mode" in p for p in created), (
        f"application wrote beneath sentinel HOME: {created}"
    )


def test_load_state_keeps_custom_models_dir_but_follows_profile_identity(tmp_path):
    persisted_default = str(Path("~/bc250-llm-mode-placeholder").expanduser())
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state["models_dir"] = "/mnt/custom/models"
    store.save(state)

    profile = AppPaths.temporary(tmp_path / "profile")
    loaded = load_state_with_paths(store, profile)

    # Installation identity always follows the composed profile.
    assert loaded["app_dir"] == str(profile.app_dir)
    assert loaded["logs_dir"] == str(profile.logs_dir)
    # An explicitly customized model location is preserved.
    assert loaded["models_dir"] == "/mnt/custom/models"


def test_load_state_redirects_untouched_default_models_dir(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()  # defaults only; models_dir untouched

    profile = AppPaths.temporary(tmp_path / "profile")
    loaded = load_state_with_paths(store, profile)
    assert loaded["models_dir"] == str(profile.models_dir)


def test_cli_state_flag_uses_legacy_json_mode(tmp_path, monkeypatch, capsys):
    """Explicit --state opts into transitional legacy mode: JSON only, no
    database is created or imported."""
    monkeypatch.setattr(cli_module, "configure_logging", lambda *_a: None)
    runner = SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        emit=lambda *_l: None,
    )
    monkeypatch.setattr(cli_module, "CommandRunner", lambda *_a, **_k: runner)
    monkeypatch.setattr(cli_module, "service_status", lambda st, rn: {"active": False})

    custom_root = tmp_path / "custom-root"
    state_file = custom_root / "state.json"
    StateStore(state_file).save({"disclaimer_ack": True})

    assert main(["--state", str(state_file), "llm", "status"]) == 0
    json.loads(capsys.readouterr().out)
    assert not (custom_root / "state.db").exists(), (
        "--state is legacy JSON mode; it must not create a database"
    )
