from __future__ import annotations

import json
import subprocess
import threading

import pytest

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.gateway_runtime import (
    BACKEND_CACHE_SECONDS,
    BackendIdentityProbe,
    GatewayRuntimeError,
    discover_bridge,
    parse_bridge_inspect,
    run_gateway_runtime,
)
from bc250_llm_mode.repositories import SettingsRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


def _network(**changes):
    value = {
        "name": "bc250-openwebui",
        "driver": "bridge",
        "subnets": [{"subnet": "10.89.0.0/24", "gateway": "10.89.0.1"}],
    }
    value.update(changes)
    return json.dumps([value])


def test_bridge_parser_accepts_one_managed_rfc1918_gateway():
    observed = parse_bridge_inspect(_network())

    assert observed.gateway == "10.89.0.1"
    assert observed.subnet == "10.89.0.0/24"
    assert observed.identity.startswith("sha256:")
    assert "10.89.0.1" not in observed.identity


@pytest.mark.parametrize(
    "document",
    (
        [],
        [{"name": "podman", "driver": "bridge", "subnets": []}],
        [{"name": "bc250-openwebui", "driver": "host", "subnets": []}],
        [{
            "name": "bc250-openwebui", "driver": "bridge",
            "subnets": [
                {"subnet": "10.89.0.0/24", "gateway": "10.89.0.1"},
                {"subnet": "10.90.0.0/24", "gateway": "10.90.0.1"},
            ],
        }],
        [{
            "name": "bc250-openwebui", "driver": "bridge",
            "subnets": [{"subnet": "0.0.0.0/0", "gateway": "8.8.8.8"}],
        }],
        [{
            "name": "bc250-openwebui", "driver": "bridge",
            "subnets": [{"subnet": "100.64.0.0/10", "gateway": "100.115.57.31"}],
        }],
    ),
)
def test_bridge_parser_refuses_absent_host_public_tailnet_and_ambiguous(document):
    with pytest.raises(GatewayRuntimeError):
        parse_bridge_inspect(json.dumps(document))


def test_bridge_discovery_uses_fixed_bounded_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "bc250_llm_mode.gateway_runtime.shutil.which",
        lambda name: "/usr/bin/podman" if name == "podman" else None,
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, _network().encode(), b"")

    observed = discover_bridge(run=run)

    assert observed.gateway == "10.89.0.1"
    assert calls[0][0] == [
        "/usr/bin/podman", "network", "inspect", "bc250-openwebui"
    ]
    assert calls[0][1]["timeout"] == 5.0
    assert "shell" not in calls[0][1]


def test_backend_identity_probe_requires_desired_live_model_and_caches(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    with units.begin() as conn:
        SettingsRepository(conn).set_many({
            "current_model": "qwen38-9b", "server_port": 8080,
        })
    now = [100.0]
    calls = []

    def json_get(url, **_kwargs):
        calls.append(url)
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": [{"id": "qwen38-9b"}]}

    probe = BackendIdentityProbe(
        units, port=8080, clock=lambda: now[0], json_get=json_get)

    assert probe.ready() is True
    assert probe.ready() is True
    assert len(calls) == 2
    now[0] += BACKEND_CACHE_SECONDS + 0.01
    assert probe.ready() is True
    assert len(calls) == 4


def test_backend_identity_probe_fails_closed_for_wrong_model_and_port(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    with units.begin() as conn:
        SettingsRepository(conn).set_many({
            "current_model": "expected", "server_port": 8080,
        })

    def wrong_model(url, **_kwargs):
        return {"status": "ok"} if url.endswith("/health") else {
            "data": [{"id": "different"}]}

    assert BackendIdentityProbe(
        units, port=8080, json_get=wrong_model).ready() is False
    assert BackendIdentityProbe(
        units, port=8081, json_get=wrong_model).ready() is False


def test_runtime_owns_exactly_loopback_and_observed_bridge_listeners(tmp_path):
    app_dir = tmp_path / "profile"
    app_dir.mkdir()
    database = app_dir / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    with units.begin() as conn:
        SettingsRepository(conn).set_many({
            "current_model": "qwen38-9b", "server_port": 8080,
        })
    observed = parse_bridge_inspect(_network())
    created = []

    class Server:
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.stopped = threading.Event()

        def serve_forever(self, **_kwargs):
            self.stopped.wait(timeout=2)

        def shutdown(self):
            self.stopped.set()

        def server_close(self):
            return None

    def factory(_gateway, *, host, port):
        created.append((host, port))
        return Server(host, port)

    stop = threading.Event()
    stop.set()
    assert run_gateway_runtime(
        app_dir=app_dir,
        expected_network_identity=observed.identity,
        bridge_observer=lambda: observed,
        stop_event=stop,
        server_factory=factory,
    ) == 0
    assert created == [("127.0.0.1", 9071), ("10.89.0.1", 9071)]


def test_runtime_refuses_changed_bridge_before_binding(tmp_path):
    app_dir = tmp_path / "profile"
    app_dir.mkdir()
    initialize_and_close(app_dir / "state.db")
    observed = parse_bridge_inspect(_network())
    bound = []

    with pytest.raises(GatewayRuntimeError, match="identity changed"):
        run_gateway_runtime(
            app_dir=app_dir,
            expected_network_identity="sha256:" + "0" * 64,
            bridge_observer=lambda: observed,
            server_factory=lambda *_args, **_kwargs: bound.append(True),
        )
    assert bound == []
