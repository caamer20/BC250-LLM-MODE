"""CachyOS/portable-host integration contracts."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from bc250_llm_mode import bootstrap, llmmode
from bc250_llm_mode.host_platform import (
    BootManager,
    HostPlatformService,
    IntegrationTier,
    PackageInstallPlan,
    PackageManager,
    detect_host_platform,
    pacman_install_is_safe,
)


HOST_COMMANDS = {
    "systemctl", "udevadm", "pacman", "podman", "distrobox", "vulkaninfo",
}


def _cachyos(tmp_path, *, boot_path="/etc/sdboot-manage.conf"):
    os_release = tmp_path / "os-release"
    os_release.write_text(
        'ID=cachyos\nNAME="CachyOS"\nPRETTY_NAME="CachyOS"\n'
        'VERSION_ID=rolling\nID_LIKE="arch"\n',
        encoding="utf-8",
    )
    existing = {"/run/systemd/system", boot_path}
    return HostPlatformService.detect(
        os_release_path=os_release,
        command_exists=lambda name: name in HOST_COMMANDS,
        path_exists=lambda path: path in existing,
        platform_name="linux",
    )


def test_cachyos_is_a_native_pacman_systemd_profile(tmp_path):
    service = _cachyos(tmp_path)
    profile = service.profile

    assert profile.is_cachyos is True
    assert profile.integration_tier is IntegrationTier.NATIVE
    assert profile.package_manager is PackageManager.PACMAN
    assert profile.boot_manager is BootManager.SYSTEMD_BOOT
    assert profile.supports_current_boot_llm_mode is True
    assert profile.persistent_kernel_policy == "observe-only"

    tkinter = profile.tkinter_plan()
    assert tkinter.packages == ("tk",)
    assert tkinter.argv == (
        "pacman", "-S", "--needed", "--noconfirm", "tk",
    )
    runtime = profile.runtime_host_plan()
    assert runtime.packages == (
        "podman", "distrobox", "vulkan-radeon", "vulkan-tools",
    )
    # Partial-upgrade primitives are forbidden by construction.
    assert "-Sy" not in tkinter.argv
    assert "-Sy" not in runtime.argv
    assert "-Syu" not in tkinter.argv
    assert "-Syu" not in runtime.argv


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("/etc/sdboot-manage.conf", BootManager.SYSTEMD_BOOT),
        ("/etc/default/grub", BootManager.GRUB),
        ("/etc/default/limine", BootManager.LIMINE),
        ("/boot/refind_linux.conf", BootManager.REFIND),
    ),
)
def test_cachyos_detects_each_supported_boot_manager(tmp_path, path, expected):
    assert _cachyos(tmp_path, boot_path=path).profile.boot_manager is expected


def test_ambiguous_boot_manager_disables_persistent_mutation(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=cachyos\nID_LIKE=arch\n", encoding="utf-8")
    existing = {
        "/run/systemd/system", "/etc/sdboot-manage.conf", "/etc/default/grub",
    }
    profile = detect_host_platform(
        os_release_path=os_release,
        command_exists=lambda name: name in HOST_COMMANDS,
        path_exists=lambda path: path in existing,
        platform_name="linux",
    )
    assert profile.boot_manager is BootManager.UNKNOWN
    assert profile.persistent_kernel_policy == "observe-only"
    assert any("multiple boot-manager" in item for item in profile.observations)


def test_bazzite_retains_rpm_ostree_profile(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text(
        'ID=bazzite\nPRETTY_NAME="Bazzite"\nID_LIKE="fedora"\n',
        encoding="utf-8",
    )
    commands = {"systemctl", "udevadm", "rpm-ostree", "podman", "distrobox"}
    profile = detect_host_platform(
        os_release_path=os_release,
        command_exists=lambda name: name in commands,
        path_exists=lambda path: path == "/run/systemd/system",
        platform_name="linux",
    )
    assert profile.integration_tier is IntegrationTier.NATIVE
    assert profile.package_manager is PackageManager.RPM_OSTREE
    assert profile.boot_manager is BootManager.RPM_OSTREE
    assert profile.tkinter_plan().requires_reboot is True


def test_arch_family_is_implemented_but_not_claimed_as_qualified(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=endeavouros\nID_LIKE=arch\n", encoding="utf-8")
    profile = detect_host_platform(
        os_release_path=os_release,
        command_exists=lambda name: name in HOST_COMMANDS,
        path_exists=lambda path: path in {
            "/run/systemd/system", "/etc/default/grub",
        },
        platform_name="linux",
    )
    assert profile.integration_tier is IntegrationTier.COMPATIBLE
    assert profile.package_manager is PackageManager.PACMAN
    assert any("qualification" in item for item in profile.observations)


def test_fedora_atomic_marker_wins_over_dnf_command_presence(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=fedora\nID_LIKE=fedora\n", encoding="utf-8")
    commands = {"systemctl", "udevadm", "dnf", "rpm-ostree"}
    profile = detect_host_platform(
        os_release_path=os_release,
        command_exists=lambda name: name in commands,
        path_exists=lambda path: path in {
            "/run/systemd/system", "/run/ostree-booted",
        },
        platform_name="linux",
    )
    assert profile.package_manager is PackageManager.RPM_OSTREE
    assert profile.persistent_kernel_policy == "managed-rpm-ostree"


def test_non_systemd_host_refuses_host_mode(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=cachyos\nID_LIKE=arch\n", encoding="utf-8")
    service = HostPlatformService.detect(
        os_release_path=os_release,
        command_exists=lambda name: name in {"pacman", "udevadm"},
        path_exists=lambda _path: False,
        platform_name="linux",
    )
    assert service.profile.integration_tier is IntegrationTier.UNSUPPORTED
    with pytest.raises(RuntimeError, match="systemd"):
        service.require_current_boot_llm_mode()


class RecordingRunner:
    def __init__(self, *, upgrades=""):
        self.commands = []
        self.messages = []
        self.upgrades = upgrades

    def run(self, command, **kwargs):
        command = list(command)
        self.commands.append((command, kwargs))
        output = self.upgrades if command == ["pacman", "-Qu"] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    def emit(self, message):
        self.messages.append(message)


def test_pacman_preflight_refuses_pending_partial_upgrade():
    runner = RecordingRunner(upgrades="linux 6.0 -> 6.1\n")
    safe, guidance = pacman_install_is_safe(runner)
    assert safe is False
    assert "pacman -Syu" in guidance
    assert runner.commands == [(["pacman", "-Qu"], {"check": False})]


def test_cachyos_tkinter_install_is_exact_and_never_refreshes(tmp_path, monkeypatch):
    platform = _cachyos(tmp_path)
    plan = platform.profile.tkinter_plan()
    runner = RecordingRunner()
    state = {}
    monkeypatch.setattr(bootstrap, "elevated", lambda command: command)

    bootstrap._stage_tkinter(state, SimpleNamespace(), runner, plan)

    commands = [command for command, _kwargs in runner.commands]
    assert commands == [
        ["pacman", "-Qu"],
        ["pacman", "-S", "--needed", "--noconfirm", "tk"],
    ]
    assert state["bootstrap_tkinter_staged"] is True
    assert state["reboot_required"] is False


def test_fresh_native_bootstrap_advances_hardware_before_tkinter(
    tmp_path, monkeypatch
):
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.paths import AppPaths

    application = Application.compose(AppPaths.temporary(tmp_path / "profile"))
    application.setup.acknowledge_safety()
    report = SimpleNamespace(valid=True, errors=[], warnings=[])
    bootstrap._ensure_hardware_validation(application, report)
    assert application.setup.current_workflow()["stage"] == "HARDWARE_VALIDATED"

    plan = PackageInstallPlan(
        purpose="native tkinter",
        manager=PackageManager.DNF,
        packages=("python3-tkinter",),
        argv=("dnf", "install", "-y", "python3-tkinter"),
        requires_reboot=False,
        automatic=True,
        guidance="test",
    )
    monkeypatch.setattr(bootstrap, "elevated", lambda command: command)
    state = application.read_model()
    bootstrap._stage_tkinter(state, application, RecordingRunner(), plan)
    assert application.setup.current_workflow()["stage"] == "TKINTER_READY"
    assert application.read_model()["reboot_required"] is False


def test_missing_cachyos_container_tools_report_reviewed_recovery_plan(
    tmp_path, monkeypatch
):
    from bc250_llm_mode import env

    platform = _cachyos(tmp_path)
    monkeypatch.setattr(env.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="pacman -Syu"):
        env.provision_environment(
            {"disclaimer_ack": True}, RecordingRunner(), platform=platform
        )


def test_cachyos_current_boot_mode_never_calls_rpm_ostree(tmp_path, monkeypatch):
    platform = _cachyos(tmp_path)
    persistent = tmp_path / "etc" / "persistent.rules"
    runtime = tmp_path / "run" / "runtime.rules"
    monkeypatch.setattr(llmmode, "UDEV_RULE_PATH", persistent)
    monkeypatch.setattr(llmmode, "RUNTIME_UDEV_RULE_PATH", runtime)
    monkeypatch.setattr(llmmode, "_require_commands", lambda *_commands: None)
    monkeypatch.setattr(llmmode, "_active_cmdline", lambda: "quiet splash")
    monkeypatch.setattr(llmmode, "_set_gpu_power_control", lambda *_args: None)
    monkeypatch.setattr(llmmode, "elevated", lambda command: command)
    runner = RecordingRunner()
    state = {
        "disclaimer_ack": True,
        "service_name": "bc250-llm.service",
        "setup_phase": 1,
    }

    llmmode.apply_llm_mode(state, runner, platform=platform)

    commands = [command for command, _kwargs in runner.commands]
    assert not any(command and command[0] == "rpm-ostree" for command in commands)
    assert ["systemctl", "set-default", "graphical.target"] in commands
    assert ["systemctl", "disable", "bc250-llm.service"] in commands
    assert state["system_mode"] == "llm-session"
    assert state["pending_karg_mode"] is None


def test_external_kernel_argument_is_reported_not_mutated(tmp_path, monkeypatch):
    platform = _cachyos(tmp_path, boot_path="/etc/default/limine")
    monkeypatch.setattr(llmmode, "_require_commands", lambda *_commands: None)
    monkeypatch.setattr(llmmode, "_active_cmdline", lambda: "amdgpu.runpm=0 quiet")
    monkeypatch.setattr(llmmode, "_set_gpu_power_control", lambda *_args: None)
    monkeypatch.setattr(llmmode, "elevated", lambda command: command)
    monkeypatch.setattr(llmmode, "UDEV_RULE_PATH", tmp_path / "missing")
    runner = RecordingRunner()
    state = {"service_name": "bc250-llm.service"}

    llmmode.stage_desktop_boot(state, runner, platform=platform)

    assert state["pending_karg_mode"] == "external"
    assert not any(
        command and command[0] in {"rpm-ostree", "limine-mkinitcpio"}
        for command, _kwargs in runner.commands
    )
    assert any("externally managed" in message for message in runner.messages)


def test_cachyos_watchdog_keeps_emergency_stop_without_cyan(tmp_path, monkeypatch):
    from bc250_llm_mode import thermals
    from bc250_llm_mode import optimize

    platform = _cachyos(tmp_path)
    assert platform.profile.supports_gpu_tuning is False
    handle = SimpleNamespace(platform=platform)
    state = {
        "optimizations": {
            "thermal_watchdog_enabled": True,
            "thermal_throttle_c": 85,
            "thermal_recovery_c": 75,
            "thermal_stop_c": 95,
            # A migrated Bazzite preference must not override the observed host.
            "gpu_tuning_enabled": True,
        },
        "thermal_watchdog_state": "nominal",
    }
    monkeypatch.setattr(thermals, "read_gpu_temperature", lambda: 90.0)
    monkeypatch.setattr(
        optimize, "apply_gpu_clock_limit",
        lambda *_args: pytest.fail("Cyan clock adapter called on CachyOS"),
    )
    result = thermals.run_watchdog_once(handle, state, RecordingRunner())
    assert result["state"] == "throttled"
    assert result["clock_limit_applied"] is False
    assert "warning" in result


def test_platform_cli_is_json_and_reports_plan(monkeypatch, capsys, tmp_path):
    from bc250_llm_mode import __main__ as cli

    platform = _cachyos(tmp_path)
    monkeypatch.setattr(
        HostPlatformService, "detect", classmethod(lambda cls, **_kwargs: platform)
    )
    assert cli.main(["platform", "plan"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["distribution"]["id"] == "cachyos"
    assert payload["plans"]["tkinter"]["packages"] == ["tk"]


def test_platform_cli_is_precomposition_and_writes_nothing(monkeypatch, capsys, tmp_path):
    from bc250_llm_mode import __main__ as cli

    platform = _cachyos(tmp_path)
    sentinel_home = tmp_path / "sentinel-home"
    sentinel_home.mkdir()
    monkeypatch.setenv("HOME", str(sentinel_home))
    monkeypatch.setattr(
        HostPlatformService, "detect", classmethod(lambda cls, **_kwargs: platform)
    )
    assert cli.main(["platform", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["distribution"]["id"] == "cachyos"
    assert list(sentinel_home.iterdir()) == []


def test_frontends_have_no_distribution_specific_host_bypass():
    from pathlib import Path

    root = Path(__file__).parent.parent / "bc250_llm_mode"
    for rel in (
        "__main__.py", "chat.py", "gui/setup_page.py", "gui/system_page.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "from .llmmode import" not in text
        assert "from ..llmmode import" not in text
        assert "rpm-ostree" not in text
        assert "pacman" not in text
