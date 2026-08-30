from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.desktop_integration import DesktopIntegrationService, render_desktop_entry
from bc250_llm_mode.paths import AppPaths


def _service(tmp_path):
    home = tmp_path / "home with space"
    paths = AppPaths.for_home(home)
    paths.ensure_directories()
    return DesktopIntegrationService(
        paths,
        environ={
            "XDG_DATA_HOME": str(home / "data with space"),
            "XDG_BIN_HOME": str(home / "bin with space"),
        },
        python_executable="/opt/bc250 python/bin/python3",
    )


def test_desktop_entry_quotes_paths_and_never_autostarts(tmp_path):
    service = _service(tmp_path)
    entry = render_desktop_entry(
        launcher=service.targets().launcher, icon=service.targets().icon
    )
    assert 'Exec="' in entry
    assert "Terminal=false" in entry
    assert "StartupNotify=true" in entry
    assert "Autostart" not in entry
    with pytest.raises(ValueError):
        render_desktop_entry(launcher=Path("/tmp/%f"), icon=Path("/tmp/icon"))


def test_install_status_remove_are_owned_and_atomic(tmp_path):
    service = _service(tmp_path)
    installed = service.install()
    assert installed["installed"] is True
    assert service.targets().launcher.stat().st_mode & 0o111
    launcher = service.targets().launcher.read_text()
    assert str(service.paths.application_current_link / "venv/bin/python") in launcher
    assert "eval" not in launcher
    receipt = json.loads(service.targets().receipt.read_text())
    assert set(receipt["digests"]) == {"launcher", "desktop_entry", "icon"}
    removed = service.remove()
    assert removed["removed"] is True
    assert removed["installed"] is False


def test_modified_owned_file_is_never_removed(tmp_path):
    service = _service(tmp_path)
    service.install()
    service.targets().desktop_entry.write_text("user changed this")
    with pytest.raises(RuntimeError, match="refusing to remove modified"):
        service.remove()
    assert service.targets().desktop_entry.exists()
