from __future__ import annotations

import shutil
from typing import Any

from .logging_utils import CommandRunner

CONTAINER = "bc250-open-webui"
LEGACY_CONTAINER = "open-webui"


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
    if exists:
        result = runner.run(
            ["podman", "inspect", "--format", "{{.State.Status}}", container], check=False
        )
        status = result.stdout.strip() or "unknown"
        state["openwebui_container"] = container
        state["openwebui_installed"] = True
    return {
        "available": True,
        "installed": exists,
        "running": status == "running",
        "status": status,
        "container": container,
        "url": "http://127.0.0.1:3000",
    }


def install_open_webui(state: dict[str, Any], runner: CommandRunner) -> None:
    if not shutil.which("podman"):
        raise RuntimeError("Podman is required for Open WebUI; re-run environment setup.")
    container, exists = _container_name(state, runner)
    if exists and container == LEGACY_CONTAINER:
        runner.emit(f"Reusing existing Open WebUI container {container}")
    if not exists:
        runner.run([
            "podman", "create", "--name", container, "--network", "host",
            "-e", "PORT=3000",
            "-e", "OPENAI_API_BASE_URL=http://127.0.0.1:8080/v1",
            "-e", "OPENAI_API_BASE_URLS=http://127.0.0.1:8080/v1",
            "-e", "OPENAI_API_KEY=sk-no-key-needed",
            "-v", "bc250-open-webui:/app/backend/data",
            "ghcr.io/open-webui/open-webui:main",
        ])
    runner.run(["podman", "start", container], check=False)
    state["openwebui_installed"] = True
    state["openwebui_container"] = container
    runner.emit(
        "Open WebUI is on http://127.0.0.1:3000. If the model is absent, log in as admin and create a Workspace model pinned to the base model id."
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
