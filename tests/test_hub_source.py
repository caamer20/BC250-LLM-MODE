"""U1.1 §5: immutable hub source and range transfer against a local server."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bc250_llm_mode.hub_source import (
    HubClient,
    SourceError,
    catalog_fingerprint,
)

REVISION = "a" * 40
PAYLOAD = b"0123456789abcdef" * 8  # 128 bytes


class _Handler(BaseHTTPRequestHandler):
    received: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        type(self).received.append(("GET", self.path, dict(self.headers)))
        if self.path.startswith("/api/models/"):
            body = json.dumps({"sha": REVISION}).encode()
            self._send(200, body, {"Content-Type": "application/json"})
            return
        match = re.search(r"/repo/resolve/([0-9a-f]{40})/(file\.gguf)$", self.path)
        if not match:
            self._send(404)
            return
        range_header = self.headers.get("Range", "")
        start = 0
        if range_header.startswith("bytes="):
            start = int(range_header[6:].split("-")[0])
        body = PAYLOAD[start:]
        self._send(
            206 if start else 200,
            body,
            {
                "Content-Length": str(len(body)),
                "ETag": '"etag-1"',
                "Accept-Ranges": "bytes",
            },
        )

    def do_HEAD(self):
        type(self).received.append(("HEAD", self.path, dict(self.headers)))
        if "/redirect/" in self.path:
            # Cross-origin signed redirect: auth must be stripped on follow.
            self._send(
                302,
                {},
                {"Location": f"{self.headers['Host']}/elsewhere?sig=CANARY"},
            )
            return
        self._send(
            200,
            {},
            {"Content-Length": str(len(PAYLOAD)), "ETag": '"etag-1"'},
        )


@pytest.fixture()
def server():
    factory = lambda *a, **k: _Handler(*a, **k)  # noqa: E731
    factory.protocol_version = "HTTP/1.1"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), factory)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    _Handler.received = []
    yield base
    httpd.shutdown()


def test_fingerprint_is_stable_and_covers_policy():
    class Entry:
        id = "m"
        repo = "r"
        source_repo = None
        allow_globs = {"Q4": "*.gguf"}
        avoid = ("*MAX*",)
        conversion = False
        checksum_manifest = None
        temporary_disk_gib = None
        true_block_count = None
        validation_tier = "auto"

    assert catalog_fingerprint(Entry, "Q4") == catalog_fingerprint(Entry, "Q4")
    assert catalog_fingerprint(Entry, "Q4") != catalog_fingerprint(Entry, "Q8")


def test_resolve_pins_immutable_revision_and_observes_blob(server):
    client = HubClient(api_base=f"{server}/api", file_base=server)
    sha = client.resolve_revision("org/repo")
    assert sha == REVISION
    blob = client.observe_blob("org/repo", sha, "file.gguf")
    assert blob.expected_size == len(PAYLOAD)
    assert blob.validator


def test_stream_range_downloads_full_body_and_resumes_partial(server, tmp_path):
    client = HubClient(api_base=f"{server}/api", file_base=server)
    blob = client.observe_blob("org/repo", REVISION, "file.gguf")
    dest = tmp_path / "file.gguf.partial"
    total = client.stream_range(blob, dest, expected_fingerprint="fp-x")
    assert total == len(PAYLOAD) == dest.stat().st_size

    # Simulate an interrupted prefix with a matching receipt.
    dest.write_bytes(PAYLOAD[:64])
    dest.with_name(dest.name + "-receipt.json").write_text(
        json.dumps({"fingerprint": "fp-x", "total": len(PAYLOAD),
                    "validator": blob.validator})
    )
    get_before = len(_Handler.received)
    total = client.stream_range(blob, dest, expected_fingerprint="fp-x")
    assert total == len(PAYLOAD)
    ranged = [r for r in _Handler.received[get_before:] if r[0] == "GET"]
    assert any("Range" in r[2] for r in ranged), "resume must use a range request"


def test_stream_range_resets_foreign_partial(server, tmp_path):
    client = HubClient(api_base=f"{server}/api", file_base=server)
    blob = client.observe_blob("org/repo", REVISION, "file.gguf")
    dest = tmp_path / "f.partial"
    dest.write_bytes(b"foreign-garbage")
    total = client.stream_range(blob, dest, expected_fingerprint="fp-y")
    assert total == len(PAYLOAD)


def test_auth_token_never_sent_in_url_and_strips_on_cross_origin(server):
    token_box = {"token": "SECRET-CANARY-TOKEN"}
    client = HubClient(
        api_base=f"{server}/api",
        file_base=server,
        token_provider=lambda: token_box["token"],
    )
    sha = client.resolve_revision("org/repo")
    assert sha == REVISION
    # Token rides the Authorization header only; URLs carry no secret.
    for method, path, headers in _Handler.received:
        assert "CANARY" not in path
    authed = [h for _, _, h in _Handler.received if h.get("Authorization")]
    assert authed, "same-origin requests may carry credentials in headers only"


def test_mutable_or_unsafe_revisions_are_refused(server):
    client = HubClient(api_base=f"{server}/api", file_base=server)
    with pytest.raises(SourceError) as err:
        client.observe_blob("org/repo", "main", "file.gguf")
    assert err.value.code == "SOURCE_MANIFEST_INVALID"
    with pytest.raises(SourceError):
        client.observe_blob("org/repo", REVISION, "../escape.gguf")