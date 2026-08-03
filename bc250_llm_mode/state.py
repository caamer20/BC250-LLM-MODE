"""Atomic, schema-tolerant application state."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_APP_DIR,
    DEFAULT_CONTAINER,
    DEFAULT_CTX,
    DEFAULT_LOGS_DIR,
    DEFAULT_MODELS_DIR,
    DEFAULT_PORT,
    DEFAULT_SERVICE,
    DEFAULT_STATE_PATH,
)
from .optimize import DEFAULT_OPTIMIZATIONS

DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 2,
    "disclaimer_ack": False,
    "ack_timestamp": None,
    "setup_phase": 0,
    "setup_complete": False,
    "llm_mode_done": False,
    "system_mode": "unconfigured",
    "reboot_required": False,
    "pending_karg_mode": None,
    "env_ready": False,
    "installed_models": [],
    "current_model": None,
    "selected_source": "catalog",
    "selected_local_model": None,
    "current_ctx": DEFAULT_CTX,
    "server_port": DEFAULT_PORT,
    "container_name": DEFAULT_CONTAINER,
    "service_name": DEFAULT_SERVICE,
    "app_dir": str(DEFAULT_APP_DIR),
    "models_dir": str(DEFAULT_MODELS_DIR),
    "model_search_paths": [],
    "logs_dir": str(DEFAULT_LOGS_DIR),
    "llama_cpp_path": "/root/llama.cpp",
    "venv_path": "/root/.venvs/hf",
    "optimizations": deepcopy(DEFAULT_OPTIMIZATIONS),
}


class StateStore:
    def __init__(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> dict[str, Any]:
        state = deepcopy(DEFAULT_STATE)
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Cannot read state file {self.path}: {exc}") from exc
            if not isinstance(raw, dict):
                raise RuntimeError(f"State file {self.path} must contain a JSON object")
            state.update(raw)
            if int(raw.get("schema_version", 1)) < 2:
                old_phase = int(state.get("setup_phase", 0))
                if old_phase >= 6:
                    state["setup_phase"] = old_phase + 1
                state["schema_version"] = 2
        state["state_path"] = str(self.path)
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = deepcopy(DEFAULT_STATE)
        data.update(state)
        data["state_path"] = str(self.path)
        fd, temporary = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def update(self, **changes: Any) -> dict[str, Any]:
        state = self.load()
        state.update(changes)
        self.save(state)
        return state
