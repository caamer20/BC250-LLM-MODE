"""Idempotent, platform-gated current-boot power policy and its inverse."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .disclaimer import require_acknowledgment
from .host_platform import HostPlatformService
from .logging_utils import CommandRunner
from .privilege import elevated

SLEEP_TARGETS = (
    "sleep.target",
    "suspend.target",
    "hibernate.target",
    "hybrid-sleep.target",
    "suspend-then-hibernate.target",
)
UDEV_RULE_PATH = Path("/etc/udev/rules.d/99-amdgpu-nosleep.rules")
RUNTIME_UDEV_RULE_PATH = Path("/run/udev/rules.d/99-amdgpu-nosleep.rules")
UDEV_RULE = 'ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x1002", ATTR{power/control}="on"\n'


def _run_root(runner: CommandRunner, args: list[str], check: bool = True):
    return runner.run(elevated(args), check=check)


def _require_commands(*commands: str) -> None:
    missing = [name for name in commands if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"Required host command(s) not found: {', '.join(missing)}")


def _platform_service(platform: Any = None) -> HostPlatformService:
    if isinstance(platform, HostPlatformService):
        return platform
    return HostPlatformService.detect()


def _active_cmdline() -> str:
    try:
        return Path("/proc/cmdline").read_text(encoding="utf-8")
    except OSError:
        return ""


def _set_gpu_power_control(runner: CommandRunner, value: str) -> None:
    for vendor_file in sorted(Path("/sys/bus/pci/devices").glob("*/vendor")):
        try:
            if vendor_file.read_text(encoding="utf-8").strip().lower() == "0x1002":
                runner.run(
                    elevated(["tee", str(vendor_file.parent / "power/control")]),
                    input_text=value + "\n",
                    check=False,
                )
        except OSError:
            pass


def stage_desktop_boot(
    state: dict[str, Any], runner: CommandRunner, *, platform: Any = None
) -> dict[str, Any]:
    """Guarantee a normal graphical next boot without stopping this boot's LLM."""
    host = _platform_service(platform)
    host.require_current_boot_llm_mode()
    _require_commands(*host.profile.required_host_mode_commands())
    service = str(state.get("service_name", "bc250-llm.service"))
    runner.emit(f"Staging normal {host.profile.label} desktop mode for the next boot")
    _run_root(runner, ["systemctl", "set-default", "graphical.target"])
    _run_root(runner, ["systemctl", "unmask", *SLEEP_TARGETS], check=False)
    _run_root(
        runner,
        ["systemctl", "unmask", *host.profile.desktop_units],
        check=False,
    )
    # Disable only; an explicitly started model may continue for this boot.
    _run_root(runner, ["systemctl", "disable", service], check=False)

    cleanup = host.persistent_kernel_cleanup(
        "amdgpu.runpm=0",
        runner=runner,
        run_root=lambda args, check=True: _run_root(runner, args, check=check),
    )
    if UDEV_RULE_PATH.exists():
        _run_root(runner, ["rm", "-f", str(UDEV_RULE_PATH)])
    _run_root(runner, ["udevadm", "control", "--reload"], check=False)

    active_karg = "amdgpu.runpm=0" in _active_cmdline().split()
    state.update(
        boot_policy="desktop",
        desktop_on_reboot=True,
        llm_autostart=False,
        desktop_reboot_pending=active_karg,
        pending_karg_mode=(
            "disable" if active_karg and cleanup.get("policy") == "managed-rpm-ostree"
            else "external" if active_karg else None
        ),
    )
    runner.emit(
        "Next boot is graphical desktop mode and bc250-llm.service is disabled; "
        "the current model process was not stopped."
    )
    if active_karg and cleanup.get("policy") == "observe-only":
        runner.emit(
            "amdgpu.runpm=0 is active but its persistent boot configuration "
            f"is externally managed by {host.profile.boot_manager.value}. "
            "BC250 LLM MODE did not edit it; remove it with the host's boot "
            "manager before expecting normal runtime power management after reboot."
        )
    return state


def apply_llm_mode(
    state: dict[str, Any], runner: CommandRunner, *,
    mask_desktop_services: bool = False, platform: Any = None
) -> dict[str, Any]:
    require_acknowledgment(state)
    host = _platform_service(platform)
    host.require_current_boot_llm_mode()
    _require_commands(*host.profile.required_host_mode_commands())
    runner.emit("Applying current-boot LLM Mode session")
    stage_desktop_boot(state, runner, platform=host)
    # Runtime masks disappear automatically on reboot.
    _run_root(runner, ["systemctl", "mask", "--runtime", *SLEEP_TARGETS])
    if mask_desktop_services:
        # Opt-in and runtime-only. A reboot always restores the desktop units.
        for unit in host.profile.desktop_units:
            _run_root(runner, ["systemctl", "mask", "--runtime", unit], check=False)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(UDEV_RULE)
        temporary = handle.name
    try:
        current_rule = (
            RUNTIME_UDEV_RULE_PATH.read_text(encoding="utf-8")
            if RUNTIME_UDEV_RULE_PATH.exists()
            else ""
        )
        if current_rule != UDEV_RULE:
            _run_root(
                runner,
                ["install", "-D", "-m", "0644", temporary, str(RUNTIME_UDEV_RULE_PATH)],
            )
        _run_root(runner, ["udevadm", "control", "--reload"])
        _set_gpu_power_control(runner, "on")
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass

    state["llm_mode_done"] = True
    state["system_mode"] = "llm-session"
    state["reboot_required"] = False
    try:
        state["llm_session_boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        state["llm_session_boot_id"] = None
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 3)
    runner.emit(
        f"LLM Mode is active for this boot only. Reboot returns to normal "
        f"{host.profile.label} desktop mode."
    )
    return state


def revert_llm_mode(
    state: dict[str, Any], runner: CommandRunner, *, platform: Any = None
) -> dict[str, Any]:
    host = _platform_service(platform)
    host.require_current_boot_llm_mode()
    _require_commands(*host.profile.required_host_mode_commands())
    stage_desktop_boot(state, runner, platform=host)
    _run_root(runner, ["systemctl", "unmask", *SLEEP_TARGETS], check=False)
    _run_root(runner, ["systemctl", "unmask", "--runtime", *SLEEP_TARGETS], check=False)
    _run_root(runner, ["systemctl", "unmask", *host.profile.desktop_units], check=False)
    _run_root(
        runner,
        ["systemctl", "unmask", "--runtime", *host.profile.desktop_units],
        check=False,
    )
    if RUNTIME_UDEV_RULE_PATH.exists():
        _run_root(runner, ["rm", "-f", str(RUNTIME_UDEV_RULE_PATH)])
        _run_root(runner, ["udevadm", "control", "--reload"], check=False)
    _set_gpu_power_control(runner, "auto")
    state["llm_mode_done"] = False
    state["system_mode"] = "desktop"
    state["llm_session_boot_id"] = None
    state["reboot_required"] = bool(state.get("desktop_reboot_pending"))
    if state.get("pending_karg_mode") == "external":
        runner.emit(
            "Desktop services are restored. Remove amdgpu.runpm=0 with the "
            f"host's {host.profile.boot_manager.value} workflow, then reboot "
            "to restore normal runtime power management."
        )
    elif state["reboot_required"]:
        runner.emit("Desktop boot settings restored. Reboot to finish removing amdgpu.runpm=0.")
    else:
        runner.emit("Desktop boot settings restored; no kernel-argument reboot is pending.")
    return state
