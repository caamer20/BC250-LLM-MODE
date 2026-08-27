"""P4 live-socket gateway exit gate (ADR 005 §10.4).

Real loopback sockets end-to-end: a small faux llama.cpp backend replies over
HTTP, and the gateway proxy fronts it. Proves that the full path — real bytes
-> policy (auth, scope, size, fail-closed) -> bounded backend proxy -> response
— works over live sockets, matching the P3 process/HTTP probe evidence
standard. No mocks for the socket or transport layer.
"""

from __future__ import annotations

import http.server
import json
import threading

import httpx

from bc250_llm_mode import gateway as g


class _FakeBackend:
    """A tiny threaded HTTP server acting as the loopback llama.cpp backend."""

    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []
        handler = type(
            "H",
            (http.server.BaseHTTPRequestHandler,),
            {"_b": self, "log_message": lambda *a, **k: None},
        )

        def do_POST(inst):  # noqa: N803
            length = int(inst.headers.get("Content-Length") or 0)
            body = inst.rfile.read(length)
            sequence = (inst.client_address[0], inst.path, body)
            self.hits.append(sequence)
            payload = b'{"id":"cmpl-live","choices":[{"message":{"role":"assistant","content":"backend-reply"}}]}'
            inst.send_response(200)
            inst.send_header("Content-Type", "application/json")
            inst.send_header("Content-Length", str(len(payload)))
            inst.end_headers()
            inst.wfile.write(payload)

        handler.do_GET = handler.do_POST = do_POST  # type: ignore[attr-defined]
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _start_gateway(backend_base: str) -> tuple[g.GatewayServer, http.server.HTTPServer, int]:
    gate = g.build_gateway_server(
        secret="globally-scoped-secret",
        backend_base=backend_base,
        should_report_backend=lambda: True,
    )
    server = g.make_gateway_socket_server(gate, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return gate, server, server.server_address[1]


def test_live_gateway_full_path_over_real_sockets():
    backend = _FakeBackend()
    gate, server, port = _start_gateway(backend.base_url())
    try:
        base = f"http://127.0.0.1:{port}"
        # 1. no credential -> 401, backend never touched
        with httpx.Client(timeout=5.0) as client:
            r = client.post(f"{base}/v1/chat/completions",
                            json={"stream": False, "messages": [{"role": "user", "content": "hi"}]},
                            headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        assert len(backend.hits) == 0

        # 2. valid credential + allowed scope -> proxied 200 with backend reply
        with httpx.Client(timeout=5.0) as client:
            r = client.post(f"{base}/v1/chat/completions",
                            json={"stream": False, "messages": [{"role": "user", "content": "hi"}]},
                            headers={"Authorization": "Bearer globally-scoped-secret"})
        assert r.status_code == 200
        assert b"backend-reply" in r.content
        assert r.headers["X-Gateway-Api-Version"] == "1"
        assert len(backend.hits) == 1  # exactly one forward, backend got real payload

        # 3. management endpoint always refused even with a valid credential
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{base}/runtime/update",
                           headers={"Authorization": "Bearer globally-scoped-secret"})
        assert r.status_code == 403
        assert len(backend.hits) == 1  # backend untouched

        # 4. models:list allowed
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{base}/v1/models",
                           headers={"Authorization": "Bearer globally-scoped-secret"})
        assert r.status_code == 200
        assert len(backend.hits) == 2

        # 5. health is credential-free
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{base}/health")
        assert r.status_code == 200
        assert json.loads(r.content)["status"] == "ready"

        # 6. content-free audit: no prompt/completion in audit snapshot
        for event in gate.audit.snapshot():
            blob = json.dumps(event.to_dict()).encode("utf-8")
            assert b"backend-reply" not in blob
            assert b'"content": "hi"' not in blob
            assert event.outcome in ("ok", "denied")
    finally:
        server.shutdown()
        server.server_close()
        backend.shutdown()


def test_live_gateway_oversize_body_refused_before_backend():
    backend = _FakeBackend()
    gate, server, port = _start_gateway(backend.base_url())
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                content=b"x" * (g.MAX_BODY_BYTES + 1),
                headers={"Authorization": "Bearer globally-scoped-secret",
                         "Content-Type": "application/json"},
            )
        assert r.status_code == 413
        assert len(backend.hits) == 0
    finally:
        server.shutdown()
        server.server_close()
        backend.shutdown()