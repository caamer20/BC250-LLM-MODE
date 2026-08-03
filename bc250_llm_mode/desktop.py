"""Non-destructive switch from dedicated LLM Mode to normal Bazzite desktop mode."""

from __future__ import annotations

import shutil
from typing import Any

from .llmmode import revert_llm_mode
from .logging_utils import CommandRunner
from .optimize import revert_optimizations
from .privilege import elevated


def switch_to_desktop_mode(
    state: dict[str, Any], runner: CommandRunner, *, activate_now: bool = False
) -> dict[str, Any]:
    """Restore desktop defaults while preserving downloaded models and app data."""
    runner.emit("Switching BC250 LLM MODE back to normal Bazzite desktop mode")

    # A regular desktop boot must not immediately reclaim the GPU for inference.
    service = str(state.get("service_name", "bc250-llm.service"))
    runner.run(elevated(["systemctl", "disable", "--now", service]), check=False)

    # Restore any opt-in host tuning before restoring the normal boot/power mode.
    revert_optimizations(state, runner)
    revert_llm_mode(state, runner)

    # Containers and models are retained, but idle application containers are
    # stopped so the desktop gets the host RAM back. Missing containers are fine.
    if shutil.which("podman"):
        webui = state.get("openwebui_container")
        if webui:
            runner.run(["podman", "stop", "--ignore", "--time", "10", str(webui)], check=False)
        container = state.get("container_name")
        if container:
            runner.run(["podman", "stop", "--ignore", "--time", "10", str(container)], check=False)

    if activate_now:
        runner.emit("Starting graphical.target for this boot")
        runner.run(elevated(["systemctl", "isolate", "graphical.target"]))

    # Keep setup/model records so repair can return to LLM Mode without downloads.
    state["system_mode"] = "desktop"
    runner.emit("Desktop mode is configured. Models, containers, and application state were preserved.")
    if state.get("reboot_required"):
        runner.emit("Reboot when convenient to activate the restored kernel power-management default.")
    elif not activate_now:
        runner.emit("Reboot, or run desktop-mode --now, to start the graphical desktop.")
    return state
