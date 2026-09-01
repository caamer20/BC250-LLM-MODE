"""Tailnet-only HTTPS publishing for Open WebUI and the model API."""

from __future__ import annotations

import json
import shutil
from typing import Any, Callable

from .logging_utils import CommandRunner
from .openwebui import GATEWAY_PORT, start_open_webui
from .privilege import elevated
from .server import start_service
from .tailscale import start_tailscale, tailscale_status

WEBUI_HTTPS_PORT = 8443
API_HTTPS_PORT = 10000
WEBUI_TARGET = "http://127.0.0.1:3000"
# ADR 005 D2/D4: the model API is published ONLY through the authenticated
# gateway, never the raw backend (the loopback backend address is not a
# publishable/proxy target). Tailnet clients present the install-scoped
# credential at the gateway, which enforces scopes/rate/size and audits.
# This is the same loopback gateway address Open WebUI uses over the
# container bridge.
API_TARGET = f"http://127.0.0.1:{GATEWAY_PORT}"


def _gateway_provision_checks(state: dict[str, Any]) -> dict[str, Any]:
    """The §10.3 explicit status fields for the gateway + auth state.

    Refuses mutation if the gateway credential is not provisioned and
    verified (D4: 'share' says no before mutating tailscale).
    """
    provisioned = bool(state.get("gateway_provisioned"))
    verified = bool(state.get("gateway_verified"))
    return {
        "topology": "gateway-only" if provisioned else "none",
        "gateway_state": "verified" if (provisioned and verified) else "pending",
        "auth_state": "scoped-credential" if provisioned else "none",
        "backend_identity": (
            state.get("gateway_backend_identity") or (
                "verified" if verified else "unverified")
        ),
    }


def _tailscale_cli() -> str:
    cli = shutil.which("tailscale")
    if not cli:
        raise RuntimeError("Tailscale is not installed. Install and connect Tailscale first.")
    return cli


def _serve_config(runner: CommandRunner) -> dict[str, Any]:
    cli = _tailscale_cli()
    result = runner.run(
        [cli, "serve", "status", "--json"], check=False, emit_output=False
    )
    if result.returncode or not result.stdout.strip():
        return {}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _port_handler(config: dict[str, Any], port: int) -> dict[str, Any]:
    web = config.get("Web") if isinstance(config.get("Web"), dict) else {}
    funnel = (
        config.get("AllowFunnel") if isinstance(config.get("AllowFunnel"), dict) else {}
    )
    suffix = f":{port}"
    for host, value in web.items():
        if not str(host).endswith(suffix) or not isinstance(value, dict):
            continue
        handlers = value.get("Handlers") if isinstance(value.get("Handlers"), dict) else {}
        root = handlers.get("/") if isinstance(handlers.get("/"), dict) else {}
        return {
            "host": str(host),
            "proxy": root.get("Proxy"),
            "public_funnel": bool(funnel.get(host)),
        }
    return {"host": None, "proxy": None, "public_funnel": False}


def https_sharing_status(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    tail = tailscale_status(runner)
    if not tail.get("installed"):
        return {
            "available": False,
            "enabled": False,
            "webui_enabled": False,
            "api_enabled": False,
            "public_funnel": False,
            "status": "Tailscale is not installed",
        }
    config = _serve_config(runner)
    webui = _port_handler(config, int(state.get("https_webui_port", WEBUI_HTTPS_PORT)))
    api = _port_handler(config, int(state.get("https_api_port", API_HTTPS_PORT)))
    dns_name = str(tail.get("dns_name") or "").rstrip(".")
    webui_port = int(state.get("https_webui_port", WEBUI_HTTPS_PORT))
    api_port = int(state.get("https_api_port", API_HTTPS_PORT))
    webui_enabled = webui.get("proxy") == WEBUI_TARGET
    api_enabled = api.get("proxy") == API_TARGET
    public_funnel = bool(webui.get("public_funnel") or api.get("public_funnel"))
    gateway = _gateway_provision_checks(state)
    verified = bool(gateway["gateway_state"] == "verified")
    # §10.3 D4: API is only 'enabled'/usable when the gateway is verified.
    api_safe = api_enabled and verified
    return {
        **gateway,
        "available": True,
        "enabled": webui_enabled and api_safe and not public_funnel,
        "webui_enabled": webui_enabled,
        "api_enabled": api_safe,
        "public_funnel": public_funnel,
        "tailnet_connected": bool(tail.get("connected")),
        "verified": verified,
        "last_verified_at": state.get("gateway_last_verified_at"),
        "dns_name": dns_name or None,
        "webui_url": f"https://{dns_name}:{webui_port}/" if dns_name else None,
        "api_base_url": f"https://{dns_name}:{api_port}/v1" if dns_name else None,
        "health_url": f"https://{dns_name}:{api_port}/health" if dns_name else None,
        "webui_proxy": webui.get("proxy"),
        "api_proxy": api.get("proxy"),
        "status": (
            "tailnet HTTPS ready"
            if webui_enabled and api_safe and not public_funnel
            else "not fully configured"
        ),
    }


def start_https_sharing(
    state: dict[str, Any],
    runner: CommandRunner,
    *,
    ensure_gateway: Callable[[], Any] | None = None,
    start_webui: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    cli = _tailscale_cli()
    tail = tailscale_status(runner)
    if not tail.get("daemon_active"):
        tail = start_tailscale(runner)
    if not tail.get("connected"):
        raise RuntimeError("Tailscale is not connected. Run `bc250-llm-mode tailscale connect` first.")

    # ADR 005 D4: refuse before any mutation unless the gateway is provisioned
    # and verified. Publishing the raw backend (or the gateway before its
    # credential is set/validated) would leak a management-reachable surface.
    gateway = _gateway_provision_checks(state)
    if gateway["gateway_state"] != "verified":
        raise RuntimeError(
            "The model API gateway is not provisioned and verified. "
            "Publish the WebUI only, or provision+verify the gateway first. "
            "Refusing to publish an unauthenticated model API."
        )

    # A sharing start is an explicit request for usable endpoints, so ensure
    # both backends are live before publishing them.
    start_service(state, runner)
    if ensure_gateway is not None:
        ensure_gateway()
    if start_webui is not None:
        start_webui()
    else:
        start_open_webui(state, runner)

    webui_port = int(state.get("https_webui_port", WEBUI_HTTPS_PORT))
    api_port = int(state.get("https_api_port", API_HTTPS_PORT))
    for port, target in ((webui_port, WEBUI_TARGET), (api_port, API_TARGET)):
        # Never inherit a public Funnel rule for an unauthenticated llama API.
        runner.run(elevated([cli, "funnel", f"--https={port}", "off"]), check=False)
        runner.run(elevated([cli, "serve", "--bg", f"--https={port}", target]))

    status = https_sharing_status(state, runner)
    if not status["enabled"]:
        raise RuntimeError("Tailscale HTTPS publishing did not reach the expected safe configuration")
    state.update(
        https_sharing_enabled=True,
        https_webui_port=webui_port,
        https_api_port=api_port,
        https_webui_url=status.get("webui_url"),
        https_api_base_url=status.get("api_base_url"),
    )
    runner.emit(
        f"Tailnet HTTPS ready: Open WebUI {status['webui_url']} · model API {status['api_base_url']}"
    )
    return status


def stop_https_sharing(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    cli = _tailscale_cli()
    for port in (
        int(state.get("https_webui_port", WEBUI_HTTPS_PORT)),
        int(state.get("https_api_port", API_HTTPS_PORT)),
    ):
        runner.run(elevated([cli, "serve", f"--https={port}", "off"]), check=False)
        runner.run(elevated([cli, "funnel", f"--https={port}", "off"]), check=False)
    state["https_sharing_enabled"] = False
    return https_sharing_status(state, runner)
