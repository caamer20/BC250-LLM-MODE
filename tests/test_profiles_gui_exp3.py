"""EXP-3 one-window Profiles page bounds and route integration."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.app import Application  # noqa: E402
from bc250_llm_mode.gui.profiles_page import (  # noqa: E402
    MAX_VISIBLE_PROFILES,
    MAX_VISIBLE_SUGGESTIONS,
    ProfilesPage,
    build_profiles_view,
)
from bc250_llm_mode.gui.routes import MANAGEMENT_ROUTES, Route  # noqa: E402
from bc250_llm_mode.gui.shell import ApplicationWindow  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402


def _application(tmp_path):
    application = Application.compose(AppPaths.temporary(tmp_path))
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "setup_complete": True, "disclaimer_ack": True}
    )
    return application


def test_profiles_view_caps_rows_suggestions_and_copies_inputs():
    profiles = [
        {"profile_id": str(index), "name": f"P{index}"}
        for index in range(MAX_VISIBLE_PROFILES + 5)
    ]
    suggestions = [
        {"code": f"C{index}", "automatic": False}
        for index in range(MAX_VISIBLE_SUGGESTIONS + 5)
    ]
    preview = {"profile_id": "selected"}
    view = build_profiles_view(profiles, preview, suggestions)
    assert len(view.profiles) == MAX_VISIBLE_PROFILES == 37
    assert len(view.suggestions) == MAX_VISIBLE_SUGGESTIONS == 3
    profiles[0]["name"] = "mutated"
    preview["profile_id"] = "mutated"
    assert view.profiles[0]["name"] == "P0"
    assert view.preview["profile_id"] == "selected"


def test_profiles_is_a_primary_route_in_the_same_window(tmp_path):
    window = ApplicationWindow(_application(tmp_path), management=True)
    try:
        assert Route.PROFILES in MANAGEMENT_ROUTES
        window.navigate(Route.PROFILES)
        assert window._route is Route.PROFILES
        assert isinstance(window._page, ProfilesPage)
        assert len(window._nav_buttons) == 5
    finally:
        window.destroy()


def test_profiles_page_uses_shared_lanes_and_no_secondary_ui_loop():
    source = Path("bc250_llm_mode/gui/profiles_page.py").read_text(encoding="utf-8")
    assert "request_observation(" in source
    assert "self.shell._work(" in source
    assert ".after(" not in source
    assert "Toplevel" not in source
    assert "messagebox" not in source
    assert "threading" not in source
