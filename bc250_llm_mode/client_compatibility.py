"""Versioned offline client/API compatibility contract.

This module is the single local source for the gateway route matrix, client
cards, Help/Connections copy, CLI output, documentation drift tests, and
redacted support metadata.  It performs no network access and contains no
credential values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit


CLIENT_COMPATIBILITY_SCHEMA_VERSION = 1
CLIENT_CARD_SCHEMA_VERSION = 2
OPENAI_COMPATIBILITY_PROFILE = "bc250-openai-compatible-v1"
OPENWEBUI_GATEWAY_BASE_URL = "http://host.containers.internal:9071/v1"

CAPABILITY_SUPPORTED = "supported"
CAPABILITY_CONDITIONAL = "conditional"
CAPABILITY_UNSUPPORTED = "unsupported"
CAPABILITY_DEFERRED = "deferred"
CAPABILITY_NOT_CLIENT_API = "not-client-api"
CAPABILITY_STATUSES = frozenset({
    CAPABILITY_SUPPORTED,
    CAPABILITY_CONDITIONAL,
    CAPABILITY_UNSUPPORTED,
    CAPABILITY_DEFERRED,
    CAPABILITY_NOT_CLIENT_API,
})
CLIENT_SUPPORT_LEVELS = frozenset({
    "hardware-tested", "protocol-tested", "example-only",
})


@dataclass(frozen=True)
class APICapability:
    capability_id: str
    method: str
    path: str
    status: str
    summary: str
    evidence_requirement: str
    gateway_behavior: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


API_CAPABILITIES = (
    APICapability(
        "models-list", "GET", "/v1/models", CAPABILITY_SUPPORTED,
        "Lists only the currently observed public model alias.",
        "Gateway route, authentication, scope, and model-identity fixtures.",
        "models:list",
    ),
    APICapability(
        "chat-json", "POST", "/v1/chat/completions", CAPABILITY_SUPPORTED,
        "Bounded OpenAI-compatible JSON chat completions.",
        "Authentication, request bounds, backend identity, and response fixtures.",
        "inference:read",
    ),
    APICapability(
        "chat-sse", "POST", "/v1/chat/completions", CAPABILITY_SUPPORTED,
        "Real server-sent event pass-through ending in [DONE].",
        "First-event, terminal-event, disconnect, bound, and exact-release fixtures.",
        "inference:stream",
    ),
    APICapability(
        "tools", "POST", "/v1/chat/completions", CAPABILITY_CONDITIONAL,
        "Tool or function calling is not a general compatibility promise.",
        "Exact model, runtime, request shape, and client-version evidence is required.",
        "inference:read",
    ),
    APICapability(
        "embeddings", "POST", "/v1/embeddings", CAPABILITY_UNSUPPORTED,
        "Embeddings are not implemented by this compatibility profile.",
        "A separate bounded semantics, resource, security, and client review is required.",
        "unsupported-inference",
    ),
    APICapability(
        "legacy-completions", "POST", "/v1/completions",
        CAPABILITY_UNSUPPORTED,
        "Legacy completions are not silently mapped to chat semantics.",
        "A separate bounded legacy-semantics contract is required.",
        "unsupported-inference",
    ),
    APICapability(
        "responses", "POST", "/v1/responses", CAPABILITY_DEFERRED,
        "The Responses API requires a separately reviewed adapter.",
        "Threat, performance, semantics, and real-client qualification is required.",
        "unsupported-inference",
    ),
    APICapability(
        "openwebui-browser-api", "ANY", "/api/...",
        CAPABILITY_NOT_CLIENT_API,
        "Open WebUI /api routes belong to the browser application, not the model API.",
        "Use the displayed Open WebUI browser URL or the separate /v1 model Base URL.",
        "management",
    ),
)
_CAPABILITY_BY_ID = {item.capability_id: item for item in API_CAPABILITIES}


@dataclass(frozen=True)
class ClientCard:
    card_id: str
    title: str
    credential_kind: str
    support_level: str
    support_evidence: str
    field_labels: tuple[str, ...]
    streaming: str
    timeout_seconds: int
    notes: tuple[str, ...] = ()
    tested_version: str | None = None
    required_capability_ids: tuple[str, ...] = ()
    automatic_probe_paths: tuple[str, ...] = ()
    base_url_rule: str = "Enter the displayed Base URL exactly once."
    transport_requirement: str = "Private tailnet HTTPS"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = CLIENT_CARD_SCHEMA_VERSION
        for key in (
            "field_labels", "notes", "required_capability_ids",
            "automatic_probe_paths",
        ):
            value[key] = list(value[key])
        return value


# Hardware-tested is intentionally absent until exact-candidate second-device
# evidence exists.  A tested_version is claimed only for the pinned Open WebUI
# adapter covered by protocol/container fixtures.
CLIENT_CARDS = (
    ClientCard(
        "openwebui", "Open WebUI", "openwebui", "protocol-tested",
        "Pinned provider adapter plus gateway/container protocol fixtures.",
        ("Base URL", "API Key", "Model"), "Enabled", 120,
        ("Managed setup uses the app-owned OpenAI-compatible provider, not an /api URL.",),
        tested_version="0.11.1",
        required_capability_ids=("models-list", "chat-sse"),
        automatic_probe_paths=("GET /v1/models", "POST /v1/chat/completions stream=true"),
        base_url_rule="Managed provider uses the private container gateway Base URL ending once in /v1.",
        transport_requirement="App-owned private Podman bridge",
    ),
    ClientCard(
        "pocketpal", "PocketPal", "pocketpal", "example-only",
        "Physical phone and app-version qualification is pending.",
        ("Base URL", "API Key", "Model", "Streaming", "Timeout"),
        "Enabled", 120,
        ("The phone must be connected to the same tailnet.",
         "Confirm the app uses Chat Completions; embeddings and Responses are unsupported."),
        required_capability_ids=("models-list", "chat-sse"),
        automatic_probe_paths=("GET /v1/models", "POST /v1/chat/completions stream=true"),
    ),
    ClientCard(
        "openai", "OpenAI-compatible app", "openai", "protocol-tested",
        "Generic OpenAI-compatible request/response protocol fixtures; no app version claimed.",
        ("Base URL", "API Key", "Model", "Streaming", "Timeout"),
        "Enabled", 120,
        ("Apps that require embeddings, legacy completions, or Responses are not compatible.",),
        required_capability_ids=("models-list", "chat-json", "chat-sse"),
        automatic_probe_paths=("GET /v1/models", "POST /v1/chat/completions"),
    ),
    ClientCard(
        "curl", "curl", "curl", "protocol-tested",
        "Bounded HTTP models and JSON/SSE chat fixtures; no curl version claimed.",
        ("Base URL", "Authorization", "Model"), "Optional", 20,
        required_capability_ids=("models-list", "chat-json", "chat-sse"),
        automatic_probe_paths=("GET /v1/models", "POST /v1/chat/completions"),
    ),
    ClientCard(
        "python", "Python OpenAI client", "openai", "example-only",
        "Example configuration; SDK-version qualification is pending.",
        ("base_url", "api_key", "model", "timeout"), "Enabled", 120,
        ("Configure Chat Completions explicitly; the Responses API is deferred.",),
        required_capability_ids=("models-list", "chat-json", "chat-sse"),
        automatic_probe_paths=("GET /v1/models", "POST /v1/chat/completions"),
    ),
    ClientCard(
        "sse", "Raw SSE diagnostic", "sse", "protocol-tested",
        "One-event and terminal-event bounded SSE fixtures.",
        ("Base URL", "Authorization", "Model", "stream"), "Required", 20,
        required_capability_ids=("chat-sse",),
        automatic_probe_paths=("POST /v1/chat/completions stream=true",),
        transport_requirement="Private tailnet HTTPS or local loopback diagnostic",
    ),
)
_CARD_BY_ID = {card.card_id: card for card in CLIENT_CARDS}


def client_card_contract(card_id: str) -> ClientCard:
    """Return one reviewed local card; never synthesize an unknown client."""
    return _CARD_BY_ID[str(card_id).strip().lower()]


def capability_contract() -> dict[str, Any]:
    """Bounded secret-free matrix for GUI, CLI, docs, and support."""
    return {
        "schema_version": CLIENT_COMPATIBILITY_SCHEMA_VERSION,
        "profile": OPENAI_COMPATIBILITY_PROFILE,
        "offline": True,
        "capabilities": [item.to_dict() for item in API_CAPABILITIES],
        "client_cards": [card.to_dict() for card in CLIENT_CARDS],
    }


def capability_display_rows() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (f"{item.method} {item.path}", item.status, item.summary)
        for item in API_CAPABILITIES
    )


def gateway_route_classification(path: str) -> str:
    """Classify a gateway path from the published capability contract."""
    clean = str(path).split("?", 1)[0].rstrip("/") or "/"
    if clean == "/v1/models":
        return _CAPABILITY_BY_ID["models-list"].gateway_behavior
    if clean == "/v1/chat/completions":
        return _CAPABILITY_BY_ID["chat-json"].gateway_behavior
    if clean == "/v1/embeddings":
        return _CAPABILITY_BY_ID["embeddings"].gateway_behavior
    if clean == "/v1/completions":
        return _CAPABILITY_BY_ID["legacy-completions"].gateway_behavior
    if clean == "/v1/responses":
        return _CAPABILITY_BY_ID["responses"].gateway_behavior
    return "management"


def base_url_problem(value: Any, *, openwebui: bool = False) -> str | None:
    """Explain the common duplicate-/v1 and unsafe-transport mistakes."""
    if not isinstance(value, str) or not value.strip():
        return "BASE_URL_MISSING"
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return "BASE_URL_INVALID"
    expected_scheme = "http" if openwebui else "https"
    if parsed.scheme != expected_scheme or not parsed.hostname:
        return "BASE_URL_TRANSPORT_INVALID"
    if parsed.path.rstrip("/").endswith("/v1/v1"):
        return "BASE_URL_DUPLICATES_V1"
    if parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/v1":
        return "BASE_URL_PATH_INVALID"
    return None


def validate_contract() -> None:
    """Fail closed at import/test time if a reviewed claim drifts."""
    if len(API_CAPABILITIES) != 8 or len(_CAPABILITY_BY_ID) != 8:
        raise RuntimeError("client capability contract must contain eight unique rows")
    for item in API_CAPABILITIES:
        if item.status not in CAPABILITY_STATUSES:
            raise RuntimeError("unknown capability status")
    if len(CLIENT_CARDS) > 16 or len(_CARD_BY_ID) != len(CLIENT_CARDS):
        raise RuntimeError("client card contract is duplicate or unbounded")
    for card in CLIENT_CARDS:
        if card.support_level not in CLIENT_SUPPORT_LEVELS:
            raise RuntimeError("unknown client support level")
        if card.tested_version and card.support_level == "example-only":
            raise RuntimeError("a client version cannot be claimed without evidence")
        if not (1 <= len(card.field_labels) <= 8):
            raise RuntimeError("client field list is unbounded")
        for capability_id in card.required_capability_ids:
            capability = _CAPABILITY_BY_ID.get(capability_id)
            if capability is None or capability.status != CAPABILITY_SUPPORTED:
                raise RuntimeError("advertised client requires an unsupported capability")


validate_contract()


__all__ = [
    "API_CAPABILITIES", "CAPABILITY_CONDITIONAL", "CAPABILITY_DEFERRED",
    "CAPABILITY_NOT_CLIENT_API", "CAPABILITY_STATUSES",
    "CAPABILITY_SUPPORTED", "CAPABILITY_UNSUPPORTED", "CLIENT_CARDS",
    "CLIENT_CARD_SCHEMA_VERSION", "CLIENT_COMPATIBILITY_SCHEMA_VERSION",
    "CLIENT_SUPPORT_LEVELS", "ClientCard", "OPENAI_COMPATIBILITY_PROFILE",
    "OPENWEBUI_GATEWAY_BASE_URL", "base_url_problem",
    "capability_contract", "capability_display_rows", "client_card_contract",
    "gateway_route_classification", "validate_contract",
]
