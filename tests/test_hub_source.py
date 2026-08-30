"""U1.1 §5: immutable hub source and range transfer (mock transport)."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from bc250_llm_mode.hub_source import (
    HubClient,
    SourceError,
    catalog_fingerprint,
)

REVISION = "a" * 40
PAYLOAD = b"0123456789abcdef" * 8  # 128 bytes


class FakeHubState:
    def __init__(self) -> None:
        self.received: list[tuple[str, str, dict]] = []


STATE = FakeHubState()


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    headers = dict(request.headers)
    STATE.received.append((request.method, str(request.url), headers))
    host = request.url.host
    if path.startswith("/api/models/"):
        return httpx.Response(
            200,
            json={"sha": REVISION},
            headers={"Content-Type": "application/json"},
        )
    match = re.search(r"/repo/resolve/([0-9a-f]{40})/(file\.gguf)$", path)
    if match:
        range_header = headers.get("range", "")
        start = 0
        status = 200
        if range_header.startswith("bytes="):
            start = int(range_header[6:].split("-")[0])
            status = 206
        body = PAYLOAD[start:]
        return httpx.Response(
            status,
            content=body,
            headers={
                "Content-Length": str(len(body)),
                "ETag": '"etag-1"',
                "Accept-Ranges": "bytes",
            },
        )
    return httpx.Response(404)


def make_client(token: str | None = None) -> HubClient:
    transport = httpx.MockTransport(_handler)
    return HubClient(
        api_base="https://hub.test/api",
        file_base="https://hub.test",
        token_provider=(lambda: token) if token else None,
        transport=transport,
    )


def make_redirect_client(redirect_host: str) -> HubClient:
    """HEAD replies with a cross-origin signed redirect."""

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        STATE.received.append(
            (request.method, str(request.url), dict(request.headers))
        )
        if request.url.path.startswith("/api/models/"):
            return httpx.Response(200, json={"sha": REVISION})
        return httpx.Response(
            302,
            headers={
                "Location": f"https://{redirect_host}/elsewhere?sig=CANARY"
            },
        )

    return HubClient(
        api_base="https://hub.test/api",
        file_base="https://hub.test",
        token_provider=lambda: "SECRET-CANARY-TOKEN",
        transport=httpx.MockTransport(redirect_handler),
    )


def make_cdn_redirect_client() -> HubClient:
    """Model files redirect to a signed, cross-origin CDN URL."""

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        headers = dict(request.headers)
        STATE.received.append((request.method, str(request.url), headers))
        if request.url.host == "hub.test":
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        "https://cdn.test/blob.gguf"
                        "?sig=signed-redirect-CANARY"
                    )
                },
            )
        if request.url.host == "cdn.test":
            range_header = headers.get("range", "")
            start = 0
            status = 200
            if request.method == "GET" and range_header.startswith("bytes="):
                start = int(range_header[6:].split("-")[0])
                status = 206
            body = b"" if request.method == "HEAD" else PAYLOAD[start:]
            return httpx.Response(
                status,
                content=body,
                headers={
                    "Content-Length": str(len(PAYLOAD) - start),
                    "ETag": '"cdn-etag-1"',
                    "Accept-Ranges": "bytes",
                },
            )
        return httpx.Response(404)

    return HubClient(
        api_base="https://hub.test/api",
        file_base="https://hub.test",
        token_provider=lambda: "SECRET-CANARY-TOKEN",
        transport=httpx.MockTransport(redirect_handler),
    )


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


def test_resolve_pins_immutable_revision_and_observes_blob():
    client = make_client()
    sha = client.resolve_revision("org/repo")
    assert sha == REVISION
    blob = client.observe_blob("org/repo", sha, "file.gguf")
    assert blob.expected_size == len(PAYLOAD)
    assert blob.validator


def test_resolve_revision_follows_bounded_same_origin_repo_rename():
    received: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append((request.method, str(request.url), dict(request.headers)))
        if request.url.path == "/api/models/org/old-repo":
            return httpx.Response(
                307,
                headers={"Location": "/api/models/org/canonical-repo"},
            )
        if request.url.path == "/api/models/org/canonical-repo":
            return httpx.Response(200, json={"sha": REVISION})
        return httpx.Response(404)

    client = HubClient(
        api_base="https://hub.test/api",
        file_base="https://hub.test",
        token_provider=lambda: "SECRET-CANARY-TOKEN",
        transport=httpx.MockTransport(handler),
    )

    assert client.resolve_revision("org/old-repo") == REVISION
    assert len(received) == 2
    assert all(item[2].get("authorization") for item in received)


@pytest.mark.parametrize(
    "location",
    (
        "http://cdn.test/blob.gguf",
        "https://user:password@cdn.test/blob.gguf",
    ),
)
def test_resolve_revision_refuses_unsafe_redirect_targets(location):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": location})

    client = HubClient(
        api_base="https://hub.test/api",
        file_base="https://hub.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SourceError) as err:
        client.resolve_revision("org/repo")
    assert err.value.code == "SOURCE_REDIRECT_REFUSED"


def test_stream_range_downloads_full_body_and_resumes_partial(tmp_path):
    client = make_client()
    blob = client.observe_blob("org/repo", REVISION, "file.gguf")
    dest = tmp_path / "file.gguf.partial"
    total = client.stream_range(blob, dest, expected_fingerprint="fp-x")
    assert total == len(PAYLOAD) == dest.stat().st_size

    # Simulate an interrupted prefix with a matching receipt.
    dest.write_bytes(PAYLOAD[:64])
    dest.with_name(dest.name + "-receipt.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-x",
                "total": len(PAYLOAD),
                "validator": blob.validator,
            }
        )
    )
    get_before = len(STATE.received)
    total = client.stream_range(blob, dest, expected_fingerprint="fp-x")
    assert total == len(PAYLOAD)
    ranged = [r for r in STATE.received[get_before:] if r[0] == "GET"]
    assert any("range" in r[2] for r in ranged), "resume must use a range"


def test_stream_range_resets_foreign_partial(tmp_path):
    client = make_client()
    blob = client.observe_blob("org/repo", REVISION, "file.gguf")
    dest = tmp_path / "f.partial"
    dest.write_bytes(b"foreign-garbage")
    total = client.stream_range(blob, dest, expected_fingerprint="fp-y")
    assert total == len(PAYLOAD)


def test_stream_range_follows_cdn_redirect_and_preserves_safe_resume(tmp_path):
    STATE.received.clear()
    client = make_cdn_redirect_client()
    blob = client.observe_blob("org/repo", REVISION, "file.gguf")
    dest = tmp_path / "redirected.gguf.partial"

    assert client.stream_range(blob, dest, expected_fingerprint="fp-cdn") == len(
        PAYLOAD
    )
    assert dest.read_bytes() == PAYLOAD

    origin_requests = [item for item in STATE.received if "hub.test" in item[1]]
    cdn_requests = [item for item in STATE.received if "cdn.test" in item[1]]
    assert any(item[2].get("authorization") for item in origin_requests)
    assert all(not item[2].get("authorization") for item in cdn_requests)
    assert all("SECRET-CANARY-TOKEN" not in item[1] for item in STATE.received)

    dest.write_bytes(PAYLOAD[:64])
    dest.with_name(dest.name + "-receipt.json").write_text(
        json.dumps(
            {
                "fingerprint": "fp-cdn",
                "total": len(PAYLOAD),
                "validator": blob.validator,
            }
        )
    )
    STATE.received.clear()

    assert client.stream_range(blob, dest, expected_fingerprint="fp-cdn") == len(
        PAYLOAD
    )
    resumed_cdn_gets = [
        item
        for item in STATE.received
        if item[0] == "GET" and "cdn.test" in item[1]
    ]
    assert resumed_cdn_gets
    assert resumed_cdn_gets[-1][2]["range"] == "bytes=64-"
    assert resumed_cdn_gets[-1][2]["if-range"] == blob.validator
    assert not resumed_cdn_gets[-1][2].get("authorization")
    assert dest.read_bytes() == PAYLOAD


def test_auth_token_never_leaks_into_urls():
    STATE.received.clear()
    client = make_client(token="SECRET-CANARY-TOKEN")
    sha = client.resolve_revision("org/repo")
    assert sha == REVISION
    for method, url, headers in STATE.received:
        assert "CANARY" not in url
    authed = [h for _, _, h in STATE.received if h.get("authorization")]
    assert authed, "same-origin requests carry credentials in headers only"
    # Cross-origin redirects strip Authorization before following.
    redirector = make_redirect_client("evil.test")
    with pytest.raises(SourceError):
        redirector.observe_blob("org/repo", REVISION, "file.gguf")


def test_mutable_or_unsafe_revisions_are_refused():
    client = make_client()
    with pytest.raises(SourceError) as err:
        client.observe_blob("org/repo", "main", "file.gguf")
    assert err.value.code == "SOURCE_MANIFEST_INVALID"
    with pytest.raises(SourceError):
        client.observe_blob("org/repo", REVISION, "../escape.gguf")
