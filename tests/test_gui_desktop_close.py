"""Desktop GUI close owns the interactive model-server lifetime."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.gui.shell import ApplicationWindow  # noqa: E402


class _ModelServer:
    def __init__(self, *, active: bool, remains_active: bool = False) -> None:
        self.active = active
        self.remains_active = remains_active
        self.calls: list[str] = []

    def status(self, _state, _runner):
        self.calls.append("status")
        return {"active": self.active}

    def stop(self, _state, _runner):
        self.calls.append("stop")
        return {"active": self.remains_active}


def _window(tmp_path, *, mode: str, server: _ModelServer):
    del tmp_path
    summary = SimpleNamespace(active_count=0)
    application = SimpleNamespace(
        read_model=lambda: {"system_mode": mode},
        model_server=server,
        operation_query=SimpleNamespace(active_summary=lambda: summary),
    )
    window = ApplicationWindow.__new__(ApplicationWindow)
    window.application = application
    window._page = None
    window.runner = lambda: object()
    window.emit = lambda _line: None
    window.notice_bar = SimpleNamespace(show_notice=lambda _notice: None)
    window.drawer = SimpleNamespace(show_confirmation=lambda *_args: None)
    window._refresh_coordinator = SimpleNamespace(
        mapped=True, request_now=lambda: None
    )
    closed: list[bool] = []
    window.destroy = lambda: closed.append(True)
    return window, closed


def test_desktop_close_stops_an_active_model_before_destroy(tmp_path):
    server = _ModelServer(active=True)
    window, closed = _window(tmp_path, mode="desktop", server=server)

    window.request_close()

    assert server.calls == ["status", "stop"]
    assert closed == [True]


def test_desktop_close_does_not_stop_an_already_inactive_model(tmp_path):
    server = _ModelServer(active=False)
    window, closed = _window(tmp_path, mode="desktop", server=server)

    window.request_close()

    assert server.calls == ["status"]
    assert closed == [True]


def test_llm_mode_close_leaves_explicit_serving_session_running(tmp_path):
    server = _ModelServer(active=True)
    window, closed = _window(tmp_path, mode="llm-session", server=server)

    window.request_close()

    assert server.calls == []
    assert closed == [True]


def test_desktop_close_stays_open_when_inactive_state_cannot_be_verified(tmp_path):
    server = _ModelServer(active=True, remains_active=True)
    window, closed = _window(tmp_path, mode="desktop", server=server)

    window.request_close()

    assert server.calls == ["status", "stop"]
    assert closed == []


def test_unmap_does_not_change_model_lifecycle(tmp_path):
    server = _ModelServer(active=True)
    window, closed = _window(tmp_path, mode="desktop", server=server)

    window._set_mapped(False)

    assert server.calls == []
    assert closed == []


def test_active_operation_confirmation_uses_the_same_stop_then_close_boundary(tmp_path):
    server = _ModelServer(active=True)
    window, closed = _window(tmp_path, mode="desktop", server=server)
    window.application.operation_query = SimpleNamespace(
        active_summary=lambda: SimpleNamespace(
            active_count=1,
            worker_lock_owner=None,
            worker_lock_expired=True,
        )
    )
    captured = {}
    window.drawer.show_confirmation = lambda confirmation, callback: captured.update(
        confirmation=confirmation, callback=callback
    )

    window.request_close()

    assert server.calls == []
    assert closed == []
    captured["callback"]()
    assert server.calls == ["status", "stop"]
    assert closed == [True]
    assert "running model will stop" in captured["confirmation"].consequence
