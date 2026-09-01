from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.app import Application  # noqa: E402
from bc250_llm_mode.gui.connections_page import (  # noqa: E402
    MAX_VISIBLE_CLIENTS,
    ConnectionsPage,
    build_connections_view,
)
from bc250_llm_mode.gui.routes import PRIMARY_ROUTES, Route  # noqa: E402
from bc250_llm_mode.gui.shell import ApplicationWindow  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402


def _snapshot():
    return {
        "ready": True,
        "next_action": None,
        "model": {"public_alias": "LiquidAI/LFM2.5-2.6B"},
        "urls": {
            "webui_url": "https://bazzite.tail2168f.ts.net:8443/",
            "base_url": "https://bazzite.tail2168f.ts.net:10000/v1",
            "models_url": "https://bazzite.tail2168f.ts.net:10000/v1/models",
            "chat_completions_url": "https://bazzite.tail2168f.ts.net:10000/v1/chat/completions",
        },
        "checks": [{"id": "model", "passed": True}],
    }


def _application(tmp_path):
    application = Application.compose(AppPaths.temporary(tmp_path))
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "setup_complete": True, "disclaimer_ack": True})
    return application


def test_connections_view_has_exact_values_and_bounded_redacted_clients():
    clients = [{
        "client_id": f"{index:032x}", "label": f"Phone {index}",
        "client_kind": "pocketpal", "revision": 1,
        "fingerprint_prefix": "deadbeef",
    } for index in range(MAX_VISIBLE_CLIENTS + 10)]
    view = build_connections_view(_snapshot(), clients)
    assert view.ready is True
    assert view.model_alias == "LiquidAI/LFM2.5-2.6B"
    assert len(view.clients) == MAX_VISIBLE_CLIENTS == 32
    blob = json.dumps(view, default=lambda value: value.__dict__)
    assert ":10000/v1" in blob
    assert '"/api"' not in blob
    assert "fingerprint" not in blob
    assert "/root/models" not in blob


def test_connections_never_uses_legacy_green_when_journey_is_not_verified():
    snapshot = _snapshot()
    snapshot["readiness"] = {
        "remote_client_ready": False,
        "primary_problem_code": "CLIENT_VERIFICATION_STALE",
    }

    view = build_connections_view(snapshot, [])

    assert view.ready is False
    assert view.headline == "Finish connection checks"
    assert view.detail == "Run the guided connection check again."


def test_connections_is_a_primary_one_window_route(tmp_path):
    application = _application(tmp_path)
    window = ApplicationWindow(application, management=True)
    try:
        assert Route.CONNECTIONS in PRIMARY_ROUTES
        window.navigate(Route.CONNECTIONS)
        assert window._route is Route.CONNECTIONS
        assert isinstance(window._page, ConnectionsPage)
        assert len(window._nav_buttons) == 10
    finally:
        window.destroy()


def test_one_time_secret_is_cleared_on_page_leave(tmp_path):
    window = ApplicationWindow(_application(tmp_path), management=True)
    try:
        window.navigate(Route.CONNECTIONS)
        page = window._page
        page._show_secret("secret-canary-123456789")
        assert page._secret.get() == "secret-canary-123456789"
        page.leave()
        assert "secret-canary" not in page._secret.get()
        assert page._secret_deadline is None
    finally:
        window.destroy()


def test_connections_page_uses_shared_lanes_and_no_timer_or_second_window():
    source = Path(
        "bc250_llm_mode/gui/connections_page.py").read_text(encoding="utf-8")
    assert "request_observation(" in source
    assert "self.shell._work(" in source
    assert ".after(" not in source
    assert "Toplevel" not in source
    assert "messagebox" not in source
