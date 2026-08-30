"""Payload validation for durable operations (ADR 002 §11).

Everything that reaches the operations tables passes through here first:

- secret-like keys are REJECTED (not stripped) — a redaction bug must fail
  loudly, never leak;
- structure is bounded (depth, key count, string length);
- JSON is encoded canonically so byte-comparisons are stable;
- per-use size budgets are enforced before persistence.

No raw exception text or subprocess output ever belongs in these payloads;
callers pass bounded, human-readable summaries and stable codes.
"""

from __future__ import annotations

import json
from typing import Any

from .model import OperationType, OperationValidationError

# Secret markers: any mapping key containing one of these (case-insensitive)
# causes rejection of the whole payload.
SECRET_NAME_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "private_key",
)

MAX_DEPTH = 8
MAX_KEYS_PER_OBJECT = 64
MAX_STRING_CHARS = 4096
TRUNCATION_MARKER = "…[truncated]"

REQUEST_MAX_BYTES = 64 * 1024          # sanitized request payload budget
EVENT_DETAIL_MAX_BYTES = 16 * 1024     # event detail / error detail budget
SUMMARY_MAX_CHARS = 512                # human-readable summary lines

KNOWN_REQUEST_VERSIONS: dict[OperationType, int] = {
    OperationType.MODEL_ACTIVATE: 1,
    OperationType.MODEL_ACQUIRE: 1,
    OperationType.MODEL_IMPORT: 1,
    OperationType.MODEL_REMOVE: 1,
    OperationType.MODEL_CONVERT: 1,
    OperationType.RUNTIME_UPDATE: 1,
    OperationType.RUNTIME_ROLLBACK: 1,
    OperationType.BACKUP_CREATE: 1,
    OperationType.BACKUP_RESTORE: 1,
    OperationType.PROFILE_CALIBRATE: 1,
}

EVENT_LEVELS = ("debug", "info", "warn", "error")


def canonical_dumps(value: Any) -> str:
    """Canonical JSON encoding: sorted keys, compact separators."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _reject_secret_key(key: str) -> None:
    lowered = key.lower()
    if any(marker in lowered for marker in SECRET_NAME_MARKERS):
        raise OperationValidationError(
            f"refusing to persist secret-like key: {key!r}"
        )


def _sanitize(value: Any, *, depth: int, budget: "_Budget") -> Any:
    if depth > MAX_DEPTH:
        raise OperationValidationError(
            f"payload nesting exceeds {MAX_DEPTH} levels"
        )
    if isinstance(value, dict):
        if len(value) > MAX_KEYS_PER_OBJECT:
            raise OperationValidationError(
                f"object has {len(value)} keys; maximum is {MAX_KEYS_PER_OBJECT}"
            )
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key = str(key)
            _reject_secret_key(key)
            cleaned[key] = _sanitize(item, depth=depth + 1, budget=budget)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [
            _sanitize(item, depth=depth + 1, budget=budget)
            for item in value
        ]
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            keep = MAX_STRING_CHARS - len(TRUNCATION_MARKER)
            value = value[:keep] + TRUNCATION_MARKER
        return value
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    # Anything else (objects, bytes, ...) has no business in a payload.
    raise OperationValidationError(
        f"unsupported payload type: {type(value).__name__}"
    )


class _Budget:
    """Encoded-size budget enforced on the canonical encoding."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes

    def check(self, encoded: str) -> str:
        size = len(encoded.encode("utf-8"))
        if size > self.max_bytes:
            raise OperationValidationError(
                f"payload encodes to {size} bytes; maximum is {self.max_bytes}"
            )
        return encoded


def sanitize_payload(
    value: Any,
    *,
    max_bytes: int = REQUEST_MAX_BYTES,
) -> str:
    """Validate and canonically encode a payload within ``max_bytes``.

    Returns the canonical JSON string ready for storage.
    """
    budget = _Budget(max_bytes)
    cleaned = _sanitize(value, depth=0, budget=budget)
    return budget.check(canonical_dumps(cleaned))


def sanitize_summary(summary: str | None) -> str | None:
    if summary is None:
        return None
    summary = str(summary)
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS]
    return summary


def sanitize_request(
    operation_type: OperationType | str,
    request_version: int,
    request: dict[str, Any],
) -> str:
    """Full request validation: type, version, bounds, canonical encoding."""
    try:
        operation_type = OperationType(operation_type)
    except ValueError:
        raise OperationValidationError(
            f"unknown operation type: {operation_type!r}"
        ) from None
    known = KNOWN_REQUEST_VERSIONS[operation_type]
    if not isinstance(request_version, int) or request_version < 1:
        raise OperationValidationError(
            f"request_version must be a positive integer, got {request_version!r}"
        )
    if request_version != known:
        raise OperationValidationError(
            f"unknown request_version {request_version} for "
            f"{operation_type.value}; known versions: {known}"
        )
    if not isinstance(request, dict):
        raise OperationValidationError("request must be a JSON object")
    return sanitize_payload(request, max_bytes=REQUEST_MAX_BYTES)
