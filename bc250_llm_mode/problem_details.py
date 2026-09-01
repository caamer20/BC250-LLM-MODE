"""Stable, redacted problem details for user and protocol surfaces.

Problem codes are public compatibility identifiers.  Messages contain no
dynamic exception text, filesystem path, request content, or credential data.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any


MAX_TECHNICAL_SUMMARY = 240
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


@dataclass(frozen=True)
class ProblemDetail:
    code: str
    category: str
    severity: str
    title: str
    user_message: str
    component: str
    safe_action_id: str
    safe_action_label: str
    technical_summary: str
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _problem(
    code: str,
    category: str,
    severity: str,
    title: str,
    message: str,
    component: str,
    action_id: str,
    action_label: str,
    technical: str,
) -> ProblemDetail:
    return ProblemDetail(
        code, category, severity, title, message, component,
        action_id, action_label, technical,
    )


PROBLEM_CATALOG: dict[str, ProblemDetail] = {
    "MODEL_NOT_SELECTED": _problem(
        "MODEL_NOT_SELECTED", "readiness", "warning", "Choose a model",
        "Choose an installed model before starting a connection.", "model",
        "choose-model", "Choose a model", "No selected model identity is available."),
    "MODEL_NOT_RUNNING": _problem(
        "MODEL_NOT_RUNNING", "readiness", "warning", "Model is stopped",
        "Start the selected model, then try again.", "model",
        "start-model", "Start model", "The model process is not active."),
    "MODEL_ABSENT": _problem(
        "MODEL_ABSENT", "readiness", "warning", "No model is installed",
        "Choose and install a model before connecting another device.", "model",
        "choose-model", "Choose a model", "No installed model identity is available."),
    "MODEL_STOPPED": _problem(
        "MODEL_STOPPED", "readiness", "warning", "Model is stopped",
        "Start the selected model, then run the connection check.", "model",
        "start-model", "Start model", "The selected model process is stopped."),
    "MODEL_IDENTITY_MISMATCH": _problem(
        "MODEL_IDENTITY_MISMATCH", "readiness", "error", "Different model is running",
        "Reconcile the running model before sharing it.", "model",
        "reconcile-model", "Reconcile model", "Observed and expected model identities differ."),
    "MODEL_WARMING": _problem(
        "MODEL_WARMING", "readiness", "info", "Model is warming up",
        "Wait for startup verification to finish.", "model",
        "view-activity", "View Activity", "Model startup has not reached verified inference."),
    "GATEWAY_NOT_INSTALLED": _problem(
        "GATEWAY_NOT_INSTALLED", "readiness", "warning", "Private API is not installed",
        "Install the private API service for this application slot.", "gateway",
        "install-gateway", "Install private API", "The managed gateway unit is absent."),
    "GATEWAY_NOT_RUNNING": _problem(
        "GATEWAY_NOT_RUNNING", "readiness", "warning", "Private API is stopped",
        "Start private API access for this boot.", "gateway",
        "start-gateway", "Start private API", "The managed gateway process is not active."),
    "GATEWAY_STOPPED": _problem(
        "GATEWAY_STOPPED", "readiness", "warning", "Private API is stopped",
        "Start the authenticated gateway for this boot.", "gateway",
        "start-gateway", "Start private API", "The gateway process is stopped."),
    "GATEWAY_CREDENTIAL_REQUIRED": _problem(
        "GATEWAY_CREDENTIAL_REQUIRED", "authentication", "warning", "Client key required",
        "Create a separate client key for this device or app.", "gateway",
        "create-client", "Create client key", "No active named client is selected."),
    "GATEWAY_BACKEND_UNVERIFIED": _problem(
        "GATEWAY_BACKEND_UNVERIFIED", "upstream", "error", "Model backend is not ready",
        "Start or reconcile the selected model, then retry.", "gateway",
        "reconcile-model", "Check model", "The gateway could not verify its expected backend identity."),
    "AUTH_MISSING": _problem(
        "AUTH_MISSING", "authentication", "warning", "API key is missing",
        "Enter this app's named API key in the client and retry.", "gateway",
        "configure-api-key", "Enter API key", "No Bearer credential was presented."),
    "AUTH_INVALID": _problem(
        "AUTH_INVALID", "authentication", "warning", "API key was rejected",
        "Use the key created for this app, or rotate that app's key.", "gateway",
        "rotate-client", "Check or rotate key", "The Bearer credential did not match an active generation."),
    "SCOPE_NOT_GRANTED": _problem(
        "SCOPE_NOT_GRANTED", "authorization", "warning", "Client permission is missing",
        "Create a client key with the required model permission.", "gateway",
        "review-client", "Review client access", "The authenticated client lacks the required capability scope."),
    "ENDPOINT_UNSUPPORTED": _problem(
        "ENDPOINT_UNSUPPORTED", "compatibility", "warning", "Endpoint is not supported",
        "Configure the app for Chat Completions at the displayed /v1 Base URL.", "gateway",
        "show-connection-card", "Show compatible settings", "The authenticated request targeted a known unsupported inference endpoint."),
    "ENDPOINT_FORBIDDEN": _problem(
        "ENDPOINT_FORBIDDEN", "security", "error", "Endpoint is private",
        "Use only the displayed model API endpoints.", "gateway",
        "show-connection-card", "Show supported endpoints", "The requested path is outside the public inference surface."),
    "REQUEST_TOO_LARGE": _problem(
        "REQUEST_TOO_LARGE", "request", "warning", "Request is too large",
        "Reduce the request or context size and retry.", "gateway",
        "reduce-request", "Reduce request", "A configured request bound was exceeded."),
    "REQUEST_HEADERS_TOO_LARGE": _problem(
        "REQUEST_HEADERS_TOO_LARGE", "request", "warning", "Request headers are too large",
        "Remove unnecessary client headers and retry.", "gateway",
        "reduce-headers", "Reduce headers", "The aggregate request-header bound was exceeded."),
    "INVALID_REQUEST": _problem(
        "INVALID_REQUEST", "request", "warning", "Request is invalid",
        "Check the client's OpenAI-compatible request settings and retry.", "gateway",
        "review-request", "Review request", "The request body did not satisfy the bounded JSON contract."),
    "RATE_LIMITED": _problem(
        "RATE_LIMITED", "capacity", "warning", "Too many requests",
        "Wait briefly, then retry with fewer concurrent requests.", "gateway",
        "retry-later", "Retry later", "The per-client request or concurrency bound was reached."),
    "OPENWEBUI_START_FAILED": _problem(
        "OPENWEBUI_START_FAILED", "openwebui", "error", "Open WebUI did not start",
        "Open Activity for the safe rollback result, then retry.", "openwebui",
        "view-activity", "View Activity", "Open WebUI convergence did not complete."),
    "OPENWEBUI_WARMING": _problem(
        "OPENWEBUI_WARMING", "openwebui", "info", "Open WebUI is starting",
        "Wait for its HTTP and provider checks to finish.", "openwebui",
        "view-activity", "View Activity", "Open WebUI has not completed readiness checks."),
    "OPENWEBUI_PROVIDER_STALE": _problem(
        "OPENWEBUI_PROVIDER_STALE", "openwebui", "warning", "Open WebUI provider needs repair",
        "Run Open WebUI setup again to reconcile its named provider.", "openwebui",
        "setup-openwebui", "Reconfigure Open WebUI", "The provider receipt does not match the current configuration identity."),
    "OPENWEBUI_MODEL_MISSING": _problem(
        "OPENWEBUI_MODEL_MISSING", "openwebui", "warning", "Model is missing in Open WebUI",
        "Run Open WebUI setup after starting the selected model.", "openwebui",
        "setup-openwebui", "Reconfigure Open WebUI", "The selected public model alias was not discovered."),
    "TAILSCALE_DISCONNECTED": _problem(
        "TAILSCALE_DISCONNECTED", "network", "warning", "Private network is disconnected",
        "Connect Tailscale on this BC-250, then retry.", "tailscale",
        "connect-tailscale", "Connect Tailscale", "The local node is not connected to its tailnet."),
    "SERVE_MAPPING_MISSING": _problem(
        "SERVE_MAPPING_MISSING", "network", "warning", "Private address is not published",
        "Publish the reviewed private HTTPS mappings.", "serve",
        "configure-serve", "Configure private sharing", "The required Tailscale Serve mapping is absent."),
    "SERVE_MAPPING_MISMATCH": _problem(
        "SERVE_MAPPING_MISMATCH", "network", "error", "Private address has changed",
        "Reconcile the reviewed private HTTPS mappings.", "serve",
        "configure-serve", "Repair private sharing", "Observed Serve mappings differ from the closed topology."),
    "PUBLIC_FUNNEL_ENABLED": _problem(
        "PUBLIC_FUNNEL_ENABLED", "security", "error", "Public access must be disabled",
        "Disable public Funnel exposure before continuing.", "serve",
        "disable-funnel", "Disable public access", "A public Funnel mapping was observed."),
    "FUNNEL_MUST_BE_DISABLED": _problem(
        "FUNNEL_MUST_BE_DISABLED", "security", "error", "Public access must be disabled",
        "Disable public Funnel exposure before continuing.", "serve",
        "disable-funnel", "Disable public access", "A public Funnel mapping was observed."),
    "STREAM_INTERRUPTED": _problem(
        "STREAM_INTERRUPTED", "upstream", "warning", "Response stream was interrupted",
        "Retry once; if it repeats, check the model and private API status.", "gateway",
        "retry-stream", "Retry response", "The upstream stream ended before normal completion."),
    "UPSTREAM_TIMEOUT": _problem(
        "UPSTREAM_TIMEOUT", "upstream", "error", "Model response timed out",
        "Reduce the request or use a smaller model, then retry.", "gateway",
        "reduce-request", "Reduce request", "The bounded upstream deadline expired."),
    "UPSTREAM_FAILED": _problem(
        "UPSTREAM_FAILED", "upstream", "error", "Model request failed",
        "Check the selected model, then retry.", "gateway",
        "check-model", "Check model", "The upstream request failed without exposing internal details."),
    "CLIENT_VERIFICATION_STALE": _problem(
        "CLIENT_VERIFICATION_STALE", "verification", "warning", "Connection check is stale",
        "Run the guided connection check again.", "connections",
        "test-client", "Run connection check", "The prior client evidence exceeded its freshness bound."),
    "CLIENT_VERIFICATION_FAILED": _problem(
        "CLIENT_VERIFICATION_FAILED", "verification", "error", "Connection check failed",
        "Review the failed check, fix it, and retry.", "connections",
        "test-client", "Retry connection check", "A required client probe failed."),
    "CLIENT_VERIFICATION_INVALIDATED": _problem(
        "CLIENT_VERIFICATION_INVALIDATED", "verification", "warning", "Connection changed",
        "A dependency changed; run the connection check again.", "connections",
        "test-client", "Run connection check", "The prior evidence no longer matches dependency identity."),
    "RECOVERY_REQUIRED": _problem(
        "RECOVERY_REQUIRED", "recovery", "error", "Recovery needs attention",
        "Open Activity and complete the guided recovery action.", "operations",
        "view-activity", "View Activity", "A durable operation could not prove a safe terminal state."),
    "OPERATION_DEADLINE_EXCEEDED": _problem(
        "OPERATION_DEADLINE_EXCEEDED", "operations", "error",
        "Task is taking too long",
        "Open Activity to review the current phase and its safe recovery action.",
        "operations", "view-activity", "View Activity",
        "The named finite operation deadline expired without a durable checkpoint."),
    "OPERATION_FAILED": _problem(
        "OPERATION_FAILED", "operations", "error", "Task stopped safely",
        "Open Activity to review the recorded problem and safe next action.",
        "operations", "view-activity", "View Activity",
        "A durable operation reached a safe failure state without a public failure code."),
    "GATEWAY_INTERNAL": _problem(
        "GATEWAY_INTERNAL", "internal", "error", "Private API request failed",
        "Retry once; if it repeats, open Activity for redacted details.", "gateway",
        "retry-request", "Retry request", "The gateway failed closed on an internal adapter error."),
}


UNKNOWN_PROBLEM = _problem(
    "UNKNOWN_PROBLEM", "unknown", "error", "Action could not be completed",
    "Open Activity for redacted details and a safe next action.", "application",
    "view-activity", "View Activity", "An unrecognized problem code was reported.")


def problem_detail(
    code: str,
    *,
    request_id: str | None = None,
) -> ProblemDetail:
    template = PROBLEM_CATALOG.get(str(code), UNKNOWN_PROBLEM)
    safe_request_id = (
        request_id if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id)
        else None
    )
    return replace(template, request_id=safe_request_id)


def openai_error(problem: ProblemDetail) -> dict[str, Any]:
    type_name = {
        "authentication": "authentication_error",
        "authorization": "permission_error",
        "capacity": "rate_limit_error",
        "request": "invalid_request_error",
        "compatibility": "invalid_request_error",
    }.get(problem.category, "server_error")
    return {
        "error": {
            "message": problem.user_message,
            "type": type_name,
            "param": None,
            "code": problem.code,
        },
        "request_id": problem.request_id,
    }


__all__ = [
    "MAX_TECHNICAL_SUMMARY", "PROBLEM_CATALOG", "ProblemDetail",
    "openai_error", "problem_detail",
]
