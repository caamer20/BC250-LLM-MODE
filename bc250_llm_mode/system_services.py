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

    def prepare_gui_close(self, state_supplier, host_mode, runner) -> dict[str, Any]:
        from .profile_access import profile_access
        from .operations.repositories import OperationRepository

        # Drain any already-running activation before the final observation.
        # Queued/recovering work must be resolved before ending Desktop chat.
        with profile_access(self._units.database_path.parent, exclusive=True):
            state = state_supplier()
            host = host_mode.status(state, runner)
            if not isinstance(host, dict) or "desktop_active" not in host:
                raise RuntimeError("desktop status unavailable")
            if not host["desktop_active"] and state.get("system_mode") != "desktop":
                return {"safe_to_close": True}
            with self._units.read() as conn:
                if OperationRepository(conn).list_active():
                    return {"safe_to_close": False, "reason": "Finish or repair active operations before closing Desktop chat."}
            status = self.status(state, runner)
            if status.get("active"):
                status = self.stop(state, runner)
            if status.get("active") is not False:
                raise RuntimeError("model service inactivity unverified")
            return {"safe_to_close": True}


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
