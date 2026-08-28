from bc250_llm_mode import desktop


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        self.commands.append((command, kwargs))

    def emit(self, message):
        self.messages.append(message)


def test_desktop_mode_preserves_setup_and_stops_services(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "elevated", lambda command: command)
    monkeypatch.setattr(desktop.shutil, "which", lambda command: "/usr/bin/podman")
    monkeypatch.setattr(
        desktop,
        "revert_optimizations",
        lambda state, runner: calls.append("optimizations") or state,
    )
    monkeypatch.setattr(
        desktop,
        "revert_llm_mode",
        lambda state, runner, **_kwargs: calls.append("llm") or state.update(reboot_required=True) or state,
    )
    state = {
        "setup_complete": True,
        "service_name": "bc250-llm.service",
        "container_name": "llm",
        "openwebui_container": "bc250-open-webui",
    }
    runner = FakeRunner()

    result = desktop.switch_to_desktop_mode(state, runner)

    commands = [command for command, _kwargs in runner.commands]
    assert commands[0] == ["systemctl", "disable", "--now", "bc250-llm.service"]
    assert commands[1] == ["systemctl", "reset-failed", "bc250-llm.service"]
    assert ["podman", "stop", "--ignore", "--time", "10", "bc250-open-webui"] in commands
    assert ["podman", "stop", "--ignore", "--time", "10", "llm"] in commands
    assert calls == ["optimizations", "llm"]
    assert result["setup_complete"] is True
    assert result["system_mode"] == "desktop"


def test_desktop_mode_now_isolates_graphical_target(monkeypatch):
    monkeypatch.setattr(desktop, "elevated", lambda command: command)
    monkeypatch.setattr(desktop.shutil, "which", lambda command: None)
    monkeypatch.setattr(desktop, "revert_optimizations", lambda state, runner: state)
    monkeypatch.setattr(desktop, "revert_llm_mode", lambda state, runner, **_kwargs: state)
    runner = FakeRunner()

    desktop.switch_to_desktop_mode({}, runner, activate_now=True)

    commands = [command for command, _kwargs in runner.commands]
    assert ["systemctl", "isolate", "graphical.target"] in commands
