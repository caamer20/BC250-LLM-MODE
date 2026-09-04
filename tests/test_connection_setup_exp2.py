from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.connection_setup import (
    CLIENT_CARDS,
    LOCAL_GATEWAY_BASE_URL,
    ConnectionCredentialCommandService,
    ConnectionProbeService,
    ConnectionSetupQueryService,
    MultiClientAuthenticationStore,
    ProbeHTTPResponse,
    build_connection_snapshot,
    endpoint_urls,
    instructions_for,
)
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.gateway import GatewayPolicy, GatewayServer
from bc250_llm_mode.repositories import RepositoryConflict
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


NOW = "2026-08-29T12:00:00Z"
CLIENT_ID = "1" * 32
SECRET = "client-secret-canary-000000000000000000000000"


@pytest.fixture()
def world(tmp_path):
    app_dir = tmp_path / "profile"
    app_dir.mkdir()
    database = app_dir / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    service = ConnectionCredentialCommandService(
        units, app_dir, clock=lambda: NOW,
        id_provider=lambda: CLIENT_ID, secret_provider=lambda: SECRET,
    )
    return units, app_dir, service


def _observations(*, funnel=False, probes=None):
    return build_connection_snapshot(
        generated_at=NOW,
        model={"healthy": True, "model_id": "defiant-fable-q5", "path": "/root/models/x.gguf"},
        gateway={"healthy": True, "enabled": True, "active_clients": 1,
                 "ready_clients": 1},
        openwebui={"installed": True, "running": True, "healthy": True},
        tailscale={"installed": True, "daemon_active": True, "connected": True,
                   "dns_name": "bazzite.tail2168f.ts.net."},
        sharing={"webui_proxy": "http://127.0.0.1:3000",
                 "api_proxy": "http://127.0.0.1:9071",
                 "public_funnel": funnel},
        probes=probes or {
            "local_authorized": "passed", "local_unauthorized": "passed",
            "tailnet_unauthorized": "passed",
            "tailnet_authorized_models": "passed", "tailnet_stream": "passed",
        },
    )


def test_exact_urls_alias_and_no_api_or_filesystem_path():
    snapshot = _observations().to_dict()
    assert snapshot["ready"] is True
    assert snapshot["urls"] == {
        "local_base_url": "http://127.0.0.1:9071/v1",
        "local_models_url": "http://127.0.0.1:9071/v1/models",
        "local_chat_completions_url": "http://127.0.0.1:9071/v1/chat/completions",
        "webui_url": "https://bazzite.tail2168f.ts.net:8443/",
        "base_url": "https://bazzite.tail2168f.ts.net:10000/v1",
        "models_url": "https://bazzite.tail2168f.ts.net:10000/v1/models",
        "chat_completions_url": "https://bazzite.tail2168f.ts.net:10000/v1/chat/completions",
    }
    assert snapshot["model"]["public_alias"] == "defiant-fable-q5"
    blob = json.dumps(snapshot)
    assert '"/api"' not in blob
    assert "/root/models" not in blob


def test_path_like_observed_alias_fails_closed_and_funnel_blocks_ready():
    snapshot = build_connection_snapshot(
        generated_at=NOW,
        model={"healthy": True, "model_id": "/root/models/secret.gguf"},
        gateway={"healthy": True, "enabled": True, "active_clients": 1,
                 "ready_clients": 1},
        openwebui={}, tailscale={}, sharing={}, probes={},
    )
    assert snapshot.ready is False
    assert snapshot.model["public_alias"] is None
    assert snapshot.next_action == "Start and verify the selected model."
    unsafe = _observations(funnel=True)
    assert unsafe.ready is False
    assert next(c for c in unsafe.checks if c["id"] == "funnel-disabled")["passed"] is False


def test_cards_are_local_versioned_and_claim_no_unqualified_hardware_evidence():
    assert {card.card_id for card in CLIENT_CARDS} == {
        "openwebui", "pocketpal", "openai", "curl", "python", "sse"
    }
    assert all(card.support_level in {
        "hardware-tested", "protocol-tested", "example-only"} for card in CLIENT_CARDS)
    assert all(card.support_level != "hardware-tested" for card in CLIENT_CARDS)
    pocketpal = instructions_for(
        "pocketpal", urls=endpoint_urls("bazzite.tail2168f.ts.net"),
        public_alias="defiant-fable-q5")
    assert pocketpal["available"] is True
    assert pocketpal["values"]["Base URL"].endswith(":10000/v1")
    assert pocketpal["values"]["Model"] == "defiant-fable-q5"


def test_atomic_secret_create_default_serialization_and_0600(world):
    units, app_dir, service = world
    result = service.add_client(label="Cameron's phone", client_kind="pocketpal")
    assert result.secret == SECRET
    assert "secret" not in result.to_dict()
    revealed = result.to_dict(reveal_secret=True)
    assert revealed["secret"] == SECRET
    path = app_dir / "connection-secrets" / f"{CLIENT_ID}-1.secret"
    assert path.read_text() == SECRET
    assert path.stat().st_mode & 0o777 == 0o600
    with units.read() as conn:
        dump = "\n".join(conn.iterdump())
    assert SECRET not in dump


def test_failed_create_removes_published_secret(world):
    _units, app_dir, service = world
    service.add_client(label="Phone", client_kind="pocketpal")
    duplicate = ConnectionCredentialCommandService(
        service._units, app_dir, clock=lambda: NOW,
        id_provider=lambda: "2" * 32, secret_provider=lambda: "other-secret-000000000000000000000000",
    )
    from bc250_llm_mode.connection_credentials import ConnectionCredentialError
    with pytest.raises(ConnectionCredentialError):
        duplicate.add_client(label=" phone ", client_kind="pocketpal")
    assert not (app_dir / "connection-secrets" / f"{'2' * 32}-1.secret").exists()


def test_rotate_default_revokes_old_secret_without_disrupting_another_client(tmp_path):
    app_dir = tmp_path / "profile"
    app_dir.mkdir()
    database = app_dir / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    ids = iter(("1" * 32, "2" * 32))
    generated = iter(("secret-one-000000000000000000000000",
                      "secret-two-000000000000000000000000",
                      "secret-one-new-00000000000000000000"))
    service = ConnectionCredentialCommandService(
        units, app_dir, clock=lambda: NOW,
        id_provider=lambda: next(ids), secret_provider=lambda: next(generated))
    first = service.add_client(label="Phone", client_kind="pocketpal")
    second = service.add_client(label="WebUI", client_kind="openwebui")
    rotated = service.rotate_client(
        first.client["client_id"], expected_revision=1, overlap_seconds=0)
    assert rotated.client["active_generation"] == 2
    assert not (app_dir / "connection-secrets" / f"{'1' * 32}-1.secret").exists()
    assert (app_dir / "connection-secrets" / f"{'1' * 32}-2.secret").exists()
    assert service.secret_for_probe(second.client["client_id"]).startswith("secret-two")


def test_multi_client_gateway_uses_named_principal_and_individual_scopes(world):
    units, _app_dir, service = world
    service.add_client(
        label="Models only", client_kind="curl", scopes=("models:list",))
    policy = GatewayPolicy(MultiClientAuthenticationStore(units))
    gateway = GatewayServer(policy, backend_base="http://127.0.0.1:8080",
                            should_report_backend=lambda: True)
    ok = gateway.handle(
        "r1", "untrusted-header", "GET", "/v1/models", 100, b"", SECRET,
        backend_request=lambda: (200, b'{"data":[]}'))
    assert ok[0] == 200
    denied = gateway.handle(
        "r2", "untrusted-header", "POST", "/v1/chat/completions", 100,
        b'{"stream":false}', SECRET, backend_request=lambda: (200, b"{}"))
    assert denied[0] == 403
    events = gateway.audit.snapshot()
    assert events[0].actor == CLIENT_ID
    assert events[1].actor == CLIENT_ID
    with units.read() as conn:
        used = conn.execute(
            "SELECT last_used_at, last_endpoint_class FROM connection_clients "
            "WHERE client_id = ?", (CLIENT_ID,)).fetchone()
    assert used["last_used_at"] is not None
    assert used["last_endpoint_class"] == "models"


class _ProbeTransport:
    def __init__(self):
        self.calls = []

    def request(self, *, method, url, token, body=None, stream=False, timeout=10):
        self.calls.append({"method": method, "url": url, "token": token,
                           "body": body, "stream": stream, "timeout": timeout})
        if token is None:
            return ProbeHTTPResponse(401, {"error": "invalid credential"})
        if stream:
            return ProbeHTTPResponse(200, valid_sse_event=True)
        return ProbeHTTPResponse(200, {"data": [{"id": "defiant-fable-q5"}]})


def test_probe_requires_negative_and_positive_paths_and_persists_no_content(world):
    units, _app_dir, credentials = world
    credentials.add_client(label="Phone", client_kind="pocketpal")
    transport = _ProbeTransport()
    service = ConnectionProbeService(
        units, credentials, transport=transport, clock=lambda: NOW)
    report = service.run(
        client_id=CLIENT_ID, public_alias="defiant-fable-q5",
        tailnet_base_url="https://bazzite.tail2168f.ts.net:10000/v1")
    assert report.passed is True
    assert report.results == {
        "local_unauthorized": "passed", "local_authorized": "passed",
        "tailnet_unauthorized": "passed", "tailnet_authorized_models": "passed",
        "tailnet_stream": "passed",
    }
    assert len(transport.calls) == 5
    assert transport.calls[0]["token"] is None
    assert transport.calls[2]["token"] is None
    assert transport.calls[-1]["body"]["messages"][0]["content"] == "Reply with one word."
    with units.read() as conn:
        row = conn.execute(
            "SELECT payload_json FROM runtime_observations WHERE key = ?",
            (f"connection_probe.{CLIENT_ID}",)).fetchone()
        dumped = "\n".join(conn.iterdump())
    assert json.loads(row["payload_json"])["passed"] is True
    assert SECRET not in dumped
    assert "Reply with one word" not in dumped
    assert "bazzite.tail2168f" not in dumped


def test_unauthorized_success_causes_probe_failure(world):
    units, _app_dir, credentials = world
    credentials.add_client(label="Phone", client_kind="pocketpal")

    class BadNegative(_ProbeTransport):
        def request(self, **kwargs):
            if kwargs["token"] is None:
                return ProbeHTTPResponse(200, {"data": []})
            return super().request(**kwargs)

    report = ConnectionProbeService(
        units, credentials, transport=BadNegative(), clock=lambda: NOW,
    ).run(client_id=CLIENT_ID, public_alias="defiant-fable-q5")
    assert report.passed is False
    assert report.results["local_unauthorized"] == "failed"
    assert report.results["local_authorized"] == "passed"


def test_disable_all_requires_explicit_new_client_and_sharing_enable(world):
    _units, _app_dir, service = world
    service.add_client(label="Phone", client_kind="pocketpal")
    access = service.access_state()
    service.disable_all(expected_revision=access["revision"])
    assert service.readiness()["enabled"] is False
    replacement = ConnectionCredentialCommandService(
        service._units, service._app_dir, clock=lambda: NOW,
        id_provider=lambda: "2" * 32,
        secret_provider=lambda: "replacement-secret-000000000000000000000",
    )
    replacement.add_client(label="New phone", client_kind="pocketpal")
    assert replacement.readiness()["enabled"] is False
    revision = replacement.access_state()["revision"]
    enabled = replacement.enable_for_sharing(expected_revision=revision)
    assert enabled["access"]["enabled"] is True
    assert replacement.readiness()["ready_clients"] == 1


def test_composed_query_snapshot_is_live_bounded_and_read_only(world):
    units, _app_dir, credentials = world
    credentials.add_client(label="Phone", client_kind="pocketpal")

    class Transport:
        def request(self, *, url, **_kwargs):
            if url.endswith(":8080/v1/models"):
                return ProbeHTTPResponse(200, {"data": [{"id": "defiant-fable-q5"}]})
            if url.endswith(":9071/health"):
                return ProbeHTTPResponse(200, {
                    "status": "ready", "backend_identity": "verified"})
            if url == "http://127.0.0.1:3000/":
                return ProbeHTTPResponse(200)
            raise AssertionError(url)

    class Model:
        def status(self, state, runner):
            return {"active": True}

    class WebUI:
        def status(self, state, runner):
            return {"installed": True, "running": True, "digest_verified": True}

    class Tail:
        def status(self, runner):
            return {"installed": True, "daemon_active": True, "connected": True,
                    "dns_name": "bazzite.tail2168f.ts.net."}

    class Sharing:
        def status(self, state, runner):
            assert state["gateway_verified"] is True
            return {"webui_proxy": "http://127.0.0.1:3000",
                    "api_proxy": "http://127.0.0.1:9071",
                    "public_funnel": False,
                    "dns_name": "bazzite.tail2168f.ts.net"}

    before = units.database_path.read_bytes()
    snapshot = ConnectionSetupQueryService(
        units, credentials,
        state_supplier=lambda: {"server_port": 8080}, runner_factory=object,
        model_server=Model(), openwebui=WebUI(), tailscale=Tail(),
        sharing=Sharing(), transport=Transport(), clock=lambda: NOW,
    ).snapshot()
    after = units.database_path.read_bytes()
    assert snapshot.model["public_alias"] == "defiant-fable-q5"
    assert snapshot.gateway["ready_clients"] == 1
    assert snapshot.openwebui["http_ready"] is True
    assert snapshot.openwebui["expected_model_visible"] is False
    assert snapshot.readiness["openwebui_ready"] is False
    assert snapshot.urls["base_url"] == "https://bazzite.tail2168f.ts.net:10000/v1"
    assert snapshot.ready is False  # mandatory auth probes have not run
    assert snapshot.next_action == "Run the authorized local connection test."
    assert before == after


def test_composed_query_accepts_public_alias_for_selected_local_model(world):
    units, _app_dir, credentials = world
    credentials.add_client(label="Phone", client_kind="pocketpal")
    local_id = "local-a3515b591cfc"
    public_alias = "LFM2.5-2.6B-Q5_K_M"

    class Transport:
        def request(self, *, url, **_kwargs):
            if url.endswith(":8080/v1/models"):
                return ProbeHTTPResponse(200, {"data": [{"id": public_alias}]})
            if url.endswith(":9071/health"):
                return ProbeHTTPResponse(200, {
                    "status": "ready", "backend_identity": "verified"})
            if url == "http://127.0.0.1:3000/":
                return ProbeHTTPResponse(200)
            raise AssertionError(url)

    class Active:
        def status(self, state, runner):
            return {"active": True, "installed": True, "running": True,
                    "digest_verified": True}

    class Tail:
        def status(self, runner):
            return {"installed": True, "daemon_active": True, "connected": True,
                    "dns_name": "bazzite.tail2168f.ts.net."}

    class Sharing:
        def status(self, state, runner):
            return {"webui_proxy": "http://127.0.0.1:3000",
                    "api_proxy": "http://127.0.0.1:9071",
                    "public_funnel": False,
                    "dns_name": "bazzite.tail2168f.ts.net"}

    state = {
        "server_port": 8080,
        "current_model": local_id,
        "installed_models": [{"id": local_id, "display_name": public_alias}],
    }
    snapshot = ConnectionSetupQueryService(
        units, credentials, state_supplier=lambda: state, runner_factory=object,
        model_server=Active(), openwebui=Active(), tailscale=Tail(),
        sharing=Sharing(), transport=Transport(), clock=lambda: NOW,
    ).snapshot()

    assert snapshot.model["expected_identity"] == local_id
    assert snapshot.model["observed_identity"] == public_alias
    assert snapshot.model["identity_matches"] is True
