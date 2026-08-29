"""Headless GUI contract: Wizard must keep its full surface through refactors.

tkinter is stubbed so Wizard can be *constructed* without a display; every
widget call lands on an inert recorder. The method list below is the frozen
surface of the pre-refactor monolith — the split must preserve every name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.gui import Wizard  # noqa: E402



WIZARD_METHODS = frozenset({
    "__init__", "_build_shell", "emit", "_drain_events", "runner",
    "commit_narrow", "refresh_snapshot",
    "_clear", "show_step", "_body_label", "_hardware", "_disclaimer",
    "_update_ack", "_llm_mode", "_environment", "_catalog", "_add_model_folder",
    "_model_changed", "_fit", "_labeled_spin", "_optimize",
    "_balanced_optimizations", "_update_optimization_fit",
    "_disable_host_optimizations", "_collect_optimization_settings",
    "_download", "_prepare", "_server", "_webui", "_complete",
    "_dashboard_service_card", "_populate_dashboard_models",
    "_dashboard_action", "_refresh_dashboard", "_poll_dashboard",
    "_open_shared_webui", "_dashboard_use_model", "_refresh_catalog_browser",
    "_dashboard_install_catalog_model", "_dashboard_benchmark",
    "_dashboard_change_context", "_dashboard_tail", "_dashboard_desktop_mode",
    "_dashboard_enter_llm_mode", "_manage_optimizations", "_repair", "back",
    "_work", "_advance", "continue_step", "_after_llm_mode", "_finish_setup",
    "_finish_optimization_management", "_launch_chat_terminal",
})

KEY_ATTRIBUTES = (
    "state_data", "events", "_page",
)

DASHBOARD_TREE_ATTRIBUTES = ()


@pytest.fixture
def wizard(tmp_path):
    """Fully isolated wizard: every writable path lives under tmp_path."""
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.paths import AppPaths

    paths = AppPaths.temporary(tmp_path)
    application = Application.compose(paths)
    state = application.read_model()
    application.commit_settings_changes(
        state,
        {**state, "setup_complete": True, "disclaimer_ack": True},
    )
    return Wizard(application, management=True)


def test_wizard_preserves_the_full_monolith_surface():
    mro_attrs = set()
    for klass in Wizard.__mro__:
        mro_attrs.update(vars(klass))
    missing = WIZARD_METHODS - mro_attrs
    assert not missing, f"GUI refactor dropped methods: {sorted(missing)}"


def test_wizard_constructs_headless_with_all_widgets(wizard):
    # Real assignments land in the instance __dict__; inherited stub __getattr__
    # would otherwise mask everything.
    assigned = set(vars(wizard))
    for attr in KEY_ATTRIBUTES + DASHBOARD_TREE_ATTRIBUTES:
        assert attr in assigned, attr


def test_wizard_step_navigation_contract():
    import bc250_llm_mode.gui as gui_module

    assert len(gui_module.STEP_TITLES) == 11


def test_task_oriented_home_consumes_the_composed_snapshot(wizard):
    """The default Home page reads the same composed service as CLI/support."""
    from bc250_llm_mode.gui.home_page import HomePage

    assert isinstance(wizard._page, HomePage)
    assert wizard._page._view is not None
    assert len(wizard._page._view.cards) == 5
    wizard._page.refresh()
    assert wizard._page._view.primary.code
