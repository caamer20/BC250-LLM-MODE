from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .llmmode import revert_llm_mode
from .logging_utils import CommandRunner
from .optimize import revert_optimizations
from .privilege import elevated


def uninstall(
    state: dict[str, Any],
    runner: CommandRunner,
    *,
    remove_container: bool = False,
    remove_models: bool = False,
) -> dict[str, Any]:
    service = str(state.get("service_name", "bc250-llm.service"))
    runner.run(elevated(["systemctl", "disable", "--now", service]), check=False)
    service_path = Path("/etc/systemd/system") / service
    if service_path.exists():
        runner.run(elevated(["rm", "-f", str(service_path)]))
        runner.run(elevated(["systemctl", "daemon-reload"]), check=False)
    if state.get("llm_mode_done"):
        revert_llm_mode(state, runner)
    if any(
        (
            state.get("optimizations_applied"),
            state.get("gpu_optimizer_applied"),
            state.get("optimization_service_previous"),
            "optimization_original_swappiness" in state,
            state.get("optimization_logrotate_applied"),
        )
    ):
        revert_optimizations(state, runner)
    if remove_container:
        runner.run(["podman", "rm", "--force", str(state.get("container_name", "llm"))], check=False)
        webui = state.get("openwebui_container")
        if webui:
            runner.run(["podman", "rm", "--force", str(webui)], check=False)
    if remove_models:
        models = Path(str(state["models_dir"])).expanduser().resolve()
        app = Path(str(state["app_dir"])).expanduser().resolve()
        if models != app / "models" or app not in models.parents:
            raise RuntimeError(f"Refusing to remove unexpected models path: {models}")
        if models.exists():
            shutil.rmtree(models)
            runner.emit(f"Removed model files at {models}; this cannot be recovered from the app.")
    state.update(setup_complete=False, env_ready=False if remove_container else state.get("env_ready", False))
    return state
