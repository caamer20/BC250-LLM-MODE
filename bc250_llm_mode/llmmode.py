"""Idempotent Bazzite power/boot configuration and its inverse."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .disclaimer import require_acknowledgment
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
UDEV_RULE = 'ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x1002", ATTR{power/control}="on"\n'


def _run_root(runner: CommandRunner, args: list[str], check: bool = True):
    return runner.run(elevated(args), check=check)


def _require_commands(*commands: str) -> None:
    missing = [name for name in commands if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"Required Bazzite command(s) not found: {', '.join(missing)}")


def apply_llm_mode(
    state: dict[str, Any], runner: CommandRunner, *, mask_desktop_services: bool = False
) -> dict[str, Any]:
    require_acknowledgment(state)
    _require_commands("systemctl", "rpm-ostree", "udevadm")
    runner.emit("Applying idempotent LLM Mode configuration")
    _run_root(runner, ["systemctl", "set-default", "multi-user.target"])
    _run_root(runner, ["systemctl", "mask", *SLEEP_TARGETS])
    if mask_desktop_services:
        # Opt-in only. Display manager names vary; nonexistent units do not abort setup.
        for unit in ("display-manager.service", "sddm.service"):
            _run_root(runner, ["systemctl", "mask", unit], check=False)

    cmdline = Path("/proc/cmdline").read_text(encoding="utf-8") if Path("/proc/cmdline").exists() else ""
    reboot_required = "amdgpu.runpm=0" not in cmdline.split()
    if reboot_required:
        current = runner.run(["rpm-ostree", "kargs"], check=False).stdout
        if "amdgpu.runpm=0" not in current.split():
            _run_root(runner, ["rpm-ostree", "kargs", "--append=amdgpu.runpm=0"])

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(UDEV_RULE)
        temporary = handle.name
    try:
        current_rule = UDEV_RULE_PATH.read_text(encoding="utf-8") if UDEV_RULE_PATH.exists() else ""
        if current_rule != UDEV_RULE:
            _run_root(runner, ["install", "-D", "-m", "0644", temporary, str(UDEV_RULE_PATH)])
        _run_root(runner, ["udevadm", "control", "--reload"])
        # Apply now as well as on the next device add event. GPU is found by vendor every run.
        for vendor_file in sorted(Path("/sys/bus/pci/devices").glob("*/vendor")):
            try:
                if vendor_file.read_text(encoding="utf-8").strip().lower() == "0x1002":
                    runner.run(elevated(["tee", str(vendor_file.parent / "power/control")]), input_text="on\n", check=False)
            except OSError:
                pass
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass

    state["llm_mode_done"] = True
    state["system_mode"] = "llm"
    state["reboot_required"] = reboot_required
    state["pending_karg_mode"] = "enable" if reboot_required else None
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 3)
    if reboot_required:
        runner.emit("Kernel argument staged. Reboot is required; the wizard will resume from this step.")
    return state


def revert_llm_mode(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    _require_commands("systemctl", "rpm-ostree", "udevadm")
    _run_root(runner, ["systemctl", "unmask", *SLEEP_TARGETS], check=False)
    _run_root(runner, ["systemctl", "unmask", "display-manager.service", "sddm.service"], check=False)
    _run_root(runner, ["systemctl", "set-default", "graphical.target"])
    current = runner.run(["rpm-ostree", "kargs"], check=False).stdout
    active_cmdline = (
        Path("/proc/cmdline").read_text(encoding="utf-8")
        if Path("/proc/cmdline").exists()
        else ""
    )
    karg_was_active = "amdgpu.runpm=0" in active_cmdline.split()
    karg_was_staged = "amdgpu.runpm=0" in current.split()
    if karg_was_staged:
        _run_root(runner, ["rpm-ostree", "kargs", "--delete=amdgpu.runpm=0"])
    if UDEV_RULE_PATH.exists():
        _run_root(runner, ["rm", "-f", str(UDEV_RULE_PATH)])
        _run_root(runner, ["udevadm", "control", "--reload"], check=False)
    # Restore normal runtime power management immediately. The persistent udev
    # rule is already gone, so future device-add events retain the OS default.
    for vendor_file in sorted(Path("/sys/bus/pci/devices").glob("*/vendor")):
        try:
            if vendor_file.read_text(encoding="utf-8").strip().lower() == "0x1002":
                runner.run(
                    elevated(["tee", str(vendor_file.parent / "power/control")]),
                    input_text="auto\n",
                    check=False,
                )
        except OSError:
            pass
    state["llm_mode_done"] = False
    state["system_mode"] = "desktop"
    state["reboot_required"] = karg_was_active or karg_was_staged
    state["pending_karg_mode"] = "disable" if state["reboot_required"] else None
    if state["reboot_required"]:
        runner.emit("Desktop boot settings restored. Reboot to finish removing amdgpu.runpm=0.")
    else:
        runner.emit("Desktop boot settings restored; no kernel-argument reboot is pending.")
    return state
