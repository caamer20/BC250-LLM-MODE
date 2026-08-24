from __future__ import annotations

import json
import shutil
from typing import Any

from .logging_utils import CommandRunner

CONTAINER = "bc250-open-webui"
LEGACY_CONTAINER = "open-webui"
# Pinned to a version tag; release engineering replaces this with an
# @digest reference (and records it in the runtime manifest) at release
# time. Tracking a mutable tag like :main is not acceptable for production.
IMAGE_REF = "ghcr.io/open-webui/open-webui:v0.6.14"
DATA_VOLUME = "bc250-open-webui"
# U0.6 containment: the UI lives on a dedicated private Podman network and
# is published STRICTLY on host loopback. Host networking is never used;
# a legacy host-network container is migrated (named volume preserved)
# before it may start.
NETWORK = "bc250-openwebui"
UI_PUBLISH = "127.0.0.1:3000:8080"
# Interim backend route (U3.3 replaces this with the authenticated
# gateway): the isolated container resolves the HOST-side bridge gateway,
# which is where the U3 gateway will terminate. Until that gateway exists
# the raw model API is NOT advertised as production-safe remote sharing.
BACKEND_HOST = "host.containers.internal"
BACKEND_URL = f"http://{BACKEND_HOST}:8080/v1"


def _ensure_network(state: dict[str, Any], runner: CommandRunner) -> None:
    result = runner.run(
        ["podman", "network", "exists", NETWORK], check=False
    )
    if result.returncode != 0:
        runner.run([
            "podman", "network", "create", NETWORK,
        ])


def _container_topology(
    state: dict[str, Any], runner: CommandRunner, container: str
) -> str:
    """Classify the container network topology without mutating anything."""
    result = runner.run(
        [
            "podman", "inspect", "--format",
            "{{json .NetworkSettings.Networks}}", container,
        ],
        check=False,
    )
    try:
        networks = json.loads(result.stdout.strip() or "{}")
    except ValueError:
        return "unknown"
    if not isinstance(networks, dict):
        return "unknown"
    if "host" in networks:
        return "legacy-host"
    if NETWORK in networks:
        return "contained"
    return "unknown"


def _migrate_legacy_container(
    state: dict[str, Any], runner: CommandRunner, container: str
) -> None:
    """Stop+remove a legacy/uncontained container, KEEPING its volume."""
    runner.emit(
        f"Migrating Open WebUI container {container} to the contained "
        "private network (user data volume is preserved)."
    )
    runner.run(["podman", "stop", "--time", "10", container], check=False)
    runner.run(["podman", "rm", container], check=False)


def _create_command(container: str) -> list[str]:
    # Security posture: dedicated private network, UI published strictly on
    # host loopback, no-new-privileges, dropped capabilities, bounded
    # memory/PIDs. Host networking is forbidden (U0.6).
    return [
        "podman", "create", "--name", container,
        "--network", NETWORK,
        "-p", UI_PUBLISH,
        "--add-host", f"{BACKEND_HOST}:host-gateway",
        "-e", "PORT=8080",
        "-e", f"OPENAI_API_BASE_URL={BACKEND_URL}",
        "-e", f"OPENAI_API_BASE_URLS={BACKEND_URL}",
        "-e", "OPENAI_API_KEY=sk-no-key-needed",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "all",
        "--memory", "2g",
        "--pids-limit", "256",
        "-v", f"{DATA_VOLUME}:/app/backend/data",
        IMAGE_REF,
    ]



def _container_name(state: dict[str, Any], runner: CommandRunner) -> tuple[str, bool]:
    preferred = str(state.get("openwebui_container") or CONTAINER)
    for name in dict.fromkeys((preferred, CONTAINER, LEGACY_CONTAINER)):
        exists = runner.run(["podman", "container", "exists", name], check=False).returncode == 0
        if exists:
            return name, True
    return preferred, False


def open_webui_status(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    if not shutil.which("podman"):
        return {"available": False, "installed": False, "running": False, "status": "podman missing"}
    container, exists = _container_name(state, runner)
    status = "not installed"
    topology = "absent"
    if exists:
        result = runner.run(
            ["podman", "inspect", "--format", "{{.State.Status}}", container], check=False
        )
        status = result.stdout.strip() or "unknown"
        topology = _container_topology(state, runner, container)
        state["openwebui_container"] = container
        state["openwebui_installed"] = True
    return {
        "available": True,
        "installed": exists,
        "running": status == "running",
        "status": status,
        "container": container,
        "topology": topology,
        # U3.3 lands the authenticated gateway; until then the raw model
        # API is never advertised as production-safe remote sharing.
        "backend_route": (
            "pending-authenticated-gateway" if exists else "none"
        ),
        "url": "http://127.0.0.1:3000",
    }


def install_open_webui(state: dict[str, Any], runner: CommandRunner) -> None:
    if not shutil.which("podman"):
        raise RuntimeError("Podman is required for Open WebUI; re-run environment setup.")
    container, exists = _container_name(state, runner)
    _ensure_network(state, runner)
    if exists:
        topology = _container_topology(state, runner, container)
        if container == LEGACY_CONTAINER or topology != "contained":
            # Legacy/uncontained containers are migrated (volume
            # preserved) and recreated contained — never reused or
            # started while unsafe (U0.6).
            _migrate_legacy_container(state, runner, container)
            container, exists = CONTAINER, False
    if not exists:
        runner.run(_create_command(container))
    runner.run(["podman", "start", container], check=False)
    state["openwebui_installed"] = True
    state["openwebui_container"] = container
    runner.emit(
        "Open WebUI is on http://127.0.0.1:3000 (loopback only). If the "
        "model is absent, log in as admin and create a Workspace model "
        "pinned to the base model id."
    )


def start_open_webui(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    status = open_webui_status(state, runner)
    if not status["installed"]:
        install_open_webui(state, runner)
    else:
        runner.run(["podman", "start", str(status["container"])], check=False)
    return open_webui_status(state, runner)


def stop_open_webui(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    status = open_webui_status(state, runner)
    if status["installed"]:
        runner.run(["podman", "stop", "--time", "10", str(status["container"])], check=False)
    return open_webui_status(state, runner)


def restart_open_webui(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    status = open_webui_status(state, runner)
    if not status["installed"]:
        install_open_webui(state, runner)
    else:
        runner.run(["podman", "restart", "--time", "10", str(status["container"])])
    return open_webui_status(state, runner)
