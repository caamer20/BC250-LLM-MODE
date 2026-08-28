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
    assert not any("--append=amdgpu.runpm=0" in command for command in commands)
    assert any(command[-1] == str(runtime) for command in commands if command[:1] == ["install"])
    assert state["boot_policy"] == "desktop"
    assert state["desktop_on_reboot"] is True
    assert state["llm_autostart"] is False
    assert state["system_mode"] == "llm-session"
