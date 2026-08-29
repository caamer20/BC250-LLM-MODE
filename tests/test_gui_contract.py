"""Headless GUI contract for the unified native application shell.

tkinter is stubbed so ApplicationWindow can be constructed without a display;
every widget call lands on an inert recorder. The contract is behavioral:
private setup method names are deliberately free to change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.gui import ApplicationWindow, Wizard  # noqa: E402
from bc250_llm_mode.gui.routes import SETUP_CHAPTERS, Route  # noqa: E402

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


def test_compatibility_alias_targets_the_one_concrete_window():
    assert Wizard is ApplicationWindow
    assert all("Mixin" not in klass.__name__ for klass in Wizard.__mro__)


def test_wizard_constructs_headless_with_all_widgets(wizard):
    # Real assignments land in the instance __dict__; inherited stub __getattr__
    # would otherwise mask everything.
    assigned = set(vars(wizard))
    for attr in KEY_ATTRIBUTES + DASHBOARD_TREE_ATTRIBUTES:
        assert attr in assigned, attr


def test_setup_is_five_chapters_and_management_is_in_window(wizard):
    assert len(SETUP_CHAPTERS) == 5
    wizard.navigate(Route.ACTIVITY)
    assert wizard._route is Route.ACTIVITY
    assert wizard._page is not None


def test_task_oriented_home_consumes_the_composed_snapshot(wizard):
    """The default Home page reads the same composed service as CLI/support."""
    from bc250_llm_mode.gui.home_page import HomePage

    assert isinstance(wizard._page, HomePage)
    assert wizard._page._view is not None
    assert len(wizard._page._view.cards) == 5
    wizard._page.refresh()
    assert wizard._page._view.primary.code
