from __future__ import annotations

from typing import Any

from .logging_utils import CommandRunner

CONTAINER = "bc250-open-webui"
LEGACY_CONTAINER = "open-webui"


def install_open_webui(state: dict[str, Any], runner: CommandRunner) -> None:
    container = CONTAINER
    exists = runner.run(["podman", "container", "exists", container], check=False).returncode == 0
    if not exists and runner.run(["podman", "container", "exists", LEGACY_CONTAINER], check=False).returncode == 0:
        container = LEGACY_CONTAINER
        exists = True
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
