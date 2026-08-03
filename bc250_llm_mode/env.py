"""Containerized llama.cpp/Vulkan environment setup."""

from __future__ import annotations

import os
import shutil
from typing import Any

from .disclaimer import require_acknowledgment
from .logging_utils import CommandRunner

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
    return state
