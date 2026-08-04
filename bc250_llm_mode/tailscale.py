"""Optional Tailscale lifecycle management for a systemd-based Bazzite host."""

from __future__ import annotations

import json
import shutil
from typing import Any

from .logging_utils import CommandRunner
from .privilege import elevated

SERVICE = "tailscaled.service"


def _service_value(runner: CommandRunner, prop: str) -> str:
    result = runner.run(
        ["systemctl", "show", SERVICE, f"--property={prop}", "--value"], check=False
    )
    return result.stdout.strip()


def tailscale_status(runner: CommandRunner) -> dict[str, Any]:
    cli = shutil.which("tailscale")
    daemon = shutil.which("tailscaled")
    if not cli and not daemon:
        return {
            "installed": False,
            "daemon_active": False,
            "connected": False,
            "backend_state": "not installed",
        }
    load = _service_value(runner, "LoadState")
    active_state = _service_value(runner, "ActiveState") if load != "not-found" else "inactive"
    result: dict[str, Any] = {
        "installed": bool(cli or daemon),
        "service_installed": bool(load and load != "not-found"),
        "daemon_active": active_state == "active",
        "active_state": active_state or "unknown",
        "connected": False,
        "backend_state": "daemon stopped" if active_state != "active" else "unknown",
    }
    if cli and result["daemon_active"]:
        # The raw JSON contains the entire peer/user map. Capture it for
        # parsing but never stream that unrelated tailnet data into app logs.
        status = runner.run([cli, "status", "--json"], check=False, emit_output=False)
        if status.returncode == 0:
            try:
                payload = json.loads(status.stdout)
                backend = str(payload.get("BackendState", "unknown"))
                own = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
                result.update(
                    backend_state=backend,
                    connected=backend == "Running",
                    online=bool(own.get("Online")),
                    dns_name=own.get("DNSName"),
                    addresses=own.get("TailscaleIPs", []),
                )
            except json.JSONDecodeError:
                result["error"] = "tailscale status returned invalid JSON"
        elif status.stdout.strip():
            result["error"] = status.stdout.strip()
    return result


def _require_tailscale() -> None:
    if not shutil.which("tailscale") and not shutil.which("tailscaled"):
        raise RuntimeError("Tailscale is not installed. Install it on Bazzite, then retry.")


def start_tailscale(runner: CommandRunner) -> dict[str, Any]:
    _require_tailscale()
    runner.run(elevated(["systemctl", "start", SERVICE]))
    return tailscale_status(runner)


def stop_tailscale(runner: CommandRunner) -> dict[str, Any]:
    _require_tailscale()
    runner.run(elevated(["systemctl", "stop", SERVICE]), check=False)
    return tailscale_status(runner)


def restart_tailscale(runner: CommandRunner) -> dict[str, Any]:
    _require_tailscale()
    runner.run(elevated(["systemctl", "restart", SERVICE]))
    return tailscale_status(runner)


def connect_tailscale(runner: CommandRunner) -> dict[str, Any]:
    _require_tailscale()
    runner.run(elevated(["systemctl", "start", SERVICE]))
    runner.run(elevated(["tailscale", "up"]))
    return tailscale_status(runner)


def disconnect_tailscale(runner: CommandRunner) -> dict[str, Any]:
    _require_tailscale()
    # `tailscale down` idles the daemon while preserving its system service.
    runner.run(elevated(["tailscale", "down"]), check=False)
    return tailscale_status(runner)
