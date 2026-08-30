from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.app import Application  # noqa: E402
from bc250_llm_mode.gui import Wizard  # noqa: E402
from bc250_llm_mode.gui.routes import Route  # noqa: E402
from bc250_llm_mode.gui.shell import ApplicationWindow  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402


def _application(tmp_path, *, complete=True):
    application = Application.compose(AppPaths.temporary(tmp_path))
    if complete:
        state = application.read_model()
        application.commit_settings_changes(
            state, {**state, "setup_complete": True, "disclaimer_ack": True}
        )
    return application


def test_default_wizard_compatibility_is_the_one_application_window():
    assert Wizard is ApplicationWindow


def test_management_window_constructs_with_persistent_regions(tmp_path):
    window = ApplicationWindow(_application(tmp_path), management=True)
    assigned = vars(window)
    assert assigned["_route"] is Route.HOME
    assert "notice_bar" in assigned
    assert "drawer" in assigned
    assert "_refresh_coordinator" in assigned
    assert len(assigned["_nav_buttons"]) == 10


def test_activity_navigation_is_in_window(tmp_path):
    window = ApplicationWindow(_application(tmp_path), management=True)
    window.navigate(Route.ACTIVITY)
    assert window._route is Route.ACTIVITY


def test_updates_navigation_is_one_window_and_status_only(tmp_path):
    window = ApplicationWindow(_application(tmp_path), management=True)
    window.navigate(Route.UPDATES)
    assert window._route is Route.UPDATES
    assert window._page.__class__.__name__ == "UpdatesPage"


def test_default_shell_source_creates_no_toplevel_or_messagebox():
    source = Path("bc250_llm_mode/gui/shell.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "Toplevel" not in names
    assert "messagebox" not in source
