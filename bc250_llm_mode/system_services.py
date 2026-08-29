"""Composed service boundaries for model-server and Tailscale controls."""

from __future__ import annotations

from typing import Any

from .services import persist_state_diff


class ModelServerService:
    def __init__(self, units) -> None:
        self._units = units

    def status(self, view, runner) -> dict[str, Any]:
        from .server import service_status

        return service_status(view, runner)

    def start(self, view, runner) -> dict[str, Any]:
        from .server import start_service

        before = dict(view)
        result = start_service(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def stop(self, view, runner) -> dict[str, Any]:
        from .server import stop_service

        before = dict(view)
        result = stop_service(view, runner)
        persist_state_diff(self._units, before, view)
        return result

    def restart(self, view, runner) -> dict[str, Any]:
        from .server import restart_and_wait

        before = dict(view)
        result = restart_and_wait(view, runner)
        persist_state_diff(self._units, before, view)
        return result


class TailscaleService:
    def __init__(self, units) -> None:
        self._units = units

    def status(self, runner) -> dict[str, Any]:
        from .tailscale import tailscale_status

        return tailscale_status(runner)

    def start(self, view, runner) -> dict[str, Any]:
        from .tailscale import start_tailscale

        before = dict(view)
        result = start_tailscale(runner)
        persist_state_diff(self._units, before, view)
        return result

    def stop(self, view, runner) -> dict[str, Any]:
        from .tailscale import stop_tailscale

        before = dict(view)
        result = stop_tailscale(runner)
        persist_state_diff(self._units, before, view)
        return result

    def connect(self, view, runner) -> dict[str, Any]:
        from .tailscale import connect_tailscale

        before = dict(view)
        result = connect_tailscale(runner)
        persist_state_diff(self._units, before, view)
        return result

    def disconnect(self, view, runner) -> dict[str, Any]:
        from .tailscale import disconnect_tailscale

        before = dict(view)
        result = disconnect_tailscale(runner)
        persist_state_diff(self._units, before, view)
        return result
