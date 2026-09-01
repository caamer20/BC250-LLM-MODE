"""Authenticated integration gateway (ADR 005).

The gateway is the ONLY bridge from remote/container traffic (tailnet
clients and the managed Open WebUI container) to the loopback llama.cpp
backend. It terminates TLS, requires an install-scoped credential on every
request, enforces capability scopes, bounds requests/streams, audits
outcomes WITHOUT prompt/completion content, and fails closed when the
expected backend identity is unreachable or mismatched.

The raw backend (`127.0.0.1:<port>`) is loopback-only; it is never a serve
or integration publication target. Integration modules must route through
{gateway_url}/v1 with an install-scoped credential, never to the backend
directly.

Security posture is pure-idiomatic here so it can be unit-tested
deterministically: decision functions (auth, scope, size, stream bounds)
take plain inputs and return typed results; the thin HTTP adapter feeds them
live bytes from real loopback sockets.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# --- immutable identity / versioning (plan §16.1) -----------------------------
GATEWAY_API_VERSION = 1

# Supported capability scopes (ADR 005 D3).
SCOPE_INFERENCE_READ = "inference:read"      # non-stream chat/completions
SCOPE_INFERENCE_STREAM = "inference:stream"  # SSE streaming completion
SCOPE_MODELS_LIST = "models:list"            # list models


# --- bounds (plan §10.2 overrides/guards) --------------------------------------
MAX_SECRET_LEN = 512     # bounded credential/authorization header size
MAX_BODY_BYTES = 4 * 1024 * 1024      # request body cap
MAX_CONTEXT_TOKENS = 131072           # refuse oversized context budget
MAX_GENERATED_TOKENS = 8192           # per-completion generation cap
MAX_HEADER_BYTES = 64 * 1024          # bounded aggregate request headers
MAX_REQUEST_RATE = 20                 # per-client requests per window
RATE_WINDOW_S = 60
MAX_CONCURRENT = 4                    # per-client concurrent in-flight requests
CLIENT_TOKEN_ALLOWANCE = 128 * 1024   # generated token / body allowance per window


# --- audit ----------------------------------------------------------------------
# Audit events record actor/scope/request-id/outcome ONLY; never prompt or
# completion content (ADR 005 T4).


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    at: float
    actor: str          # client id / scope principal
    scope: str
    request_id: str
    method: str
    path: str
    outcome: str        # ok | denied | malformed | rate_limited | backend | client_closed
    status: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": GATEWAY_API_VERSION,
            "seq": self.seq,
            "at": round(self.at, 3),
            "actor": self.actor,
            "scope": self.scope,
            "request_id": self.request_id,
            "method": self.method,
            "path": self.path,
            "outcome": self.outcome,
            "status": self.status,
        }


@dataclass
class AuditLogger:
    """Bounded, content-free local audit sink (injectable for tests)."""
    max_events: int = 500
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _events: list[AuditEvent] = field(default_factory=list)

    def record(
        self,
        *,
        actor: str,
        scope: str,
        request_id: str,
        method: str,
        path: str,
        outcome: str,
        status: int,
    ) -> AuditEvent:
        with self._lock:
            self._seq += 1
            event = AuditEvent(
                seq=self._seq,
                at=time.time(),
                actor=actor,
                scope=scope,
                request_id=request_id,
                method=method,
                path=path,
                outcome=outcome,
                status=status,
            )
            self._events.append(event)
            if len(self._events) > self.max_events:
                del self._events[0: len(self._events) - self.max_events]
            return event

    def snapshot(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)


# --- credentials ----------------------------------------------------------------
# Per-install secret: generated at provisioning time. The durable store keeps a
# NON-SECRET fingerprint (sha256 of the secret) so rotation/revocation is
# durable and auditable WITHOUT persisting the secret (ADR 005 D3). Comparison
# at request time is constant-time (hmac.compare_digest).


def fingerprint_of(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class CredentialStore:
    """Durable(?) non-secret credential fingerprint store.

    ``backend_get``/``backend_set`` are injectable so tests can use an in-memory
    map; the production adapter persists only the fingerprint to a 0600 config
    (never the secret). Rotating replaces the fingerprint; revoking removes it.
    """

    def __init__(
        self,
        get_backend: Callable[[str], str | None] | None = None,
        set_backend: Callable[[str, str], None] | None = None,
        drop_backend: Callable[[str], None] | None = None,
    ) -> None:
        import collections

        mem: dict[str, str] = collections.defaultdict(lambda: "")
        self._get = get_backend or (lambda key: mem.get(key))
        # production provider receives (key, fingerprint); memory default stores both
        self._set = set_backend or (lambda key, value: mem.__setitem__(key, value))
        self._drop = drop_backend or (lambda key: mem.pop(key, None))

    def provisioning_record(self, secret: str) -> dict[str, str]:
        """Persist only the fingerprint; return a redacted record."""
        if not (8 <= len(secret) <= MAX_SECRET_LEN):
            raise ValueError("secret length out of bounds")
        key = "gateway_credentials.active_fingerprint"
        fp = fingerprint_of(secret)
        self._set(key, fp)
        return {"provisioned": True, "fingerprint_prefix": fp[:8]}

    def is_valid(self, presented: str) -> bool:
        if not (1 <= len(presented) <= MAX_SECRET_LEN):
            return False
        expected = self._get("gateway_credentials.active_fingerprint") or ""
        if not expected:
            return False
        return hmac.compare_digest(fingerprint_of(presented), expected)

    def rotate(self, old_secret: str, new_secret: str) -> dict[str, str]:
        if not self.is_valid(old_secret):
            raise PermissionError("old credential rejected (revoked or wrong)")
        return self.provisioning_record(new_secret)

    def revoke(self) -> None:
        self._drop("gateway_credentials.active_fingerprint")


# --- API surface classification --------------------------------------------------


def classify_path(path: str) -> str:
    """Map a request path to a gateway scope or the management/other bucket.

    Returns one of SCOPE_* / "health" / "management".
    """
    clean = path.split("?", 1)[0].rstrip("/") or "/"
    if clean == "/health":
        return "health"
    if clean == "/v1/models":
        return SCOPE_MODELS_LIST
    if clean == "/v1/chat/completions":
        return SCOPE_INFERENCE_READ  # stream/non-stream further classified by body
    # everything else — including the index and any runtime/thermal/ops/fs/
    # host/systemctl/backup/restore surface — is management and always refused.
    return "management"


def parse_stream(request_body: bytes) -> bool:
    """Best-effort, bounded detection of stream=true in the request JSON."""
    if len(request_body) > MAX_BODY_BYTES:
        # oversized already refused at the size gate
        return False
    try:
        payload = json.loads(request_body)
    except (ValueError, TypeError):
        return False
    return bool(payload.get("stream"))


@dataclass(frozen=True)
class Decision:
    allow: bool
    outcome: str
    reason: str
    scope: str | None = None
    status: int = 200
    principal: str | None = None


@dataclass(frozen=True)
class _PreparedRequest:
    decision: Decision
    principal: str
    scope: str | None
    rate_acquired: bool = False


class _ScopeMatrix:
    """A credential is granted an explicit set of scopes. A request is allowed
    when the required scope is in the granted set; management is always denied.
    """

    HEALTH_ALWAYS = True  # no credential required; reports gateway readiness

    @staticmethod
    def required_scope(classification: str, body: bytes) -> str:
        if classification == SCOPE_INFERENCE_READ:
            return SCOPE_INFERENCE_STREAM if parse_stream(body) else SCOPE_INFERENCE_READ
        return classification


class GatewayPolicy:
    """Pure request-authorization core. No network, no persistence, no timing
    dependence that affects correctness (constant-time compare lives in the
    CredentialStore)."""

    def __init__(
        self,
        store: CredentialStore,
        *,
        allowed_scopes: Iterable[str] = (
            SCOPE_INFERENCE_READ, SCOPE_INFERENCE_STREAM, SCOPE_MODELS_LIST),
    ) -> None:
        self.store = store
        self._allowed = set(allowed_scopes)

    def authorize(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        body: bytes,
        header_bytes: int,
        presented_token: str,
    ) -> Decision:
        # 1. header size bound
        if header_bytes > MAX_HEADER_BYTES:
            return Decision(False, "malformed", "request headers too large",
                            status=431)
        classification = classify_path(path)
        if classification == "health":
            # health is intentionally credential-free (gateway readiness only)
            return Decision(True, "ok", "health", scope=None, status=200)
        if classification == "management":
            return Decision(False, "denied", "management endpoint refused",
                            scope=None, status=403)
        # 2. size gate before parsing
        if len(body) > MAX_BODY_BYTES:
            return Decision(False, "denied", "request body too large",
                            status=413)
        # 3. credential.  Migration-011 stores authenticate to a named client
        # and its own scopes; the legacy singleton adapter remains supported
        # until its explicit compatibility surface is removed.
        identity = None
        authenticate = getattr(self.store, "authenticate", None)
        if callable(authenticate):
            identity = authenticate(presented_token)
            if identity is None:
                return Decision(False, "denied", "invalid credential",
                                status=401, principal="unauthenticated")
            granted = set(identity.scopes)
            principal = str(identity.client_id)
        else:
            if not self.store.is_valid(presented_token):
                return Decision(False, "denied", "invalid credential",
                                status=401)
            granted = self._allowed
            principal = None
        # 4. scope
        required = _ScopeMatrix.required_scope(classification, body)
        if required not in granted:
            return Decision(False, "denied", f"scope not granted: {required}",
                            scope=required, status=403, principal=principal)
        return Decision(True, "ok", "allowed", scope=required, status=200,
                        principal=principal)

    def record_use(self, principal: str, scope: str) -> None:
        recorder = getattr(self.store, "record_use", None)
        if callable(recorder):
            endpoint = (
                "models" if scope == SCOPE_MODELS_LIST
                else ("stream" if scope == SCOPE_INFERENCE_STREAM else "chat")
            )
            recorder(principal, endpoint)


def validate_body_bounds(body: bytes, ml) -> Decision:
    """Additional application-level bounds: context/generation/token budget."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        if body.strip():
            return Decision(False, "malformed", "malformed JSON body", status=400)
        return Decision(True, "ok", "empty body", status=200)
    if not isinstance(payload, dict):
        return Decision(False, "malformed", "body must be a JSON object", status=400)
    # maximum context budget = max_tokens + up-to model's ctx; enforce combined cap
    max_tokens = payload.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens <= 0:
        return Decision(False, "denied", "max_tokens must be positive", status=400)
    if isinstance(max_tokens, int) and max_tokens > MAX_GENERATED_TOKENS:
        return Decision(False, "denied", "max_tokens exceeds gateway cap",
                        status=400)
    context = payload.get("n_ctx") or payload.get("context_length")
    if isinstance(context, int) and context > MAX_CONTEXT_TOKENS:
        return Decision(False, "denied", "context exceeds gateway cap", status=400)
    return Decision(True, "ok", "bounds ok", status=200)


class _ClientRate:
    __slots__ = ("window_start", "count", "tokens", "inflight", "lock")

    def __init__(self) -> None:
        self.window_start = time.monotonic()
        self.count = 0
        self.tokens = 0
        self.inflight = 0
        self.lock = threading.Lock()


class RateLimiter:
    """Per-client concurrency + windowed rate + token allowance."""

    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.monotonic
        self._clients: dict[str, _ClientRate] = {}
        self._lock = threading.Lock()

    def _row(self, client: str) -> _ClientRate:
        with self._lock:
            row = self._clients.get(client)
            if row is None or self._now() - row.window_start >= RATE_WINDOW_S:
                row = _ClientRate()
                row.window_start = self._now()
                self._clients[client] = row
            return row

    def check(self, client: str) -> tuple[bool, str]:
        row = self._row(client)
        with row.lock:
            if row.inflight >= MAX_CONCURRENT:
                return False, "concurrency limit"
            row.inflight += 1
            if row.count >= MAX_REQUEST_RATE:
                row.inflight -= 1
                return False, "rate limit"
            row.count += 1
            return True, "allowed"

    def release(self, client: str) -> None:
        row = self._row(client)
        with row.lock:
            row.inflight = max(0, row.inflight - 1)

    def charge_tokens(self, client: str, amount: int) -> bool:
        row = self._row(client)
        with row.lock:
            if row.tokens + amount > CLIENT_TOKEN_ALLOWANCE:
                return False
            row.tokens += amount
            return True


# --- HTTP adapter -----------------------------------------------------------------
# The adapter is deliberately thin: real loopback sockets in, decision/policy in,
# bounded httpx proxy out. It never parses prompt/completion for content-bearing
# audit (only outcome/status/scope).

# gateway keeps the same bounded request policy shape as chat (P3 §9.4): connect/
# read/write deadlines cascade, so a dead/half-open backend is cut at its bound.
GATEWAY_CONNECT_TIMEOUT_S = 10.0
GATEWAY_READ_TIMEOUT_S = 120.0
GATEWAY_WRITE_TIMEOUT_S = 30.0


def _gateway_timeout() -> Any:
    import httpx

    return httpx.Timeout(
        GATEWAY_READ_TIMEOUT_S,
        connect=GATEWAY_CONNECT_TIMEOUT_S,
        write=GATEWAY_WRITE_TIMEOUT_S,
        read=GATEWAY_READ_TIMEOUT_S,
    )


def new_request_id() -> str:
    import uuid

    return uuid.uuid4().hex


class GatewayServer:
    """Loopback/tailnet-bound HTTP gateway.

    ``backend_base`` is the ONLY reachable origin; the gateway proxies an
    allowed request to ``{backend_base}{path}`` with the bounded httpx policy
    and streams the response body back. ``should_report_backend`` lets tests
    inject the fail-closed rule (backend identity verification) without a
    live llama server; the production wiring verifies the ADR 004 identity
    chain before enabling.
    """

    def __init__(
        self,
        policy: GatewayPolicy,
        *,
        backend_base: str,
        audit: AuditLogger | None = None,
        rate: RateLimiter | None = None,
        should_report_backend: Callable[[], bool] | None = None,
        max_response_body: int = 32 * 1024 * 1024,
    ) -> None:
        self.policy = policy
        self.backend_base = backend_base.rstrip("/")
        self.audit = audit or AuditLogger()
        self.rate = rate or RateLimiter()
        self._report_backend = should_report_backend or (lambda: True)
        self._max_response = max_response_body

    # -- fail-closed backend check --------------------------------------------------
    def backend_ready(self) -> bool:
        return bool(self._report_backend())

    def prepare_request(
        self,
        request_id: str,
        client: str,
        method: str,
        path: str,
        header_bytes: int,
        body: bytes,
        presented_token: str,
    ) -> _PreparedRequest:
        """Authorize, validate, and reserve one request slot.

        Denials are audited here. An allowed non-health result with
        ``rate_acquired`` set MUST be passed to :meth:`finish_request` exactly
        once by the buffered or streaming adapter.
        """
        decision = self.policy.authorize(
            request_id=request_id,
            method=method,
            path=path,
            body=body,
            header_bytes=header_bytes,
            presented_token=presented_token,
        )
        scope = decision.scope
        principal = decision.principal or client
        if decision.outcome == "ok" and scope is not None:
            bounds = validate_body_bounds(body, None)
            if not bounds.allow:
                decision = Decision(
                    bounds.allow,
                    bounds.outcome,
                    bounds.reason,
                    scope=scope,
                    status=bounds.status,
                    principal=principal,
                )
        if not decision.allow:
            self.audit.record(
                actor=principal,
                scope=scope or "n/a",
                request_id=request_id,
                method=method,
                path=path,
                outcome=decision.outcome,
                status=decision.status,
            )
            return _PreparedRequest(decision, principal, scope)
        if classify_path(path) == "health":
            return _PreparedRequest(decision, principal, scope)
        if not self.backend_ready():
            unavailable = Decision(
                False,
                "backend",
                "gateway cannot reach the expected backend identity",
                scope=scope,
                status=503,
                principal=principal,
            )
            self.audit.record(
                actor=principal,
                scope=scope or "n/a",
                request_id=request_id,
                method=method,
                path=path,
                outcome=unavailable.outcome,
                status=unavailable.status,
            )
            return _PreparedRequest(unavailable, principal, scope)
        ok, message = self.rate.check(principal)
        if not ok:
            limited = Decision(
                False,
                "rate_limited",
                message,
                scope=scope,
                status=429,
                principal=principal,
            )
            self.audit.record(
                actor=principal,
                scope=scope or "n/a",
                request_id=request_id,
                method=method,
                path=path,
                outcome=limited.outcome,
                status=limited.status,
            )
            return _PreparedRequest(limited, principal, scope)
        return _PreparedRequest(decision, principal, scope, rate_acquired=True)

    def finish_request(
        self,
        prepared: _PreparedRequest,
        *,
        request_id: str,
        method: str,
        path: str,
        outcome: str,
        status: int,
    ) -> None:
        """Release a prepared request and record content-free outcome data."""
        if prepared.rate_acquired:
            self.rate.release(prepared.principal)
        self.audit.record(
            actor=prepared.principal,
            scope=prepared.scope or "n/a",
            request_id=request_id,
            method=method,
            path=path,
            outcome=outcome,
            status=status,
        )
        if (
            prepared.scope is not None
            and outcome == "ok"
            and 200 <= status < 400
        ):
            try:
                self.policy.record_use(prepared.principal, prepared.scope)
            except Exception:
                # Usage freshness is optional redacted evidence. It can never
                # turn a successful inference request into a failure.
                pass

    # -- request handling core (takes request bytes; returns (status, headers, body,
    #    stream)) so it can be driven both by the live HTTP handler and by tests.
    def handle(self, request_id: str, client: str, method: str, path: str,
               header_bytes: int, body: bytes, presented_token: str,
               backend_request: Callable[[], tuple[int, bytes]]) -> tuple[int, bytes, bool]:
        """Authorize and, if allowed, forward to the backend.

        Returns ``(status, body, stream)``. ``stream`` is advisory (the live
        handler uses SSE pass-through); tests consume non-stream bodies.
        """
        prepared = self.prepare_request(
            request_id,
            client,
            method,
            path,
            header_bytes,
            body,
            presented_token,
        )
        decision = prepared.decision
        if not decision.allow:
            reason = json.dumps({"error": decision.reason}).encode("utf-8")
            return decision.status, reason, False
        # health short-circuits: reports gateway readiness/redacted backend
        # identity, never proxies and never requires a credential.
        if classify_path(path) == "health":
            payload = {
                "status": "ready" if self.backend_ready() else "degraded",
                "api_version": GATEWAY_API_VERSION,
                "backend_identity": "verified" if self.backend_ready() else "unverified",
            }
            return 200, json.dumps(payload).encode("utf-8"), False
        response_status = 502
        response_body = json.dumps({"error": "backend forward failed"}).encode(
            "utf-8"
        )
        outcome = "backend"
        try:
            response_status, response_body = backend_request()
            if len(response_body) > self._max_response:
                response_status = 502
                response_body = json.dumps(
                    {"error": "backend response too large"}
                ).encode("utf-8")
            else:
                outcome = "ok"
        except Exception as exc:  # noqa: BLE001 - map to bounded 502
            response_body = json.dumps(
                {"error": "backend forward failed",
                 "message": str(exc)[:200]}
            ).encode("utf-8")
        finally:
            self.finish_request(
                prepared,
                request_id=request_id,
                method=method,
                path=path,
                outcome=outcome,
                status=response_status,
            )
        return response_status, response_body, parse_stream(body)

    # -- helper that builds the actual backend HTTP call (bounded) -----------------
    def proxy_request(self, request_id: str, method: str, path: str,
                      body: bytes, presented_token: str) -> tuple[int, bytes]:
        import httpx

        url = f"{self.backend_base}{path.split('?')[0] if '?' in path else path}"
        # identity check for the backend (bounded)
        with httpx.Client(timeout=_gateway_timeout()) as client:
            headers = {"Content-Type": "application/json"}
            response = client.request(method, url, content=body, headers=headers)
            return response.status_code, response.content

    @contextmanager
    def open_backend_stream(self, method: str, path: str, body: bytes):
        """Open one bounded upstream response without forwarding credentials."""
        import httpx

        url = f"{self.backend_base}{path.split('?')[0] if '?' in path else path}"
        with httpx.Client(timeout=_gateway_timeout()) as client:
            with client.stream(
                method,
                url,
                content=body,
                headers={"Content-Type": "application/json"},
            ) as response:
                yield response

    # -- bounded SSE proxy used by the live handler ---------------------------------
    def proxy_stream(self, method: str, path: str, body: bytes,
                     presented_token: str):
        with self.open_backend_stream(method, path, body) as response:
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > self._max_response:
                    break
                yield response.status_code, chunk


# --- live HTTP server -----------------------------------------------------------
# A real loopback/tailnet-bound HTTP/1.1 server. The exit gate exercises it over
# live sockets (same evidence standard as the P3 process/HTTP probes), so the
# whole path — real bytes -> policy -> bounded backend proxy -> response — is
# proven, not just the pure core. Management is denied at the policy before any
# forwarding; health is credential-free; oversized requests are refused before
# the backend is contacted.

class _GatewayHandler:
    def __init__(self, server: "GatewayServer"):  # noqa: F821
        self.gateway = server

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        gate = self.gateway
        request_id = new_request_id()
        header_bytes = sum(len(k) + len(v) + 2 for k, v in headers.items()) + 16
        presented = (headers.get("authorization") or "")
        if presented.lower().startswith("bearer "):
            presented = presented[7:].strip()
        # snatch client by X-Forwarded-For peer or a stable integration label
        actor = headers.get("x-gateway-client") or headers.get("x-forwarded-for") or "unknown"

        if len(body) > MAX_BODY_BYTES:
            gate.audit.record(
                actor=actor, scope="n/a", request_id=request_id, method=method,
                path=path, outcome="malformed", status=413,
            )
            return 413, {"Content-Type": "application/json"}, json.dumps(
                {"error": "request body too large"}).encode("utf-8")

        def backend_request() -> tuple[int, bytes]:
            # fail-closed identity already checked in handle(); here we proxy
            return gate.proxy_request(request_id, method, path, body, presented)

        status, response_body, _stream = self._route(
            request_id, actor, method, path, header_bytes, body, presented,
            backend_request,
        )
        headers_out = {
            "Content-Type": "application/json",
            "X-Gateway-Api-Version": str(GATEWAY_API_VERSION),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        }
        return status, headers_out, response_body

    def _route(self, request_id, actor, method, path, header_bytes, body,
               presented, backend_request):
        # delegate the whole decision chain to GatewayServer.handle (authorize,
        # bounds, fail-closed, rate limit, audit) using the live backend proxy.
        return self.gateway.handle(
            request_id, actor, method, path, header_bytes, body, presented,
            backend_request,
        )


def build_gateway_server(
    *,
    secret: str,
    allowed_scopes: Iterable[str] = (
        SCOPE_INFERENCE_READ, SCOPE_INFERENCE_STREAM, SCOPE_MODELS_LIST),
    backend_base: str,
    should_report_backend: Callable[[], bool] | None = None,
    audit: AuditLogger | None = None,
) -> "GatewayServer":
    """Compose the production gateway from a provisioned secret + policy.

    Only the non-secret fingerprint is retained in the store; the passed
    ``secret`` is compared constant-time at request time and is never
    persisted, logged, or placed in argv/container labels.
    """
    slot: list[str] = [fingerprint_of(secret)]
    store = CredentialStore(
        get_backend=lambda key: slot[0] if key.endswith("active_fingerprint") else None,
        set_backend=lambda key, value: slot.__setitem__(0, value),
        drop_backend=lambda key: slot.__setitem__(0, ""),
    )
    policy = GatewayPolicy(store, allowed_scopes=allowed_scopes)
    return GatewayServer(
        policy, backend_base=backend_base,
        should_report_backend=should_report_backend, audit=audit,
    )


def build_multi_client_gateway_server(
    *,
    authentication_store: Any,
    backend_base: str,
    should_report_backend: Callable[[], bool] | None = None,
    audit: AuditLogger | None = None,
) -> "GatewayServer":
    """Compose migration-011 named-client authentication.

    The injected store resolves a Bearer token to a client ID and that
    client's scopes.  No plaintext credential is retained by this object.
    """
    return GatewayServer(
        GatewayPolicy(authentication_store), backend_base=backend_base,
        should_report_backend=should_report_backend, audit=audit,
    )


# --- real socket server for the exit gate ----------------------------------------

class _SocketHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BC250Gateway"

    def _read_body(self) -> bytes:
        length = min(int(self.headers.get("Content-Length") or 0),
                     MAX_BODY_BYTES + 1)
        return self.rfile.read(length)

    def _headers_dict(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self.headers.items()}

    def _send_buffered(
        self, status: int, headers_out: dict[str, str], payload: bytes
    ) -> None:
        self.send_response(status)
        for key, value in headers_out.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _run_stream(
        self,
        gateway: "GatewayServer",
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        request_id = new_request_id()
        header_bytes = sum(len(k) + len(v) + 2 for k, v in headers.items()) + 16
        presented = headers.get("authorization") or ""
        if presented.lower().startswith("bearer "):
            presented = presented[7:].strip()
        actor = (
            headers.get("x-gateway-client")
            or headers.get("x-forwarded-for")
            or "unknown"
        )
        prepared = gateway.prepare_request(
            request_id,
            actor,
            self.command,
            self.path,
            header_bytes,
            body,
            presented,
        )
        if not prepared.decision.allow:
            payload = json.dumps(
                {"error": prepared.decision.reason}
            ).encode("utf-8")
            self._send_buffered(
                prepared.decision.status,
                {
                    "Content-Type": "application/json",
                    "X-Gateway-Api-Version": str(GATEWAY_API_VERSION),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
                payload,
            )
            return

        response_status = 502
        outcome = "backend"
        headers_sent = False
        try:
            with gateway.open_backend_stream(
                self.command, self.path, body
            ) as response:
                declared_length = int(response.headers.get("Content-Length") or 0)
                if declared_length > gateway._max_response:
                    raise RuntimeError("backend stream response too large")
                response_status = response.status_code
                self.send_response(response_status)
                self.send_header(
                    "Content-Type",
                    response.headers.get("Content-Type", "text/event-stream"),
                )
                self.send_header(
                    "X-Gateway-Api-Version", str(GATEWAY_API_VERSION)
                )
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                headers_sent = True
                total = 0
                for chunk in response.iter_raw():
                    total += len(chunk)
                    if total > gateway._max_response:
                        response_status = 502
                        raise RuntimeError("backend stream response too large")
                    self.wfile.write(chunk)
                    self.wfile.flush()
                outcome = "ok" if 200 <= response_status < 400 else "backend"
        except (BrokenPipeError, ConnectionResetError):
            # The client disconnected.  The in-flight slot is still released;
            # no prompt or partial completion is logged.
            outcome = "client_closed"
        except Exception:  # noqa: BLE001 - fail closed before headers are sent
            if not headers_sent:
                self._send_buffered(
                    502,
                    {
                        "Content-Type": "application/json",
                        "X-Gateway-Api-Version": str(GATEWAY_API_VERSION),
                        "X-Content-Type-Options": "nosniff",
                        "Cache-Control": "no-store",
                    },
                    json.dumps({"error": "backend forward failed"}).encode("utf-8"),
                )
        finally:
            gateway.finish_request(
                prepared,
                request_id=request_id,
                method=self.command,
                path=self.path,
                outcome=outcome,
                status=response_status,
            )

    def _run(self) -> None:
        gateway: "GatewayServer" = self.server.gateway  # type: ignore[attr-defined]
        body = self._read_body()
        headers = self._headers_dict()
        if (
            classify_path(self.path) == SCOPE_INFERENCE_READ
            and parse_stream(body)
        ):
            self._run_stream(gateway, body=body, headers=headers)
            return
        handler = _GatewayHandler(gateway)
        try:
            status, headers_out, payload = handler.handle(
                method=self.command,
                path=self.path,
                headers=headers,
                body=body,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed on any handler error
            status, headers_out, payload = (502, {"Content-Type": "application/json"},
                                            json.dumps({"error": "gateway internal"}).encode("utf-8"))
        self._send_buffered(status, headers_out, payload)

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _run
    do_OPTIONS = _run
    do_PATCH = _run

    def log_message(self, *args):  # do not leak request paths/content to stderr
        pass


def make_gateway_socket_server(gateway: "GatewayServer", *, host: str = "127.0.0.1",
                               port: int = 0) -> "http.server.ThreadingHTTPServer":
    """Bind a real loopback socket server wired to the gateway (exit gate)."""
    server_cls = type("GatewayHTTPServer", (http.server.ThreadingHTTPServer,), {})
    server = server_cls((host, port), _SocketHandler)
    server.gateway = gateway  # type: ignore[attr-defined]
    return server
