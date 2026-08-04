from __future__ import annotations

import json
import os
import pwd
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .logging_utils import CommandRunner
from .optimize import normalized_settings
from .privilege import elevated


def current_model_record(state: dict[str, Any]) -> dict[str, Any]:
    model_id = state.get("current_model")
    for record in state.get("installed_models", []):
        if record.get("id") == model_id:
            return record
    raise RuntimeError("No current installed model is selected")


def generate_launcher(state: dict[str, Any]) -> Path:
    app_dir = Path(str(state["app_dir"])).expanduser()
    app_dir.mkdir(parents=True, exist_ok=True)
    launcher = app_dir / "run-model.sh"
    state_path = Path(str(state.get("state_path", app_dir / "state.json"))).expanduser()
    llama_server = Path(str(state["llama_cpp_path"])) / "build/bin/llama-server"
    content = f"""#!/usr/bin/env bash
set -euo pipefail
STATE=${{BC250_STATE_PATH:-{state_path}}}
readarray -t CFG < <(python3 - "$STATE" <<'PY'
import json, sys
s = json.load(open(sys.argv[1], encoding='utf-8'))
r = next(x for x in s['installed_models'] if x['id'] == s['current_model'])
print(r['path'])
print(s.get('current_ctx', 8192))
print(s.get('server_port', 8080))
o = s.get('optimizations', {{}})
if not o.get('runtime_enabled', True):
    o = {{}}
print(o.get('flash_attention', 'auto'))
print(o.get('batch_size', 2048))
print(o.get('ubatch_size', 512))
print(o.get('kv_cache_type', 'q8_0'))
alias = str(r.get('display_name') or r.get('id') or 'local').replace('\\n', ' ').strip()
print(alias)
PY
)
export GGML_VK_DISABLE_F16=1
export GGML_VK_FORCE_SYNC=1
exec {llama_server} -m "${{CFG[0]}}" --host 127.0.0.1 --port "${{CFG[2]}}" \\
  --n-gpu-layers 99 --ctx-size "${{CFG[1]}}" --flash-attn "${{CFG[3]}}" \\
  --batch-size "${{CFG[4]}}" --ubatch-size "${{CFG[5]}}" \\
  --cache-type-k "${{CFG[6]}}" --cache-type-v "${{CFG[6]}}" \\
  --alias "${{CFG[7]}}" \\
  --temp 0.3 --top-p 0.9 --min-p 0.05 --repeat-penalty 1.05
"""
    launcher.write_text(content, encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _service_text(state: dict[str, Any], launcher: Path) -> str:
    container = state.get("container_name", "llm")
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    identity = ""
    if uid:
        identity = (
            f"User={account.pw_name}\nGroup={account.pw_gid}\n"
            f"Environment=HOME={account.pw_dir}\nEnvironment=XDG_RUNTIME_DIR=/run/user/{uid}\n"
        )
    podman = shutil.which("podman") or "/usr/bin/podman"
    runtime_prep = ""
    if uid == 0:
        install = shutil.which("install") or "/usr/bin/install"
        # Root-created Distrobox containers bind-mount /run/user/0. That path
        # is volatile and may not exist on a headless boot until root logs in.
        runtime_prep = f"ExecStartPre={install} -d -m 0700 -o 0 -g 0 /run/user/0\n"
    # Bazzite maps /root to /var/roothome. systemd/SELinux can reject an
    # append: output target there with status 209/STDOUT before ExecStart runs.
    # /var/log is the appropriate system-service log location.
    server_log = (
        "/var/log/bc250-llm-server.log"
        if uid == 0
        else str(Path(str(state["logs_dir"])).expanduser() / "llama-server.log")
    )
    state["server_log"] = server_log
    tuning = normalized_settings(state.get("optimizations"))
    limits = ""
    restart_delay = 10
    if tuning["safeguards_enabled"]:
        limits = (
            f"StartLimitIntervalSec={int(tuning['restart_window_sec'])}\n"
            f"StartLimitBurst={int(tuning['restart_burst'])}\n"
        )
        restart_delay = int(tuning["restart_delay_sec"])
    return f"""[Unit]
Description=BC250 llama.cpp model server
After=network-online.target
Wants=network-online.target
{limits}

[Service]
{identity}{runtime_prep}ExecStartPre=-{podman} start {container}
ExecStart={podman} exec --user root {container} {launcher}
Restart=on-failure
RestartSec={restart_delay}
StandardOutput=append:{server_log}
StandardError=append:{server_log}

[Install]
WantedBy=multi-user.target
"""


def install_service(
    state: dict[str, Any], runner: CommandRunner, *, enable_and_start: bool = True
) -> Path:
    launcher = generate_launcher(state)
    service_name = str(state.get("service_name", "bc250-llm.service"))
    destination = Path("/etc/systemd/system") / service_name
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(_service_text(state, launcher))
        temporary = handle.name
    try:
        runner.run(elevated(["install", "-m", "0644", temporary, str(destination)]))
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    runner.run(elevated(["systemctl", "daemon-reload"]))
    if os.getuid():
        runner.run(elevated(["loginctl", "enable-linger", pwd.getpwuid(os.getuid()).pw_name]), check=False)
    if enable_and_start:
        runner.run(elevated(["systemctl", "enable", "--now", service_name]))
    else:
        runner.emit(f"Refreshed {service_name} definition without changing its enabled/running state")
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 9)
    return destination


def restart_service(state: dict[str, Any], runner: CommandRunner) -> None:
    runner.run(elevated(["systemctl", "restart", str(state["service_name"])]))


def _unit_property(runner: CommandRunner, service: str, prop: str) -> str:
    result = runner.run(
        ["systemctl", "show", service, f"--property={prop}", "--value"], check=False
    )
    return result.stdout.strip()


def service_status(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Return live systemd state without assuming the service is installed."""
    service = str(state.get("service_name", "bc250-llm.service"))
    load = _unit_property(runner, service, "LoadState")
    active = _unit_property(runner, service, "ActiveState") if load != "not-found" else "inactive"
    sub = _unit_property(runner, service, "SubState") if load != "not-found" else "dead"
    unit_file = _unit_property(runner, service, "UnitFileState") if load != "not-found" else "disabled"
    return {
        "service": service,
        "installed": bool(load and load != "not-found"),
        "load_state": load or "unknown",
        "active": active == "active",
        "active_state": active or "unknown",
        "sub_state": sub or "unknown",
        "enabled": unit_file in {"enabled", "enabled-runtime", "static"},
        "unit_file_state": unit_file or "unknown",
    }


def start_service(
    state: dict[str, Any], runner: CommandRunner, *, wait_for_health: bool = True
) -> dict[str, Any]:
    """Start only the systemd-owned llama server, optionally waiting for its API."""
    current_model_record(state)
    runner.run(elevated(["systemctl", "start", str(state["service_name"])]))
    if wait_for_health:
        return health_check(state, runner)
    return service_status(state, runner)


def stop_service(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    service = str(state["service_name"])
    runner.run(elevated(["systemctl", "stop", service]), check=False)
    # Podman's attached exec can report a nonzero exit while llama-server is
    # gracefully terminating. An explicit user Stop should settle at inactive,
    # while genuine unexpected failures still retain Restart=on-failure.
    runner.run(elevated(["systemctl", "reset-failed", service]), check=False)
    return service_status(state, runner)


def restart_and_wait(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    current_model_record(state)
    restart_service(state, runner)
    return health_check(state, runner)


def _json_get(url: str, timeout: float = 5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def health_check(state: dict[str, Any], runner: CommandRunner | None = None, timeout: int = 120) -> dict[str, Any]:
    port = int(state.get("server_port", 8080))
    deadline = time.monotonic() + timeout
    last_error = "server did not respond"
    while time.monotonic() < deadline:
        try:
            health = _json_get(f"http://127.0.0.1:{port}/health")
            models = _json_get(f"http://127.0.0.1:{port}/v1/models")
            metrics = system_metrics()
            result = {
                "healthy": True,
                "model_id": state.get("current_model"),
                "n_ctx": int(state.get("current_ctx", 8192)),
                "vram_used_mib": metrics.get("vram_used_mib"),
                "vram_total_mib": metrics.get("vram_total_mib"),
                "health": health,
                "models": models,
            }
            if runner:
                runner.emit(
                    f"Server healthy on 127.0.0.1:{port}; model={result['model_id']} "
                    f"n_ctx={result['n_ctx']} VRAM={result['vram_used_mib']}/{result['vram_total_mib']} MiB"
                )
            return result
        except (OSError, urllib.error.URLError, ValueError) as exc:
            last_error = str(exc)
            time.sleep(2)
    if runner:
        show_server_failure(state, runner, last_error)
    raise TimeoutError(f"Model server was not healthy within {timeout}s: {last_error}")


def diagnose_server_log(text: str) -> str:
    lower = text.lower()
    if "erroroutofdevicememory" in lower and any(word in lower for word in ("max", "fused", "imatrix")):
        return "This is a fused MAX repack; it cannot load on this card. Choose a standard-layout catalog model."
    if "missing tensor" in lower and "nextn" in lower:
        return "MTP metadata mismatch detected; run the nextn metadata repair."
    if "missing tensor" in lower and "blk." in lower:
        return "Block-count metadata mismatch detected; run GGUF verification/repair."
    if "vulkan" in lower and any(word in lower for word in ("fail", "error", "no device")):
        return "Vulkan initialization failed; re-run inference environment setup."
    if "erroroutofdevicememory" in lower:
        return "GPU allocation failed. Choose a smaller quant/context and verify the BIOS 12 GiB UMA allocation."
    if "no module named" in lower:
        return "The inference environment is incomplete; re-run environment setup."
    return "Review the server log above; it is the ground truth for model load failures."


def show_server_failure(state: dict[str, Any], runner: CommandRunner, error: str = "") -> str:
    server_log = str(state.get("server_log", "/root/llama-server.log"))
    command = ["tail", "-n", "120", server_log]
    result = runner.run(elevated(command) if server_log.startswith("/root/") else command, check=False)
    guidance = diagnose_server_log(result.stdout)
    runner.emit(f"Server health failure: {error}")
    runner.emit(f"Guidance: {guidance}")
    return guidance


def system_metrics() -> dict[str, Any]:
    # Discover on every invocation because cardN renumbers.
    from .hardware import _bytes_to_mib, _read_text, find_amd_gpu

    gpu = find_amd_gpu()
    if not gpu:
        return {"error": "No AMD GPU found"}
    hwmon_values: dict[str, str] = {}
    for hwmon in (gpu / "hwmon").glob("hwmon*"):
        for filename in ("temp1_input", "freq1_input", "power1_average"):
            value = _read_text(hwmon / filename)
            if value:
                hwmon_values[filename] = value
    return {
        "gpu_path": str(gpu),
        "vram_used_mib": _bytes_to_mib(_read_text(gpu / "mem_info_vram_used")),
        "vram_total_mib": _bytes_to_mib(_read_text(gpu / "mem_info_vram_total")),
        **hwmon_values,
    }
