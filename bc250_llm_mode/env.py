"""Containerized environment provisioning (U1.2 §15.5).

This module provisions ONLY container/dependency/venv prerequisites and
performs their smoke checks. The llama.cpp RUNTIME is never cloned,
built, or recorded here: the first runtime comes from the durable
``RUNTIME_UPDATE v1`` operation targeting the shipped immutable pin
(ADR 004 D11). The mutable ``llamacpp_history`` lifecycle is deleted.
"""

from __future__ import annotations

import os
import shutil

from .disclaimer import require_acknowledgment
from .logging_utils import CommandRunner

FEDORA_IMAGE = "registry.fedoraproject.org/fedora:latest"
BUILD_PACKAGES = "git cmake ninja-build gcc-c++ vulkan-loader-devel vulkan-tools python3 python3-pip python3-devel"
PYTHON_PACKAGES = "huggingface_hub[cli] gguf safetensors numpy sentencepiece protobuf torch --extra-index-url https://download.pytorch.org/whl/cpu"


def provision_environment(state: dict, runner: CommandRunner) -> dict:
    """Create/start the distrobox container and the inference venv."""
    require_acknowledgment(state)
    missing = [c for c in ("podman", "distrobox") if not shutil.which(c)]
    if missing:
        raise RuntimeError(
            f"Missing host command(s): {', '.join(missing)}. Install them "
            "through the Bazzite-supported layering path."
        )
    name = str(state.get("container_name", "llm"))
    runner.run(["podman", "container", "exists", name], check=False)
    if _container_missing(runner, name):
        root_flag = ["--root"] if os.geteuid() == 0 else []
        runner.run(["distrobox", "create", *root_flag, "--name", name,
                    "--image", FEDORA_IMAGE, "--yes"])
    runner.run(["podman", "start", name], check=False)
    state.update(env_ready=True)
    return state


def _container_missing(runner: CommandRunner, name: str) -> bool:
    return runner.run(["podman", "container", "exists", name],
                      check=False).returncode != 0


def install_python_environment(state: dict, runner: CommandRunner) -> dict:
    """Provision the inference Python venv inside the container."""
    name = str(state.get("container_name", "llm"))
    venv = str(state.get("venv_path", "/root/.venvs/hf"))
    ready = _exec(runner, name,
                  "test", "-x", f"{venv}/bin/hf",
                  check=False).returncode == 0
    if ready:
        runner.emit(f"Reusing existing inference Python environment at {venv}")
        return state
    _exec(runner, name, "dnf", "install", "-y",
          "python3", "python3-pip", "python3-devel")
    _exec(runner, name, "python3", "-m", "venv", venv)
    _exec(runner, name, f"{venv}/bin/pip", "install", "--upgrade", "pip")
    pip_packages = PYTHON_PACKAGES.split()
    _exec(runner, name, f"{venv}/bin/pip", "install", *pip_packages)
    return state


def ensure_build_prerequisites(state: dict, runner: CommandRunner) -> dict:
    """Install the toolchain packages a durable runtime build requires."""
    name = str(state.get("container_name", "llm"))
    _exec(runner, name, "dnf", "install", "-y", *BUILD_PACKAGES.split())
    return state


def verify_vulkan(state: dict, runner: CommandRunner) -> dict:
    """Fail-closed Vulkan capability check inside the container."""
    name = str(state.get("container_name", "llm"))
    vulkan = _exec(runner, name, "vulkaninfo", "--summary", check=False)
    if vulkan.returncode == 127:
        _exec(runner, name, "dnf", "install", "-y", "vulkan-tools")
        vulkan = _exec(runner, name, "vulkaninfo", "--summary", check=False)
    combined = vulkan.stdout.lower()
    if vulkan.returncode or not any(
        token in combined for token in ("bc-250", "gfx1013", "amd", "radeon")
    ):
        raise RuntimeError(
            "Vulkan smoke test failed: the container cannot see an AMD "
            "Vulkan device."
        )
    return {"vulkan_ok": True}


def setup_environment(state: dict, runner: CommandRunner) -> dict:
    """Compatibility entry: provisioning WITHOUT any llama.cpp build.

    The wizard composes this with the durable pinned runtime update; the
    llama.cpp clone/build path that used to live here is DELETED (the AST
    guards in tests/test_runtime_architecture.py enforce it).
    """
    provision_environment(state, runner)
    install_python_environment(state, runner)
    ensure_build_prerequisites(state, runner)
    verify_vulkan(state, runner)
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 4)
    return state


def _exec(runner: CommandRunner, name: str, *args: str, check: bool = True):
    return runner.run(["podman", "exec", "--user", "root", name, *args],
                      check=check)
