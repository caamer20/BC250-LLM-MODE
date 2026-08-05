"""Bounded, reversible BC-250 optimization settings."""

from __future__ import annotations

import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .disclaimer import require_acknowledgment
from .logging_utils import CommandRunner
from .privilege import elevated

CYAN_CONFIG = Path("/etc/cyan-skillfish-governor-smu/config.toml")
CYAN_BACKUP = Path("/etc/cyan-skillfish-governor-smu/config.toml.bc250-original")
CYAN_SERVICE = "cyan-skillfish-governor-smu.service"
SYSCTL_CONFIG = Path("/etc/sysctl.d/99-bc250-llm-mode.conf")
LOGROTATE_CONFIG = Path("/etc/logrotate.d/bc250-llm-mode")

TRIMMABLE_SERVICES: dict[str, str] = {
    "input-remapper.service": "Input Remapper (about 85 MiB on the audited system)",
    "ds-inhibit.service": "DualShock/DualSense mouse inhibitor",
    "displaylink.service": "DisplayLink manager",
    "dmemcg-booster-system.service": "Gaming foreground-memory booster",
    "uresourced.service": "Gaming resource allocator",
}

DEFAULT_OPTIMIZATIONS: dict[str, Any] = {
    "runtime_enabled": True,
    "flash_attention": "auto",
    "batch_size": 1024,
    "ubatch_size": 256,
    "kv_cache_type": "q8_0",
    "parallel_slots": 4,
    "gpu_enabled": False,
    "gpu_min_mhz": 500,
    "gpu_max_mhz": 1850,
    "thermal_throttle_c": 85,
    "thermal_recovery_c": 75,
    "memory_enabled": False,
    "swappiness": 100,
    "safeguards_enabled": False,
    "restart_window_sec": 300,
    "restart_burst": 3,
    "restart_delay_sec": 10,
    "log_max_mib": 100,
    "trim_services_enabled": False,
    "trim_services": {unit: False for unit in TRIMMABLE_SERVICES},
}


def normalized_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(DEFAULT_OPTIMIZATIONS)
    if settings:
        for key, value in settings.items():
            if key == "trim_services" and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
    return result


def kv_scale_for_settings(settings: dict[str, Any] | None) -> float:
    checked = normalized_settings(settings)
    return 0.5 if checked["runtime_enabled"] and checked["kv_cache_type"] == "q4_0" else 1.0


def parallel_slots_for_settings(settings: dict[str, Any] | None) -> int:
    return int(normalized_settings(settings)["parallel_slots"])


def _bounded_int(settings: dict[str, Any], key: str, low: int, high: int) -> int:
    try:
        value = int(settings[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if not low <= value <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return value


def validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    result = normalized_settings(settings)
    if result["flash_attention"] not in {"auto", "on", "off"}:
        raise ValueError("flash_attention must be auto, on, or off")
    if result["kv_cache_type"] not in {"q8_0", "q4_0"}:
        raise ValueError("kv_cache_type must be q8_0 or q4_0")
    _bounded_int(result, "parallel_slots", 1, 8)
    batch = _bounded_int(result, "batch_size", 128, 2048)
    ubatch = _bounded_int(result, "ubatch_size", 64, 512)
    if batch % 64 or ubatch % 64:
        raise ValueError("batch_size and ubatch_size must be multiples of 64")
    if ubatch > batch:
        raise ValueError("ubatch_size cannot exceed batch_size")
    minimum = _bounded_int(result, "gpu_min_mhz", 500, 1200)
    maximum = _bounded_int(result, "gpu_max_mhz", 1500, 2000)
    if minimum >= maximum:
        raise ValueError("GPU minimum frequency must be lower than maximum frequency")
    throttle = _bounded_int(result, "thermal_throttle_c", 75, 90)
    recovery = _bounded_int(result, "thermal_recovery_c", 60, 85)
    if recovery > throttle - 5:
        raise ValueError("Thermal recovery must be at least 5°C below the throttle point")
    _bounded_int(result, "swappiness", 10, 200)
    _bounded_int(result, "restart_window_sec", 60, 900)
    _bounded_int(result, "restart_burst", 1, 10)
    _bounded_int(result, "restart_delay_sec", 5, 60)
    _bounded_int(result, "log_max_mib", 10, 500)
    trims = result.get("trim_services")
    if not isinstance(trims, dict) or any(unit not in TRIMMABLE_SERVICES for unit in trims):
        raise ValueError("trim_services contains an unsupported systemd unit")
    return result


def _install_text(path: Path, content: str, runner: CommandRunner) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(content)
        temporary = handle.name
    try:
        runner.run(elevated(["install", "-D", "-m", "0644", temporary, str(path)]))
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _update_toml_value(text: str, section: str, key: str, value: int) -> str:
    lines = text.splitlines(keepends=True)
    active = False
    changed = False
    section_header = f"[{section}]"
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)[^#\r\n]*?(\s*(?:#.*)?)$")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            active = stripped == section_header
            continue
        if active:
            match = pattern.match(line.rstrip("\r\n"))
            if match:
                ending = "\n" if line.endswith("\n") else ""
                lines[index] = f"{match.group(1)}{value}{match.group(2)}{ending}"
                changed = True
                break
    if not changed:
        raise ValueError(f"Cyan config is missing {section}.{key}")
    return "".join(lines)


def _apply_gpu(settings: dict[str, Any], state: dict[str, Any], runner: CommandRunner) -> None:
    if not CYAN_CONFIG.exists():
        raise RuntimeError(f"Cyan governor config was not found at {CYAN_CONFIG}")
    if not CYAN_BACKUP.exists():
        runner.run(elevated(["cp", "--preserve=mode,ownership,timestamps", str(CYAN_CONFIG), str(CYAN_BACKUP)]))
    state["gpu_optimizer_applied"] = True
    content = CYAN_CONFIG.read_text(encoding="utf-8")
    for section, key, setting in (
        ("frequency-range", "min", "gpu_min_mhz"),
        ("frequency-range", "max", "gpu_max_mhz"),
        ("temperature", "throttling", "thermal_throttle_c"),
        ("temperature", "throttling_recovery", "thermal_recovery_c"),
    ):
        content = _update_toml_value(content, section, key, int(settings[setting]))
    if content != CYAN_CONFIG.read_text(encoding="utf-8"):
        _install_text(CYAN_CONFIG, content, runner)
        runner.run(elevated(["systemctl", "restart", CYAN_SERVICE]))


def _revert_gpu(state: dict[str, Any], runner: CommandRunner) -> None:
    if not state.get("gpu_optimizer_applied"):
        return
    if CYAN_BACKUP.exists():
        runner.run(elevated(["cp", "--preserve=mode,ownership,timestamps", str(CYAN_BACKUP), str(CYAN_CONFIG)]))
        runner.run(elevated(["rm", "-f", str(CYAN_BACKUP)]))
        runner.run(elevated(["systemctl", "restart", CYAN_SERVICE]), check=False)
    state["gpu_optimizer_applied"] = False


def _service_status(runner: CommandRunner, unit: str) -> dict[str, bool]:
    enabled = runner.run(["systemctl", "is-enabled", unit], check=False).stdout.strip() == "enabled"
    active = runner.run(["systemctl", "is-active", unit], check=False).stdout.strip() == "active"
    return {"enabled": enabled, "active": active}


def _apply_service_trim(settings: dict[str, Any], state: dict[str, Any], runner: CommandRunner) -> None:
    previous = dict(state.get("optimization_service_previous", {}))
    selected = settings["trim_services"] if settings["trim_services_enabled"] else {}
    for unit in TRIMMABLE_SERVICES:
        should_trim = bool(selected.get(unit, False))
        if should_trim:
            if unit not in previous:
                previous[unit] = _service_status(runner, unit)
            runner.run(elevated(["systemctl", "stop", unit]), check=False)
            runner.run(elevated(["systemctl", "disable", unit]), check=False)
        elif unit in previous:
            status = previous.pop(unit)
            if status.get("enabled"):
                runner.run(elevated(["systemctl", "enable", unit]), check=False)
            if status.get("active"):
                runner.run(elevated(["systemctl", "start", unit]), check=False)
    state["optimization_service_previous"] = previous


def _apply_memory(settings: dict[str, Any], state: dict[str, Any], runner: CommandRunner) -> None:
    if settings["memory_enabled"]:
        if "optimization_original_swappiness" not in state:
            try:
                state["optimization_original_swappiness"] = int(
                    Path("/proc/sys/vm/swappiness").read_text(encoding="utf-8").strip()
                )
            except (OSError, ValueError):
                state["optimization_original_swappiness"] = 60
        value = int(settings["swappiness"])
        _install_text(SYSCTL_CONFIG, f"# Managed by BC250 LLM MODE\nvm.swappiness={value}\n", runner)
        runner.run(elevated(["sysctl", "-w", f"vm.swappiness={value}"]))
    elif "optimization_original_swappiness" in state:
        original = int(state.pop("optimization_original_swappiness"))
        runner.run(elevated(["sysctl", "-w", f"vm.swappiness={original}"]), check=False)
        if SYSCTL_CONFIG.exists():
            runner.run(elevated(["rm", "-f", str(SYSCTL_CONFIG)]), check=False)


def _apply_log_rotation(settings: dict[str, Any], state: dict[str, Any], runner: CommandRunner) -> None:
    if settings["safeguards_enabled"]:
        server_log = str(
            state.get(
                "server_log",
                "/root/llama-server.log" if os.getuid() == 0 else Path(str(state["logs_dir"])) / "llama-server.log",
            )
        )
        content = f"""{server_log} {{
    size {int(settings['log_max_mib'])}M
    rotate 3
    compress
    copytruncate
    missingok
    notifempty
}}
"""
        _install_text(LOGROTATE_CONFIG, content, runner)
        state["optimization_logrotate_applied"] = True
    elif state.get("optimization_logrotate_applied"):
        if LOGROTATE_CONFIG.exists():
            runner.run(elevated(["rm", "-f", str(LOGROTATE_CONFIG)]), check=False)
        state["optimization_logrotate_applied"] = False


def apply_optimizations(
    state: dict[str, Any], settings: dict[str, Any], runner: CommandRunner
) -> dict[str, Any]:
    require_acknowledgment(state)
    checked = validate_settings(settings)
    if checked["gpu_enabled"]:
        _apply_gpu(checked, state, runner)
    else:
        _revert_gpu(state, runner)
    _apply_service_trim(checked, state, runner)
    _apply_memory(checked, state, runner)
    _apply_log_rotation(checked, state, runner)
    state["optimizations"] = checked
    state["optimizations_applied"] = True
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 6)
    runner.emit("Optimization settings validated and applied")
    return state


def revert_optimizations(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    settings = normalized_settings(state.get("optimizations"))
    settings.update(gpu_enabled=False, memory_enabled=False, safeguards_enabled=False, trim_services_enabled=False)
    _revert_gpu(state, runner)
    _apply_service_trim(settings, state, runner)
    _apply_memory(settings, state, runner)
    _apply_log_rotation(settings, state, runner)
    state["optimizations_applied"] = False
    return state
