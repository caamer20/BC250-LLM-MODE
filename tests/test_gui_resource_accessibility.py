"""GUI-7 deterministic resource, lifecycle, and accessibility gates."""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.app import Application  # noqa: E402
from bc250_llm_mode.gui.app import GuiBase, MAX_GUI_EVENTS  # noqa: E402
from bc250_llm_mode.gui.models_page import (  # noqa: E402
    MAX_MODEL_ROWS,
    filter_model_items,
)
from bc250_llm_mode.gui.routes import Route  # noqa: E402
from bc250_llm_mode.gui.shell import ApplicationWindow  # noqa: E402
from bc250_llm_mode.gui.tasks import TaskResult  # noqa: E402
from bc250_llm_mode.gui.refresh import RefreshCoordinator  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402


ROOT = Path(__file__).parent.parent / "bc250_llm_mode"


def _application(tmp_path):
    application = Application.compose(AppPaths.temporary(tmp_path))
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "setup_complete": True, "disclaimer_ack": True}
    )
    return application


def test_gui_event_bridge_is_bounded_and_log_bursts_never_block(tmp_path):
    window = ApplicationWindow(_application(tmp_path), management=True)
    try:
        assert window.events.maxsize == MAX_GUI_EVENTS == 512
        for index in range(MAX_GUI_EVENTS * 2):
            window.emit(f"line {index}")
        assert window.events.qsize() == MAX_GUI_EVENTS
    finally:
        window.destroy()


def test_stale_action_result_is_fenced_but_busy_state_is_released():
    called = []
    results = queue.Queue()
    results.put(TaskResult("action", generation=1, value=lambda: called.append(True)))

    class Progress:
        def stop(self):
            called.append("stopped")

    host = SimpleNamespace(
        _task_lanes=SimpleNamespace(results=results),
        _route_generation=2,
        busy=True,
        progress=Progress(),
        _refresh_coordinator=SimpleNamespace(active=True),
        _drain_events=lambda **_kwargs: None,
    )
    GuiBase._refresh_cycle(host)
    assert host.busy is False
    assert called == ["stopped"]


def test_only_refresh_coordinator_owns_tk_after_calls():
    sites = []
    for path in sorted((ROOT / "gui").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if ".after(" in text:
            sites.append(path.name)
    assert sites == ["refresh.py"]


def test_refresh_callback_cannot_collapse_idle_interval_into_busy_loop():
    class Root:
        def __init__(self):
            self.pending = {}
            self.delays = []
            self.next_id = 0

        def after(self, delay, callback):
            self.next_id += 1
            self.delays.append(delay)
            self.pending[self.next_id] = callback
            return self.next_id

        def after_cancel(self, token):
            self.pending.pop(token, None)

    root = Root()
    observed = []
    coordinator = None

    def callback():
        observed.append(coordinator.in_callback)

    coordinator = RefreshCoordinator(root, callback)
    coordinator.start()
    first = coordinator.token
    root.pending.pop(first)()
    assert observed == [True]
    assert coordinator.in_callback is False
    assert len(root.pending) == 1
    assert root.delays[-1] == 5000


def test_exactly_three_bounded_gui_worker_lanes(tmp_path):
    before = {thread.name for thread in threading.enumerate()}
    window = ApplicationWindow(_application(tmp_path), management=True)
    try:
        # Home's first observation creates the fixed action/query/chat lanes.
        names = {
            thread.name for thread in threading.enumerate()
            if thread.name.startswith("bc250-gui-")
        }
        assert names <= {
            "bc250-gui-action", "bc250-gui-observation", "bc250-gui-chat"
        }
        assert len(names) == 3
    finally:
        window.destroy()
    after = {thread.name for thread in threading.enumerate()}
    assert not ({"bc250-gui-action", "bc250-gui-observation"} & (after - before))


def test_model_filter_has_a_hard_visible_row_cap():
    from bc250_llm_mode.gui.models_page import ModelItemView

    items = tuple(
        ModelItemView(
            key=f"model-{index}", display_name=f"Model {index}", family="test",
            size_gib=1.0, state="INSTALLED", fit_verdict="FITS",
            fit_detail="fits", support_tier="supported", description="test",
            source_repo=None, catalog_id=None, alias=f"m{index}", quant="Q4",
            available_quants=("Q4",), tags=(),
        )
        for index in range(MAX_MODEL_ROWS + 40)
    )
    assert len(filter_model_items(items, category="All")) == MAX_MODEL_ROWS


def test_pages_route_in_one_window_and_dispose_inactive_widgets(tmp_path):
    application = _application(tmp_path)
    # Keep System's read-only observation deterministic and host-free.
    application.model_server = SimpleNamespace(status=lambda *_a: {"active": False})
    application.openwebui = SimpleNamespace(status=lambda *_a: {"running": False})
    application.tailscale = SimpleNamespace(status=lambda *_a: {"daemon_active": False})
    application.sharing = SimpleNamespace(status=lambda *_a: {"status": "disabled"})
    application.runtime_lifecycle = SimpleNamespace(status=lambda: {})
    application.backup = SimpleNamespace(list_backups=lambda: ())
    window = ApplicationWindow(application, management=True)
    try:
        for route in (
            Route.MODELS, Route.CHAT, Route.CONNECTIONS, Route.ACTIVITY,
            Route.MAINTENANCE, Route.SYSTEM, Route.SETTINGS, Route.HELP, Route.HOME,
        ):
            prior = window._page
            window.navigate(route)
            assert window._route is route
            assert window._page is not prior
            if "_disposed" in vars(prior):
                assert prior._disposed is True
    finally:
        window.destroy()


def test_accessibility_shortcuts_focus_and_narrow_layout_are_structural():
    shell = (ROOT / "gui" / "shell.py").read_text(encoding="utf-8")
    app = (ROOT / "gui" / "app.py").read_text(encoding="utf-8")
    for binding in (
        "<Control-Key-", "<Control-l>", "<Control-k>", "<Escape>",
    ):
        assert binding in shell
    assert "focus_set()" in shell
    assert 'self.minsize(760, 560)' in app
    assert "Toplevel" not in shell and "messagebox" not in shell


def test_preferences_are_typed_persistent_and_reduce_animation(tmp_path):
    application = _application(tmp_path)
    assert application.preferences.current() == {
        "appearance": "system",
        "reduced_motion": False,
        "notifications_enabled": False,
    }
    applied = application.preferences.apply({
        "appearance": "dark",
        "reduced_motion": True,
        "notifications_enabled": False,
    })
    assert application.preferences.current() == applied
    source = (ROOT / "gui" / "app.py").read_text(encoding="utf-8")
    assert "if not self.reduced_motion" in source


def test_page_probes_use_the_coalescing_observation_lane():
    for name in (
        "home_page.py", "models_page.py", "activity.py", "system_page.py",
        "chat_page.py", "connections_page.py", "maintenance_page.py",
    ):
        source = (ROOT / "gui" / name).read_text(encoding="utf-8")
        assert "request_observation(" in source, name
    tasks = (ROOT / "gui" / "tasks.py").read_text(encoding="utf-8")
    assert 'BoundedTaskLane("observation", self.results, coalesce=True)' in tasks
