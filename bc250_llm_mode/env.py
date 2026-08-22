"""Containerized llama.cpp/Vulkan environment setup."""

from __future__ import annotations

import os
import re
import shutil
import time
from typing import Any

from .constants import KNOWN_GOOD_LLAMACPP, TAG_PATTERN
from .disclaimer import require_acknowledgment
from .logging_utils import CommandError, CommandRunner

FEDORA_IMAGE = "registry.fedoraproject.org/fedora:latest"
BUILD_PACKAGES = "git cmake ninja-build gcc-c++ vulkan-loader-devel vulkan-tools python3 python3-pip python3-devel"
PYTHON_PACKAGES = "huggingface_hub[cli] gguf safetensors numpy sentencepiece protobuf torch --extra-index-url https://download.pytorch.org/whl/cpu"


def _container_exists(runner: CommandRunner, name: str) -> bool:
    result = runner.run(["podman", "container", "exists", name], check=False)
    return result.returncode == 0


def _exec(runner: CommandRunner, name: str, *args: str, check: bool = True):
    return runner.run(["podman", "exec", "--user", "root", name, *args], check=check)


def setup_environment(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    require_acknowledgment(state)
    missing = [command for command in ("podman", "distrobox") if not shutil.which(command)]
    if missing:
        raise RuntimeError(
            f"Missing host command(s): {', '.join(missing)}. Install them through the Bazzite-supported layering path."
        )
    name = str(state.get("container_name", "llm"))
    if not _container_exists(runner, name):
        root_flag = ["--root"] if os.geteuid() == 0 else []
        runner.run(["distrobox", "create", *root_flag, "--name", name, "--image", FEDORA_IMAGE, "--yes"])
    runner.run(["podman", "start", name], check=False)

    llama_root = str(state.get("llama_cpp_path", "/root/llama.cpp"))
    server_binary = f"{llama_root}/build/bin/llama-server"
    quantize_binary = f"{llama_root}/build/bin/llama-quantize"
    existing_build = _exec(
        runner, name, "bash", "-lc", f"test -x {server_binary} && test -x {quantize_binary}", check=False
    ).returncode == 0
    if existing_build:
        runner.emit(f"Reusing existing llama.cpp Vulkan build at {llama_root}")
    else:
        _exec(runner, name, "bash", "-lc", f"dnf install -y {BUILD_PACKAGES}")
        clone_script = (
            f"test -d {llama_root}/.git || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git {llama_root}; "
            f"cmake -S {llama_root} -B {llama_root}/build -G Ninja -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release; "
            f"cmake --build {llama_root}/build --target llama-server llama-cli llama-quantize -j2"
        )
        _exec(runner, name, "bash", "-lc", clone_script)

    venv = str(state.get("venv_path", "/root/.venvs/hf"))
    venv_ready = _exec(
        runner, name, "bash", "-lc",
        f"test -x {venv}/bin/hf && {venv}/bin/python -c 'import gguf, safetensors, numpy, sentencepiece, google.protobuf, torch'",
        check=False,
    ).returncode == 0
    if venv_ready:
        runner.emit(f"Reusing existing inference Python environment at {venv}")
    else:
        _exec(runner, name, "bash", "-lc", "dnf install -y python3 python3-pip python3-devel")
        venv_script = (
            f"python3 -m venv {venv}; {venv}/bin/pip install --upgrade pip; "
            f"{venv}/bin/pip install {PYTHON_PACKAGES}"
        )
        _exec(runner, name, "bash", "-lc", venv_script)
    _exec(runner, name, server_binary, "--version")
    vulkan = _exec(runner, name, "vulkaninfo", "--summary", check=False)
    if vulkan.returncode == 127:
        _exec(runner, name, "bash", "-lc", "dnf install -y vulkan-tools")
        vulkan = _exec(runner, name, "vulkaninfo", "--summary", check=False)
    combined = vulkan.stdout.lower()
    if vulkan.returncode or not any(token in combined for token in ("bc-250", "gfx1013", "amd", "radeon")):
        raise RuntimeError("Vulkan smoke test failed: the container cannot see an AMD Vulkan device.")
    state.update(env_ready=True, llama_cpp_path=llama_root, venv_path=venv)
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 4)
    try:
        record_llamacpp_build(state, runner)
    except (RuntimeError, CommandError) as exc:
        runner.emit(f"Could not record llama.cpp build metadata: {exc}")
    return state


def _git_show(runner: CommandRunner, name: str, root: str, args: str) -> str:
    return _exec(
        runner, name, "bash", "-lc", f"git -C {root} {args}", check=False
    ).stdout.strip()


def record_llamacpp_build(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Record the currently built llama.cpp commit; keep the last 5 entries."""
    name = str(state.get("container_name", "llm"))
    root = str(state.get("llama_cpp_path", "/root/llama.cpp"))
    commit = _git_show(runner, name, root, "rev-parse HEAD")
    if not commit:
        raise RuntimeError("The llama.cpp checkout has no recorded commit")
    describe = _git_show(runner, name, root, "describe --tags --always") or commit[:12]
    info = {"commit": commit, "describe": describe, "recorded": time.strftime("%Y-%m-%d")}
    history = [item for item in (state.get("llamacpp_history") or []) if isinstance(item, dict)]
    previous = state.get("llamacpp_build")
    if isinstance(previous, dict) and previous.get("commit"):
        # Only one physical build-backup is retained on disk, so the recorded
        # rollback target list never claims more than what exists.
        history = [previous]
    state["llamacpp_history"] = history[-1:]
    state["llamacpp_build"] = info
    return info


def evaluate_pin(installed_describe: str | None, pin: str = KNOWN_GOOD_LLAMACPP) -> bool:
    """True when the installed build matches the shipped known-good tag."""
    return bool(installed_describe) and str(installed_describe).startswith(pin)


def llamacpp_status(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Report the installed build against the shipped known-good pin."""
    name = str(state.get("container_name", "llm"))
    root = str(state.get("llama_cpp_path", "/root/llama.cpp"))
    build = state.get("llamacpp_build")
    if not build and state.get("env_ready") and _container_exists(runner, name):
        describe = _git_show(runner, name, root, "describe --tags --always")
        if describe:
            build = {"commit": None, "describe": describe}
    installed_describe = build.get("describe") if isinstance(build, dict) else None
    return {
        "installed": build,
        "pin": KNOWN_GOOD_LLAMACPP,
        "on_pin": evaluate_pin(installed_describe),
        "history_count": len(state.get("llamacpp_history") or []),
    }


def update_llamacpp(
    state: dict[str, Any], runner: CommandRunner, *, tag: str | None = None
) -> dict[str, Any]:
    """Fetch and rebuild llama.cpp into a staging dir, then swap atomically.

    The working binaries are never touched until the new build passes its smoke
    checks; a failed health restart restores the previous build directory.
    """
    require_acknowledgment(state)
    if not state.get("env_ready"):
        raise RuntimeError("Inference environment is not ready; complete setup first")
    target = tag or KNOWN_GOOD_LLAMACPP
    if not TAG_PATTERN.fullmatch(target):
        raise ValueError(f"Invalid llama.cpp tag: {target!r}")
    from .server import restart_and_wait

    name = str(state.get("container_name", "llm"))
    root = str(state.get("llama_cpp_path", "/root/llama.cpp"))
    stage = f"{root}-staging"
    backup = f"{root}-backup"
    runner.emit(f"Updating llama.cpp to {target} (staged source clone, atomic switch)")
    # 1. Fetch the tag into the active repo (metadata only; HEAD is untouched).
    _exec(runner, name, "bash", "-lc", (
        f"cd {root} && git fetch origin tag {target} --depth 1 --no-tags --force"
    ))
    # 2. Build in a fully separate staging clone so a failed compile leaves
    #    both the active source checkout and the active binaries untouched.
    _exec(runner, name, "bash", "-lc", (
        f"rm -rf {stage} && git clone --depth 1 --branch {target} {root} {stage} && "
        f"cmake -S {stage} -B {stage}/build -G Ninja -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release && "
        f"cmake --build {stage}/build --target llama-server llama-cli llama-quantize -j2 && "
        f"test -x {stage}/build/bin/llama-server && test -x {stage}/build/bin/llama-quantize"
    ))
    switched = False
    try:
        # 3. Atomic switch of source+binaries as one unit.
        _exec(runner, name, "bash", "-lc", (
            f"cd {root} && rm -rf {backup} && mv {root} {backup} && mv {stage} {root}"
        ))
        switched = True
        health = restart_and_wait(state, runner)
        record_llamacpp_build(state, runner)
        runner.emit(f"llama.cpp updated to {target}; server healthy")
        return {"updated_to": target, **health}
    except Exception as update_error:  # noqa: BLE001 - restore must cover every failure
        runner.emit(f"Update failed ({update_error}); restoring the previous build")
        try:
            if switched:
                _exec(runner, name, "bash", "-lc", (
                    f"cd {root} && rm -rf {stage} && mv {root} {stage} && mv {backup} {root}"
                ))
            restart_and_wait(state, runner)
        except Exception as restore_error:  # noqa: BLE001
            raise RuntimeError(
                f"Update failed ({update_error}) and restoring the previous build also "
                f"failed ({restore_error}). Inspect the container build directories."
            ) from update_error
        raise


def rollback_llamacpp(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Restore the most recent prior build directory kept by update_llamacpp."""
    require_acknowledgment(state)
    if not state.get("env_ready"):
        raise RuntimeError("Inference environment is not ready; complete setup first")
    history = [item for item in (state.get("llamacpp_history") or []) if isinstance(item, dict)]
    if not history:
        raise RuntimeError("No previous llama.cpp build is recorded; nothing to roll back to")
    from .server import restart_and_wait

    name = str(state.get("container_name", "llm"))
    root = str(state.get("llama_cpp_path", "/root/llama.cpp"))
    previous = history[-1]
    root_backup = f"{root}-backup"
    runner.emit(f"Rolling llama.cpp back to {previous.get('describe', 'previous build')}")
    _exec(runner, name, "bash", "-lc", (
        f"cd {root} && rm -rf {root}-rolled && mv {root} {root}-rolled && mv {root_backup} {root}"
    ))
    try:
        health = restart_and_wait(state, runner)
    except Exception as rollback_error:  # noqa: BLE001
        _exec(runner, name, "bash", "-lc", (
            f"cd {root} && rm -rf {root_backup} && mv {root} {root_backup} "
            f"&& mv {root}-rolled {root}"
        ))
        raise
    state["llamacpp_build"] = previous
    state["llamacpp_history"] = history[:-1]
    runner.emit("Rollback complete; server healthy on the previous build")
    return {"rolled_back_to": previous.get("describe"), **health}
