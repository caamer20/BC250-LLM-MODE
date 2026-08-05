import json
import subprocess

from bc250_llm_mode import sharing


class FakeRunner:
    def __init__(self, serve_config=None):
        self.serve_config = serve_config or {}
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        command = list(command)
        self.commands.append((command, kwargs))
        output = json.dumps(self.serve_config) if command[-2:] == ["status", "--json"] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    def emit(self, message):
        self.messages.append(message)


def configured(public=False):
    return {
        "Web": {
            "bc250.example.ts.net:8443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}
            },
            "bc250.example.ts.net:10000": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}
            },
        },
        "AllowFunnel": {"bc250.example.ts.net:8443": public},
    }


def patch_tailnet(monkeypatch):
    monkeypatch.setattr(sharing.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(
        sharing,
        "tailscale_status",
        lambda _runner: {
            "installed": True,
            "daemon_active": True,
            "connected": True,
            "dns_name": "bc250.example.ts.net.",
        },
    )
    monkeypatch.setattr(sharing, "elevated", lambda command: command)


def test_https_sharing_status_reports_both_private_endpoints(monkeypatch):
    patch_tailnet(monkeypatch)
    status = sharing.https_sharing_status({}, FakeRunner(configured()))
    assert status["enabled"] is True
    assert status["public_funnel"] is False
    assert status["webui_url"] == "https://bc250.example.ts.net:8443/"
    assert status["api_base_url"] == "https://bc250.example.ts.net:10000/v1"


def test_public_funnel_is_never_considered_safe(monkeypatch):
    patch_tailnet(monkeypatch)
    status = sharing.https_sharing_status({}, FakeRunner(configured(public=True)))
    assert status["enabled"] is False
    assert status["public_funnel"] is True


def test_start_sharing_starts_backends_and_replaces_funnel(monkeypatch):
    patch_tailnet(monkeypatch)
    backends = []
    monkeypatch.setattr(sharing, "start_service", lambda *_args: backends.append("llm"))
    monkeypatch.setattr(sharing, "start_open_webui", lambda *_args: backends.append("webui"))
    runner = FakeRunner(configured())
    state = {}
    status = sharing.start_https_sharing(state, runner)
    commands = [command for command, _kwargs in runner.commands]
    assert backends == ["llm", "webui"]
    assert ["/usr/bin/tailscale", "funnel", "--https=8443", "off"] in commands
    assert [
        "/usr/bin/tailscale", "serve", "--bg", "--https=10000", "http://127.0.0.1:8080"
    ] in commands
    assert status["enabled"] is True
    assert state["https_sharing_enabled"] is True


def test_stop_sharing_removes_only_managed_ports(monkeypatch):
    patch_tailnet(monkeypatch)
    runner = FakeRunner()
    state = {"https_sharing_enabled": True}
    sharing.stop_https_sharing(state, runner)
    commands = [command for command, _kwargs in runner.commands]
    assert ["/usr/bin/tailscale", "serve", "--https=8443", "off"] in commands
    assert ["/usr/bin/tailscale", "serve", "--https=10000", "off"] in commands
    assert state["https_sharing_enabled"] is False
