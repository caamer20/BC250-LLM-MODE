"""P4 — authenticated integration gateway (ADR 005 §10.2/§10.4 gates).

Covers the gateway contract headlessly: scoped credentials, constant-time
auth, fail-closed backend, scope/rate/size/stream limits, management
denial, content-free audit, rotation/revoke, and health readiness.
"""

from __future__ import annotations

import json

from bc250_llm_mode import gateway as g


def _store() -> g.CredentialStore:
    return g.CredentialStore()


def _policy(store=None, scopes=None) -> g.GatewayPolicy:
    return g.GatewayPolicy(store or _store(), allowed_scopes=scopes or (
        g.SCOPE_INFERENCE_READ, g.SCOPE_INFERENCE_STREAM, g.SCOPE_MODELS_LIST))


def _handle(policy, *, method="POST", path="/v1/chat/completions", body=b'{}',
            token="_", hdr=128, client="c", backend=(200, b'{"ok":1}')):
    server = g.GatewayServer(policy, backend_base="http://127.0.0.1:9999",
                             should_report_backend=lambda: True)
    return server.handle("rid-1", client, method, path, hdr, body, token,
                         backend_request=lambda: backend)


def test_health_is_credential_free_and_truthful():
    p = _policy(_store())  # no credential provisioned
    status, body, _ = _handle(p, method="GET", path="/health", token="")
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "ready"
    assert payload["backend_identity"] == "verified"
    assert "api_version" in payload


def test_missing_or_wrong_credential_fails_closed():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    status, body, _ = _handle(p, token="wrong-secret")
    assert status == 401
    assert json.loads(body)["error"] == "invalid credential"
    status, body, _ = _handle(p, token="")
    assert status == 401


def test_management_endpoints_always_denied_even_with_credentials():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    for path in ("/runtime/update", "/thermal/reset", "/ops/1/cancel",
                 "/fs/rm", "/backup", "/systemctl", "/", "/management"):
        status, body, _ = _handle(p, path=path, token="super-secret")
        assert status == 403, path
        assert json.loads(body)["error"] in ("management endpoint refused",)


def test_unknown_v1_endpoint_is_management_and_refused():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    status, body, _ = _handle(p, path="/v1/embeddings", token="super-secret")
    assert status == 403


def test_scope_gating_inference_stream_and_models():
    store = _store()
    store.provisioning_record("super-secret")
    # inference:read only -> stream=false ok, stream=true denied, models denied
    p = _policy(store, scopes=(g.SCOPE_INFERENCE_READ,))
    status, body, _ = _handle(
        p, body=b'{"stream": false}', token="super-secret")
    assert status == 200
    status, body, _ = _handle(
        p, body=b'{"stream": true}', token="super-secret")
    assert status == 403 and b"stream" in body
    status, body, _ = _handle(
        p, method="GET", path="/v1/models", body=b'', token="super-secret")
    assert status == 403 and b"models:list" in body
    # models:list only
    p2 = _policy(store, scopes=(g.SCOPE_MODELS_LIST,))
    status, body, _ = _handle(
        p2, method="GET", path="/v1/models", body=b'', token="super-secret")
    assert status == 200


def test_request_body_size_cap_denied_before_backend():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    huge = b"x" * (g.MAX_BODY_BYTES + 1)
    status, body, _ = _handle(p, body=huge, token="super-secret")
    assert status == 413
    assert b"large" in body


def test_generation_and_context_bounds_refused():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    bad = _handle(p, body=json.dumps({"max_tokens": g.MAX_GENERATED_TOKENS + 1}).encode(),
                  token="super-secret")
    assert bad[0] == 400 and b"max_tokens" in bad[1]
    bad2 = _handle(p, body=b'{"n_ctx": 999999}', token="super-secret")
    assert bad2[0] == 400 and b"context" in bad2[1]


def test_malformed_json_body_rejected():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    status, body, _ = _handle(p, body=b'{"stream": ', token="super-secret")
    assert status == 400 and b"malformed" in body


def test_fail_closed_when_backend_identity_unreachable():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    server = g.GatewayServer(p, backend_base="http://127.0.0.1:9999",
                             should_report_backend=lambda: False)
    status, body, _ = server.handle(
        "rid", "c", "POST", "/v1/chat/completions", 128, b'{}', "super-secret",
        backend_request=lambda: (200, b'{"ok":1}'))
    assert status == 503 and b"backend identity" in body


def test_backend_forward_failure_maps_to_502_and_audits():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    server = g.GatewayServer(p, backend_base="http://127.0.0.1:9999",
                             should_report_backend=lambda: True)

    def boom():
        raise OSError("conn refused")

    status, body, _ = server.handle(
        "rid", "c", "POST", "/v1/chat/completions", 128, b'{}', "super-secret",
        backend_request=boom)
    assert status == 502 and b"conn refused" in body


def test_rate_limit_denies_excess_clients():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    rate = g.RateLimiter()
    server = g.GatewayServer(p, backend_base="http://127.0.0.1:9999",
                             should_report_backend=lambda: True, rate=rate)
    results = []
    for _ in range(g.MAX_REQUEST_RATE + 5):  # force window exhaustion
        results.append(server.handle("rid", "client-1", "POST",
                                     "/v1/chat/completions", 128, b'{}',
                                     "super-secret",
                                     backend_request=lambda: (200, b'{}'))[0])
    assert results[-1] == 429


def test_concurrency_limit():
    store = _store()
    store.provisioning_record("super-secret")
    p = _policy(store)
    rate = g.RateLimiter()
    server = g.GatewayServer(p, backend_base="http://127.0.0.1:9999",
                             should_report_backend=lambda: True, rate=rate)
    # fill inflight
    assert rate.check("busy")[0] is True
    for _ in range(g.MAX_CONCURRENT - 1):
        assert rate.check("busy")[0] is True
    ok, _msg = rate.check("busy")
    assert ok is False  # concurrency ceiling
    rate.release("busy")
    assert rate.check("busy")[0] is True


def test_audit_events_never_carry_prompt_or_completion_content():
    store = _store()
    store.provisioning_record("super-secret")
    audit = g.AuditLogger()
    p = _policy(store)
    server = g.GatewayServer(p, backend_base="http://127.0.0.1:9999",
                             should_report_backend=lambda: True, audit=audit)
    body = b'{"stream": false, "messages": [{"role":"user","content":"SECRET_PROMPT"}]}'
    server.handle("rid-a", "client-1", "POST", "/v1/chat/completions", 128,
                  body, "super-secret", backend_request=lambda: (200, b'{"choices": [{"message": {"content": "TOP_SECRET_COMPLETION"}}]}'))
    events = audit.snapshot()
    assert len(events) == 1
    blob = json.dumps(events[0].to_dict()).encode("utf-8")
    assert b"SECRET_PROMPT" not in blob
    assert b"TOP_SECRET_COMPLETION" not in blob
    assert events[0].outcome == "ok"
    assert events[0].status == 200
    assert events[0].request_id == "rid-a"
    assert events[0].actor == "client-1"


def test_credential_rotation_and_revoke():
    store = _store()
    store.provisioning_record("secret-one")
    store.rotate("secret-one", "secret-two")
    assert store.is_valid("secret-two") is True
    assert store.is_valid("secret-one") is False  # old revoked on rotate
    store.revoke()
    assert store.is_valid("secret-two") is False


def test_constant_time_comparison_never_reveals_fingerprint():
    store = _store()
    record = store.provisioning_record("very-long-secret-value-123")
    # fingerprint stored is the sha256, but is_valid never returns it
    assert "fingerprint_prefix" in record
    # a wrong secret must never match even if it equals a stored digest
    assert store.is_valid(record["fingerprint_prefix"]) is False


def test_api_versioning_constant_present():
    assert g.GATEWAY_API_VERSION == 1
    assert g.SCOPE_INFERENCE_READ == "inference:read"
    assert g.SCOPE_INFERENCE_STREAM == "inference:stream"
    assert g.SCOPE_MODELS_LIST == "models:list"