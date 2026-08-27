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
import json
import threading
import time
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
    outcome: str        # ok | denied | malformed | rate_limited | concurrency | backend
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
        # 3. credential
        if not self.store.is_valid(presented_token):
            return Decision(False, "denied", "invalid credential",
                            status=401)
        # 4. scope
        required = _ScopeMatrix.required_scope(classification, body)
        if required not in self._allowed:
            return Decision(False, "denied", f"scope not granted: {required}",
                            scope=required, status=403)
        return Decision(True, "ok", "allowed", scope=required, status=200)


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

    # -- request handling core (takes request bytes; returns (status, headers, body,
    #    stream)) so it can be driven both by the live HTTP handler and by tests.
    def handle(self, request_id: str, client: str, method: str, path: str,
               header_bytes: int, body: bytes, presented_token: str,
               backend_request: Callable[[], tuple[int, bytes]]) -> tuple[int, bytes, bool]:
        """Authorize and, if allowed, forward to the backend.

        Returns ``(status, body, stream)``. ``stream`` is advisory (the live
        handler uses SSE pass-through); tests consume non-stream bodies.
        """
        decision = self.policy.authorize(
            request_id=request_id, method=method, path=path, body=body,
            header_bytes=header_bytes, presented_token=presented_token,
        )
        scope = decision.scope
        if decision.outcome == "ok" and scope is not None:
            decision = validate_body_bounds(body, None)
        # management/unknown handled by authorize
        if not decision.allow:
            self.audit.record(
                actor=client, scope=scope or "n/a", request_id=request_id,
                method=method, path=path, outcome=decision.outcome,
                status=decision.status,
            )
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
        # fail closed: cannot reach the expected backend identity
        if not self.backend_ready():
            self.audit.record(
                actor=client, scope=scope or "n/a", request_id=request_id,
                method=method, path=path, outcome="backend",
                status=503,
            )
            return 503, json.dumps(
                {"error": "gateway cannot reach the expected backend identity"}
            ).encode("utf-8"), False
        # rate-limit before touching the backend
        ok, msg = self.rate.check(client)
        if not ok:
            self.audit.record(
                actor=client, scope=scope or "n/a", request_id=request_id,
                method=method, path=path, outcome="rate_limited", status=429,
            )
            return 429, json.dumps({"error": msg}).encode("utf-8"), False
        try:
            status, response_body = backend_request()
        except Exception as exc:  # noqa: BLE001 - map to bounded 502
            self.rate.release(client)
            self.audit.record(
                actor=client, scope=scope or "n/a", request_id=request_id,
                method=method, path=path, outcome="backend", status=502,
            )
            return 502, json.dumps(
                {"error": "backend forward failed",
                 "message": str(exc)[:200]}
            ).encode("utf-8"), False
        finally:
            self.rate.release(client)
        if len(response_body) > self._max_response:
            return 502, json.dumps({"error": "backend response too large"}).encode("utf-8"), False
        self.audit.record(
            actor=client, scope=scope or "n/a", request_id=request_id,
            method=method, path=path, outcome="ok", status=status,
        )
        return status, response_body, parse_stream(body)

    # -- helper that builds the actual backend HTTP call (bounded) -----------------
    def proxy_request(self, request_id: str, method: str, path: str,
                      body: bytes, presented_token: str) -> tuple[int, bytes]:
        import httpx

        url = f"{self.backend_base}{path.split('?')[0] if '?' in path else path}"
        # identity check for the backend (bounded)
        with httpx.Client(timeout=_gateway_timeout()) as client:
            headers = {"Content-Type": "application/json"}
            if presented_token:
                headers["Authorization"] = f"Bearer {presented_token}"
            response = client.request(method, url, content=body, headers=headers)
            return response.status_code, response.content

    # -- bounded SSE proxy used by the live handler ---------------------------------
    def proxy_stream(self, method: str, path: str, body: bytes,
                     presented_token: str):
        import httpx

        url = f"{self.backend_base}{path.split('?')[0] if '?' in path else path}"
        with httpx.Client(timeout=_gateway_timeout()) as client:
            headers = {"Content-Type": "application/json"}
            if presented_token:
                headers["Authorization"] = f"Bearer {presented_token}"
            with client.stream(method, url, content=body, headers=headers) as response:
                status = response.status_code
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self._max_response:
                        break
                    yield status, chunk