"""R0.1/R1.1: path-authority injection — composition must be the only root."""

import json
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.__main__ import main  # noqa: E402
from bc250_llm_mode.app import Application  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402


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
        application = Application.compose(paths)
        wizard = Wizard(application, management=True)
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


def test_import_keeps_custom_models_dir_but_query_follows_profile_identity(tmp_path):
    """A customized models_dir survives import; derived paths are never
    persisted and are reconstructed by the query layer from AppPaths."""
    from _native import NativeApp

    store = NativeApp(tmp_path)
    store.set_settings({"models_dir_custom": "/mnt/custom/models"})

    profile = store.paths
    loaded = store.load()

    # Installation identity always follows the composed profile.
    assert loaded["app_dir"] == str(profile.app_dir)
    assert loaded["logs_dir"] == str(profile.logs_dir)
    # An explicitly customized model location is preserved.
    assert loaded["models_dir"] == "/mnt/custom/models"


def test_untouched_default_models_dir_redirects_to_the_profile(tmp_path):
    from _native import NativeApp

    store = NativeApp(tmp_path)
    profile = store.paths
    loaded = store.load()
    assert loaded["models_dir"] == str(profile.models_dir)


def test_cli_state_flag_is_rejected_before_any_file_changes(
    tmp_path, monkeypatch, capsys
):
    """--state is a deprecated repair-retry alias, never a runtime mode."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    custom_root = tmp_path / "custom-root"
    custom_root.mkdir()
    state_file = custom_root / "state.json"
    state_file.write_text(json.dumps({"disclaimer_ack": True}), encoding="utf-8")

    assert main(["--state", str(state_file), "llm", "status"]) == 2
    assert "--state" in capsys.readouterr().err
    # Rejection happens before composition: nothing was created anywhere.
    assert not (custom_root / "state.db").exists()
    assert not (home / ".bc250-llm-mode" / "state.db").exists()
