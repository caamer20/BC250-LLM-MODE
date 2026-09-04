"""P7 §13.1/§13.2: chat request lifecycle contract (pure, no I/O).

Every chat request gets a request ID + conversation ID, a BOUNDED
connect/read/write/total deadline (never ``timeout=None``), a cancellation
token, explicit prompt/generation token caps, and a terminal result
classification. A redacted local event record never stores prompt/completion
content. The retry policy is pure: never retry after tokens are emitted, and
retry only pre-response transient connection failures at most once.

This module is the SHARED request/result/error semantics for both the terminal
and desktop chat clients (P7 exit gate). It is pure — no tkinter, no HTTP, no
database — and headless-tested.
"""

from __future__ import annotations

import threading
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

CHAT_REQUEST_SCHEMA_VERSION = 1


class ChatResultClassification(str, Enum):
    """Closed terminal result vocabulary (P7 §13.1)."""

    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    SERVER_UNAVAILABLE = "SERVER_UNAVAILABLE"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    THERMAL_STOP = "THERMAL_STOP"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    UNKNOWN = "UNKNOWN"


CLASSIFICATIONS: tuple[str, ...] = tuple(c.value for c in ChatResultClassification)

# Classifications that are transient and retryable BEFORE any token is emitted.
_TRANSIENT_PRE_RESPONSE = frozenset({
    ChatResultClassification.SERVER_UNAVAILABLE,
    ChatResultClassification.TIMEOUT,
})


@dataclass(frozen=True)
class ChatDeadline:
    """Bounded per-operation deadlines in seconds. Never None (P7 §13.1)."""

    connect_s: float = 10.0
    read_s: float = 120.0
    write_s: float = 30.0
    total_s: float = 600.0

    def __post_init__(self) -> None:
        for name in ("connect_s", "read_s", "write_s", "total_s"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
                raise ValueError(f"{name} must be a positive bound, got {value!r}")

    def to_dict(self) -> dict[str, float]:
        return {
            "connect_s": self.connect_s,
            "read_s": self.read_s,
            "write_s": self.write_s,
            "total_s": self.total_s,
        }


DEFAULT_CHAT_DEADLINE = ChatDeadline()


@dataclass(frozen=True)
class ChatRequest:
    """One bounded chat request (P7 §13.1)."""

    request_id: str
    conversation_id: str
    deadline: ChatDeadline = DEFAULT_CHAT_DEADLINE
    max_prompt_tokens: int = 8192
    max_generated_tokens: int = 2048

    def __post_init__(self) -> None:
        if not self.request_id or not isinstance(self.request_id, str):
            raise ValueError("request_id must be a non-empty string")
        if not self.conversation_id or not isinstance(self.conversation_id, str):
            raise ValueError("conversation_id must be a non-empty string")
        if self.max_prompt_tokens <= 0 or self.max_generated_tokens <= 0:
            raise ValueError("token caps must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CHAT_REQUEST_SCHEMA_VERSION,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "deadline": self.deadline.to_dict(),
            "max_prompt_tokens": self.max_prompt_tokens,
            "max_generated_tokens": self.max_generated_tokens,
        }


class ChatCancellation:
    """Thread-safe cancellation token (Ctrl-C / window close / Stop)."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._interrupt = None
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            interrupt = self._interrupt
        if interrupt is not None:
            interrupt()

    def bind_interrupt(self, callback) -> None:
        with self._lock:
            self._interrupt = callback
        if callback is not None and self.is_cancelled:
            callback()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise ChatCancelled("chat request cancelled")


class ChatCancelled(BaseException):
    """Cancellation sentinel (BaseException: not an error to compensate)."""


def classify_outcome(
    *,
    cancelled: bool = False,
    timed_out: bool = False,
    server_unavailable: bool = False,
    model_mismatch: bool = False,
    thermal_stop: bool = False,
    malformed: bool = False,
    completed: bool = False,
) -> ChatResultClassification:
    """Deterministic terminal classification (P7 §13.1).

    Precedence: cancelled > thermal > model mismatch > timeout > server
    unavailable > malformed > completed > unknown.
    """
    if cancelled:
        return ChatResultClassification.CANCELLED
    if thermal_stop:
        return ChatResultClassification.THERMAL_STOP
    if model_mismatch:
        return ChatResultClassification.MODEL_MISMATCH
    if timed_out:
        return ChatResultClassification.TIMEOUT
    if server_unavailable:
        return ChatResultClassification.SERVER_UNAVAILABLE
    if malformed:
        return ChatResultClassification.MALFORMED_RESPONSE
    if completed:
        return ChatResultClassification.COMPLETED
    return ChatResultClassification.UNKNOWN


def classify_exception(exc: BaseException) -> ChatResultClassification:
    """Classify a raised transport error WITHOUT importing httpx (pure).

    Duck-types on the exception class name so the contract module stays
    dependency-free while still mapping real httpx/stdlib errors.
    """
    if isinstance(exc, ChatCancelled):
        return ChatResultClassification.CANCELLED
    name = type(exc).__name__
    lowered = name.lower()
    if "timeout" in lowered or isinstance(exc, TimeoutError):
        return ChatResultClassification.TIMEOUT
    if "connect" in lowered or "network" in lowered or "unavailable" in lowered:
        return ChatResultClassification.SERVER_UNAVAILABLE
    if isinstance(exc, ConnectionError):
        return ChatResultClassification.SERVER_UNAVAILABLE
    if "decode" in lowered or "json" in lowered or "malformed" in lowered:
        return ChatResultClassification.MALFORMED_RESPONSE
    return ChatResultClassification.UNKNOWN


def should_retry(
    classification: ChatResultClassification,
    *,
    tokens_emitted: int = 0,
    attempts: int = 1,
    max_retries: int = 1,
) -> bool:
    """P7 §13.2: never retry after tokens are emitted; retry only pre-response
    transient failures, at most ``max_retries`` times (default once)."""
    if tokens_emitted > 0:
        return False
    if classification not in _TRANSIENT_PRE_RESPONSE:
        return False
    return attempts <= max_retries


@dataclass(frozen=True)
class ChatEventRecord:
    """Redacted local event/metric record (P7 §13.1).

    NEVER stores prompt or completion content — only identifiers, the terminal
    classification, and bounded performance metrics.
    """

    request_id: str
    conversation_id: str
    classification: str
    duration_ms: int = 0
    time_to_first_token_ms: int | None = None
    tokens_generated: int = 0
    prompt_tokens: int = 0
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(
                f"classification {self.classification!r} not in closed vocabulary"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CHAT_REQUEST_SCHEMA_VERSION,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "classification": self.classification,
            "duration_ms": self.duration_ms,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "tokens_generated": self.tokens_generated,
            "prompt_tokens": self.prompt_tokens,
            "error_code": self.error_code,
        }


_RECOVERABLE_MESSAGES: dict[str, str] = {
    ChatResultClassification.TIMEOUT.value: (
        "The request timed out. The server may be busy or the model may be "
        "loading. Try again in a moment."
    ),
    ChatResultClassification.SERVER_UNAVAILABLE.value: (
        "The model server is not reachable. Check the service status and retry."
    ),
    ChatResultClassification.THERMAL_STOP.value: (
        "Generation stopped because the thermal latch engaged. See Activity/"
        "Health before retrying."
    ),
    ChatResultClassification.MODEL_MISMATCH.value: (
        "The active model changed since this request started. Re-send the "
        "message to use the current model."
    ),
    ChatResultClassification.MALFORMED_RESPONSE.value: (
        "The server returned an unexpected response shape. Retry, and report "
        "the request ID if it persists."
    ),
    ChatResultClassification.CANCELLED.value: "The request was cancelled.",
    ChatResultClassification.UNKNOWN.value: (
        "The request did not complete for an unknown reason. Retry, and "
        "report the request ID if it persists."
    ),
}


def recoverable_message(
    classification: ChatResultClassification | str, request_id: str
) -> str:
    """A recoverable user-facing message with the request ID — never a
    traceback (P7 §13.1)."""
    key = (
        classification.value
        if isinstance(classification, ChatResultClassification)
        else str(classification)
    )
    base = _RECOVERABLE_MESSAGES.get(key, _RECOVERABLE_MESSAGES["UNKNOWN"])
    if key == ChatResultClassification.COMPLETED.value:
        return "The request completed."
    return f"{base} (request {request_id})"
