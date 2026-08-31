import subprocess

from bc250_llm_mode import llmmode
from bc250_llm_mode.host_platform import HostPlatformService


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        command = list(command)
        self.commands.append((command, kwargs))
        output = "amdgpu.runpm=0\n" if command == ["rpm-ostree", "kargs"] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    def emit(self, message):
        self.messages.append(message)


def test_llm_mode_is_runtime_only_and_next_boot_is_desktop(tmp_path, monkeypatch):
    os_release = tmp_path / "os-release"
    os_release.write_text(
        'ID=bazzite\nPRETTY_NAME="Bazzite Test"\nID_LIKE="fedora"\n',
        encoding="utf-8",
    )
    platform = HostPlatformService.detect(
        os_release_path=os_release,
        command_exists=lambda _name: True,
        path_exists=lambda path: path == "/run/systemd/system",
        platform_name="linux",
    )
    persistent = tmp_path / "etc" / "99-amdgpu-nosleep.rules"
    persistent.parent.mkdir(parents=True)
    persistent.write_text(llmmode.UDEV_RULE, encoding="utf-8")
    runtime = tmp_path / "run" / "99-amdgpu-nosleep.rules"
    monkeypatch.setattr(llmmode, "UDEV_RULE_PATH", persistent)
    monkeypatch.setattr(llmmode, "RUNTIME_UDEV_RULE_PATH", runtime)
    monkeypatch.setattr(llmmode, "_require_commands", lambda *commands: None)
    monkeypatch.setattr(llmmode, "_active_cmdline", lambda: "quiet amdgpu.runpm=0")
    monkeypatch.setattr(llmmode, "_set_gpu_power_control", lambda runner, value: None)
    monkeypatch.setattr(llmmode, "elevated", lambda command: command)
    state = {
        "disclaimer_ack": True,
        "service_name": "bc250-llm.service",
        "setup_phase": 1,
    }
    runner = FakeRunner()

    llmmode.apply_llm_mode(state, runner, platform=platform)

    commands = [command for command, _kwargs in runner.commands]
    assert ["systemctl", "set-default", "graphical.target"] in commands
    assert ["systemctl", "set-default", "multi-user.target"] not in commands
    assert ["systemctl", "disable", "bc250-llm.service"] in commands
    assert ["rpm-ostree", "kargs", "--delete=amdgpu.runpm=0"] in commands
    assert any(command[:3] == ["systemctl", "mask", "--runtime"] for command in commands)
    assert not any(
        command[:2] == ["systemctl", "unmask"] and "--runtime" not in command
        for command in commands
        if any(target in command for target in llmmode.SLEEP_TARGETS)
    )
    assert not any("--append=amdgpu.runpm=0" in command for command in commands)
    assert any(command[-1] == str(runtime) for command in commands if command[:1] == ["install"])
    assert state["boot_policy"] == "desktop"
    assert state["desktop_on_reboot"] is True
    assert state["llm_autostart"] is False
    assert state["system_mode"] == "llm-session"


def test_text_console_transition_is_protected_from_display_session_exit(monkeypatch):
    class SupportedPlatform:
        def require_current_boot_llm_mode(self):
            return None

    monkeypatch.setattr(
        llmmode, "_platform_service", lambda platform=None: SupportedPlatform()
    )
    monkeypatch.setattr(llmmode, "_require_commands", lambda *commands: None)
    monkeypatch.setattr(llmmode, "elevated", lambda command: command)
    monkeypatch.setattr(llmmode.os, "getpid", lambda: 4242)
    monkeypatch.setattr(
        llmmode.shutil, "which",
        lambda name: {
            "systemctl": "/usr/bin/systemctl",
            "systemd-run": "/usr/sbin/systemd-run",
            "chvt": "/usr/sbin/chvt",
        }.get(name),
    )
    runner = FakeRunner()

    llmmode.activate_text_console(runner)

    command = runner.commands[-1][0]
    assert command[:4] == [
        "/usr/sbin/systemd-run",
        "--unit=bc250-llm-console-transition-4242.service",
        "--collect",
        "--no-block",
    ]
    assert "--property=IgnoreOnIsolate=yes" in command
    assert (
        "--property=ExecStartPost=/usr/bin/systemctl start getty@tty1.service"
        in command
    )
    assert "--property=ExecStartPost=/usr/sbin/chvt 1" in command
    assert command[-3:] == [
        "/usr/bin/systemctl", "isolate", "multi-user.target",
    ]


def test_host_mode_state_is_committed_before_console_transition(monkeypatch):
    from bc250_llm_mode import services

    order = []
    monkeypatch.setattr(
        llmmode, "apply_llm_mode",
        lambda state, runner, **kwargs: (
            order.append("apply"), state.update(system_mode="llm-session")
        )[-1],
    )
    monkeypatch.setattr(
        services, "persist_state_diff",
        lambda units, before, after: order.append("persist"),
    )
    monkeypatch.setattr(
        llmmode, "activate_text_console",
        lambda runner, **kwargs: order.append("console"),
    )
    service = services.HostModeService(object(), platform=object())

    service.enter_llm_mode(
        {"system_mode": "desktop"}, object(), activate_console_now=True,
    )

    assert order == ["apply", "persist", "console"]
