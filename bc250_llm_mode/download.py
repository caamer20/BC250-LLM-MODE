from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .catalog import ModelEntry, validate_artifact
from .logging_utils import CommandRunner


def download_model(
    state: dict[str, Any], model: ModelEntry, quant: str, runner: CommandRunner
) -> Path:
    if quant not in model.allow_globs:
        raise ValueError(f"Unsupported quant {quant} for {model.display_name}")
    pattern = model.allow_globs[quant]
    validate_artifact(model.repo, pattern)
    destination = Path(str(state["models_dir"])).expanduser() / model.id / "source"
    destination.mkdir(parents=True, exist_ok=True)
    venv = str(state.get("venv_path", "/root/.venvs/hf"))
    container = str(state.get("container_name", "llm"))
    command = [
        "podman", "exec", "--user", "root", container, f"{venv}/bin/hf", "download", model.repo,
        "--include", pattern, "--local-dir", str(destination),
    ]
    if os.environ.get("HF_TOKEN"):
        command.extend(["--token", os.environ["HF_TOKEN"]])
    else:
        runner.emit("HF_TOKEN is not set; continuing anonymously (Hugging Face may apply rate limits).")
    runner.run(command)
    candidates = [path for path in destination.rglob("*") if path.is_file() and path.stat().st_size > 1024 * 1024]
    candidates = [path for path in candidates if path.suffix == ".gguf" or model.conversion]
    if not candidates:
        raise RuntimeError(f"Download completed but no non-trivial artifact matched {pattern}")
    for candidate in candidates:
        validate_artifact(model.repo, candidate.name)
    state["selected_model"] = model.id
    state["selected_quant"] = quant
    state["download_dir"] = str(destination)
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 7)
    return max(candidates, key=lambda item: item.stat().st_size)
