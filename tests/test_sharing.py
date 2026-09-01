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


def configured(public=False, api_target=None):
    api_target = api_target or sharing.API_TARGET
    return {
        "Web": {
            "bc250.example.ts.net:8443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}
            },
            "bc250.example.ts.net:10000": {
                "Handlers": {"/": {"Proxy": api_target}}
            },
        },
        "AllowFunnel": {"bc250.example.ts.net:8443": public},
    }


def verified_state(**overrides):
    state = {
        "gateway_provisioned": True,
        "gateway_verified": True,
        "gateway_last_verified_at": "2026-05-01T00:00:00Z",
        "gateway_backend_identity": "verified",
    }
    state.update(overrides)
    return state


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
    status = sharing.https_sharing_status(verified_state(), FakeRunner(configured()))
    assert status["enabled"] is True
    assert status["public_funnel"] is False
    assert status["api_enabled"] is True
    assert status["topology"] == "gateway-only"
    assert status["gateway_state"] == "verified"
    assert status["auth_state"] == "scoped-credential"
    assert status["last_verified_at"] == "2026-05-01T00:00:00Z"
    # the published API target is the gateway, never the raw backend
    assert "/127.0.0.1:8080" not in json.dumps(status)
    assert status["webui_url"] == "https://bc250.example.ts.net:8443/"
    assert status["api_base_url"] == "https://bc250.example.ts.net:10000/v1"


def test_api_is_not_safe_when_gateway_unverified(monkeypatch):
    patch_tailnet(monkeypatch)
    status = sharing.https_sharing_status({}, FakeRunner(configured()))
    assert status["enabled"] is False
    assert status["api_enabled"] is False  # target set but gateway not verified
    assert status["gateway_state"] == "pending"
    assert status["topology"] == "none"
    assert status["status"] == "not fully configured"


def test_public_funnel_is_never_considered_safe(monkeypatch):
    patch_tailnet(monkeypatch)
    status = sharing.https_sharing_status(verified_state(), FakeRunner(configured(public=True)))
    assert status["enabled"] is False
    assert status["public_funnel"] is True


def test_start_sharing_starts_backends_and_replaces_funnel(monkeypatch):
    patch_tailnet(monkeypatch)
    backends = []
    monkeypatch.setattr(sharing, "start_service", lambda *_args: backends.append("llm"))
    monkeypatch.setattr(sharing, "start_open_webui", lambda *_args: backends.append("webui"))
    runner = FakeRunner(configured())
    state = verified_state()
    status = sharing.start_https_sharing(state, runner)
    commands = [command for command, _kwargs in runner.commands]
    assert backends == ["llm", "webui"]
    assert ["/usr/bin/tailscale", "funnel", "--https=8443", "off"] in commands
    assert [
        "/usr/bin/tailscale", "serve", "--bg", "--https=10000", sharing.API_TARGET
    ] in commands
    assert status["enabled"] is True
    assert state["https_sharing_enabled"] is True
    # the model API is not published on the raw backend
    assert not any("127.0.0.1:8080" in " ".join(c) for c in commands)


def test_composed_start_orders_model_gateway_then_webui(monkeypatch):
    patch_tailnet(monkeypatch)
    order = []
    monkeypatch.setattr(
        sharing, "start_service", lambda *_args: order.append("model"))
    runner = FakeRunner(configured())

    sharing.start_https_sharing(
        verified_state(), runner,
        ensure_gateway=lambda: order.append("gateway"),
        start_webui=lambda: order.append("openwebui"),
    )

    assert order == ["model", "gateway", "openwebui"]


def test_start_sharing_refuses_when_gateway_unverified(monkeypatch):
    patch_tailnet(monkeypatch)
    runner = FakeRunner(configured())
    state = {"gateway_provisioned": True, "gateway_verified": False}
    import pytest

    with pytest.raises(RuntimeError, match="gateway"):
        sharing.start_https_sharing(state, runner)
    # no tailscale mutation, no backend start
    assert runner.commands == []


def test_stop_sharing_removes_only_managed_ports(monkeypatch):
    patch_tailnet(monkeypatch)
    runner = FakeRunner()
    state = {"https_sharing_enabled": True}
    sharing.stop_https_sharing(state, runner)
    commands = [command for command, _kwargs in runner.commands]
    assert ["/usr/bin/tailscale", "serve", "--https=8443", "off"] in commands
    assert ["/usr/bin/tailscale", "serve", "--https=10000", "off"] in commands
    assert state["https_sharing_enabled"] is False
