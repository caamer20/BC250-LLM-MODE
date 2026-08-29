"""EXP-2 protocol qualification; physical second-device evidence is separate."""

from __future__ import annotations

import http.server
import json
import threading
import urllib.error
import urllib.request

from bc250_llm_mode.connection_setup import (
    BoundedHTTPProbeTransport,
    ConnectionCredentialCommandService,
    ConnectionProbeService,
    MultiClientAuthenticationStore,
)
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.gateway import (
    build_multi_client_gateway_server,
    make_gateway_socket_server,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


ALIAS = "LiquidAI/LFM2.5-2.6B"
NOW = "2026-08-29T12:00:00Z"


class _BackendHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": ALIAS}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(min(length, 64 * 1024))
        body = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return None


class _RewritingTransport:
    """Maps reviewed local/tailnet fixture URLs to one real gateway socket."""

    def __init__(self, port: int) -> None:
        self._origin = f"http://127.0.0.1:{port}"
        self._inner = BoundedHTTPProbeTransport()

    def request(self, **kwargs):
        url = kwargs["url"]
        suffix = (
            "/v1" + url.split("/v1", 1)[1]
            if "/v1" in url else url[url.find("/health"):])
        return self._inner.request(**{**kwargs, "url": self._origin + suffix})


def _http_status(url: str, token: str | None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_real_socket_two_client_auth_negative_stream_and_independent_revoke(tmp_path):
    app_dir = tmp_path / "profile"
    app_dir.mkdir()
    database = app_dir / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    ids = iter(("1" * 32, "2" * 32))
    secrets = iter((
        "phone-secret-canary-0000000000000000000000",
        "webui-secret-canary-000000000000000000000",
    ))
    credentials = ConnectionCredentialCommandService(
        units, app_dir, clock=lambda: NOW,
        id_provider=lambda: next(ids), secret_provider=lambda: next(secrets))
    phone = credentials.add_client(label="Phone", client_kind="pocketpal")
    webui = credentials.add_client(label="WebUI", client_kind="openwebui")

    backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _BackendHandler)
    backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
    backend_thread.start()
    gateway = build_multi_client_gateway_server(
        authentication_store=MultiClientAuthenticationStore(
            units, clock=lambda: NOW),
        backend_base=f"http://127.0.0.1:{backend.server_address[1]}",
        should_report_backend=lambda: True,
    )
    server = make_gateway_socket_server(gateway, port=0)
    gateway_thread = threading.Thread(target=server.serve_forever, daemon=True)
    gateway_thread.start()
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    try:
        report = ConnectionProbeService(
            units, credentials, transport=_RewritingTransport(port),
            clock=lambda: NOW,
        ).run(
            client_id=phone.client["client_id"], public_alias=ALIAS,
            tailnet_base_url="https://fixture.tailnet.invalid:10000/v1")
        assert report.passed is True
        assert set(report.results.values()) == {"passed"}
        assert _http_status(f"{origin}/v1/models", None) == 401
        assert _http_status(f"{origin}/v1/models", phone.secret) == 200
        assert _http_status(f"{origin}/v1/models", webui.secret) == 200

        current = next(
            item for item in credentials.list_clients()
            if item["client_id"] == phone.client["client_id"])
        credentials.revoke_client(
            phone.client["client_id"], expected_revision=current["revision"])
        assert _http_status(f"{origin}/v1/models", phone.secret) == 401
        assert _http_status(f"{origin}/v1/models", webui.secret) == 200

        with units.read() as conn:
            dumped = "\n".join(conn.iterdump())
        assert phone.secret not in dumped
        assert webui.secret not in dumped
        assert "Reply with one word" not in dumped
        assert "choices" not in dumped
        assert "fixture.tailnet.invalid" not in dumped
    finally:
        server.shutdown()
        server.server_close()
        backend.shutdown()
        backend.server_close()
        gateway_thread.join(timeout=2)
        backend_thread.join(timeout=2)
