"""Shared bounded chat transport and observation services.

Both the terminal and native clients use this module for HTTP/SSE parsing,
deadlines, cancellation, retry classification, and redacted request metrics.
Prompt and completion text stays only in caller-owned memory/conversation
files; it is never logged or written to operation history.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import httpx

from .chat_lifecycle import (
    DEFAULT_CHAT_DEADLINE,
    ChatCancellation,
    ChatCancelled,
    ChatDeadline,
    ChatEventRecord,
    ChatRequest,
    ChatResultClassification,
    classify_exception,
    recoverable_message,
    should_retry,
)

MAX_CHAT_MESSAGES = 500
MAX_CHAT_MESSAGE_BYTES = 256 * 1024


@dataclass(frozen=True)
class ChatStreamResult:
    request_id: str
    conversation_id: str
    classification: ChatResultClassification
    text: str
    chunks: int
    duration_ms: int
    time_to_first_token_ms: int | None
    tokens_generated: int
    prompt_tokens: int
    attempts: int
    timings: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.classification is ChatResultClassification.COMPLETED

    @property
    def message(self) -> str:
        return recoverable_message(self.classification, self.request_id)

    def event_record(self) -> ChatEventRecord:
        """Return content-free metrics suitable for an optional local sink."""
        return ChatEventRecord(
            request_id=self.request_id,
            conversation_id=self.conversation_id,
            classification=self.classification.value,
            duration_ms=self.duration_ms,
            time_to_first_token_ms=self.time_to_first_token_ms,
            tokens_generated=self.tokens_generated,
            prompt_tokens=self.prompt_tokens,
            error_code=self.error_code,
        )


class ChatTransportError(RuntimeError):
    """Compatibility exception carrying a closed, recoverable result."""

    def __init__(self, result: ChatStreamResult) -> None:
        super().__init__(result.message)
        self.result = result


def estimate_message_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    return sum(len(str(message.get("content") or "")) // 4 + 4 for message in messages)


def trim_messages(
    messages: Sequence[Mapping[str, str]], token_budget: int, *, reserve: int = 512
) -> list[dict[str, str]]:
    if token_budget <= reserve:
        raise ValueError("context token budget must exceed the response reserve")
    trimmed = [dict(message) for message in messages[-MAX_CHAT_MESSAGES:]]
    while len(trimmed) > 2 and estimate_message_tokens(trimmed) + reserve > token_budget:
        drop = 2 if trimmed[0].get("role") == "user" else 1
        trimmed = trimmed[drop:]
    return trimmed


def _validated_messages(
    messages: Sequence[Mapping[str, str]], request: ChatRequest
) -> list[dict[str, str]]:
    if not messages or len(messages) > MAX_CHAT_MESSAGES:
        raise ValueError(f"messages must contain 1..{MAX_CHAT_MESSAGES} entries")
    checked: list[dict[str, str]] = []
    for item in messages:
        role = item.get("role")
        content = item.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise ValueError("chat messages require a supported role and string content")
        if len(content.encode("utf-8")) > MAX_CHAT_MESSAGE_BYTES:
            raise ValueError("one chat message exceeds the 256 KiB bound")
        checked.append({"role": str(role), "content": content})
    if estimate_message_tokens(checked) > request.max_prompt_tokens:
        raise ValueError("chat prompt exceeds the request token cap")
    return checked


class ChatSessionService:
    """One OpenAI-compatible local SSE policy for every frontend."""

    def __init__(
        self,
        *,
        http_client: Any = httpx,
        clock: Callable[[], float] = time.monotonic,
        request_ids: Callable[[], str] | None = None,
        thermal_ok: Callable[[], bool] | None = None,
        active_model: Callable[[], str | None] | None = None,
    ) -> None:
        self._http = http_client
        self._clock = clock
        self._request_ids = request_ids or (lambda: f"chat-{uuid.uuid4().hex[:12]}")
        self._thermal_ok = thermal_ok
        self._active_model = active_model

    @staticmethod
    def _timeout(deadline: ChatDeadline) -> httpx.Timeout:
        return httpx.Timeout(
            deadline.read_s,
            connect=deadline.connect_s,
            write=deadline.write_s,
            read=deadline.read_s,
        )

    def stream(
        self,
        state: Mapping[str, Any],
        messages: Sequence[Mapping[str, str]],
        on_text: Callable[[str], None],
        *,
        conversation_id: str = "terminal",
        cancellation: ChatCancellation | None = None,
        overrides: Mapping[str, Any] | None = None,
        on_timings: Callable[[dict[str, Any]], None] | None = None,
        request_id: str | None = None,
        deadline: ChatDeadline = DEFAULT_CHAT_DEADLINE,
        max_generated_tokens: int = 2048,
    ) -> ChatStreamResult:
        token = cancellation or ChatCancellation()
        expected_model = str(state.get("current_model") or "local")
        context = int(state.get("current_ctx") or 8192)
        generated_cap = min(max_generated_tokens, max(64, context // 2))
        request = ChatRequest(
            request_id=request_id or self._request_ids(),
            conversation_id=conversation_id,
            deadline=deadline,
            max_prompt_tokens=max(128, context - generated_cap),
            max_generated_tokens=generated_cap,
        )
        checked = _validated_messages(messages, request)
        if self._thermal_ok is not None and not self._thermal_ok():
            return self._result(
                request, ChatResultClassification.THERMAL_STOP, "", 0, 0,
                None, 0, estimate_message_tokens(checked), 1,
                error_code="THERMAL_LATCH",
            )

        payload: dict[str, Any] = {
            "model": expected_model,
            "messages": checked,
            "stream": True,
            "cache_prompt": True,
            "max_tokens": generated_cap,
        }
        if overrides:
            payload.update({key: value for key, value in overrides.items() if value is not None})
        port = int(state.get("server_port") or 8080)
        if not 1 <= port <= 65535:
            raise ValueError("server_port must be within 1..65535")
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        start = self._clock()
        prompt_tokens = estimate_message_tokens(checked)
        attempts = 0
        while attempts < 2:
            attempts += 1
            chunks: list[str] = []
            timings: dict[str, Any] = {}
            first_ms: int | None = None
            malformed = False
            try:
                token.raise_if_cancelled()
                with self._http.stream(
                    "POST", url, json=payload, timeout=self._timeout(deadline)
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        token.raise_if_cancelled()
                        elapsed = self._clock() - start
                        if elapsed > deadline.total_s:
                            raise TimeoutError("chat total deadline exceeded")
                        if self._thermal_ok is not None and not self._thermal_ok():
                            return self._result(
                                request, ChatResultClassification.THERMAL_STOP,
                                "".join(chunks), len(chunks), elapsed, first_ms,
                                _token_count(chunks), prompt_tokens, attempts,
                                timings, "THERMAL_LATCH",
                            )
                        if self._active_model is not None:
                            observed = self._active_model()
                            if observed and str(observed) != expected_model:
                                return self._result(
                                    request, ChatResultClassification.MODEL_MISMATCH,
                                    "".join(chunks), len(chunks), elapsed, first_ms,
                                    _token_count(chunks), prompt_tokens, attempts,
                                    timings, "ACTIVE_MODEL_CHANGED",
                                )
                        if not isinstance(line, str) or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                            choices = event["choices"]
                            delta = choices[0]["delta"]
                            text = delta.get("content") or ""
                        except (TypeError, KeyError, IndexError, json.JSONDecodeError):
                            malformed = True
                            continue
                        if isinstance(event.get("timings"), dict):
                            timings = dict(event["timings"])
                            if on_timings is not None:
                                on_timings(dict(timings))
                        response_model = event.get("model")
                        if response_model and str(response_model) != expected_model:
                            return self._result(
                                request, ChatResultClassification.MODEL_MISMATCH,
                                "".join(chunks), len(chunks), elapsed, first_ms,
                                _token_count(chunks), prompt_tokens, attempts,
                                timings, "RESPONSE_MODEL_MISMATCH",
                            )
                        if text:
                            if first_ms is None:
                                first_ms = int(elapsed * 1000)
                            chunks.append(str(text))
                            on_text(str(text))
                elapsed = self._clock() - start
                if malformed and not chunks:
                    return self._result(
                        request, ChatResultClassification.MALFORMED_RESPONSE,
                        "", 0, elapsed, first_ms, 0, prompt_tokens, attempts,
                        timings, "MALFORMED_SSE",
                    )
                return self._result(
                    request, ChatResultClassification.COMPLETED,
                    "".join(chunks), len(chunks), elapsed, first_ms,
                    _token_count(chunks), prompt_tokens, attempts, timings,
                )
            except ChatCancelled:
                elapsed = self._clock() - start
                return self._result(
                    request, ChatResultClassification.CANCELLED,
                    "".join(chunks), len(chunks), elapsed, first_ms,
                    _token_count(chunks), prompt_tokens, attempts, timings,
                    "CANCELLED",
                )
            except Exception as exc:  # transport errors become closed results
                classification = classify_exception(exc)
                if should_retry(
                    classification,
                    tokens_emitted=_token_count(chunks), attempts=attempts,
                ):
                    continue
                elapsed = self._clock() - start
                return self._result(
                    request, classification, "".join(chunks), len(chunks),
                    elapsed, first_ms, _token_count(chunks), prompt_tokens,
                    attempts, timings, type(exc).__name__,
                )
        raise AssertionError("bounded chat attempt loop escaped")

    @staticmethod
    def _result(
        request: ChatRequest,
        classification: ChatResultClassification,
        text: str,
        chunks: int,
        elapsed_s: float,
        first_ms: int | None,
        tokens: int,
        prompt_tokens: int,
        attempts: int,
        timings: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> ChatStreamResult:
        return ChatStreamResult(
            request_id=request.request_id,
            conversation_id=request.conversation_id,
            classification=classification,
            text=text,
            chunks=chunks,
            duration_ms=max(0, int(elapsed_s * 1000)),
            time_to_first_token_ms=first_ms,
            tokens_generated=tokens,
            prompt_tokens=prompt_tokens,
            attempts=attempts,
            timings=dict(timings or {}),
            error_code=error_code,
        )


def _token_count(chunks: Sequence[str]) -> int:
    text = "".join(chunks)
    return max(1, len(text) // 4) if text else 0


@dataclass(frozen=True)
class ChatObservation:
    model: str | None
    context: int
    slots: int
    ready: bool
    thermal_blocked: bool
    stale: bool
    guidance: str


class ChatObservationService:
    """Read-only chat readiness derived from the shared appliance snapshot."""

    def __init__(self, *, state_supplier, home, runtime, live_server=None) -> None:
        self._state_supplier = state_supplier
        self._home = home
        self._runtime = runtime
        self._live_server = live_server

    def current(self) -> ChatObservation:
        state = self._state_supplier()
        profile = self._runtime.current()
        home = self._home.snapshot().to_dict()
        cards = home.get("cards") if isinstance(home.get("cards"), Mapping) else {}
        inference = cards.get("inference") if isinstance(cards.get("inference"), Mapping) else {}
        thermal = cards.get("thermal") if isinstance(cards.get("thermal"), Mapping) else {}

        def health(card: Mapping[str, Any]) -> str:
            item = card.get("health") if isinstance(card.get("health"), Mapping) else {}
            return str(item.get("effective_state") or item.get("state") or "UNVERIFIED")

        thermal_blocked = health(thermal) in {"BLOCKED", "RECOVERY_REQUIRED", "REPAIR_REQUIRED"}
        live: Mapping[str, Any] = {}
        if self._live_server is not None:
            try:
                observed = self._live_server(state)
                if isinstance(observed, Mapping):
                    live = observed
            except Exception:  # noqa: BLE001 - a failed probe is not readiness
                live = {}
        live_ready = bool(
            live.get("healthy") and live.get("model_matches_desired")
        )
        durable_ready = health(inference) in {"READY", "DEGRADED"}
        stale = bool(
            thermal.get("stale")
            or (inference.get("stale") and not live_ready)
        )
        ready = (durable_ready or live_ready) and not stale and not thermal_blocked
        if thermal_blocked:
            guidance = "Generation is blocked by thermal safety. Open System."
        elif stale:
            guidance = "Chat readiness evidence is stale. Refresh System checks."
        elif not ready:
            guidance = "The model server is not verified. Start a model from Models."
        else:
            guidance = "Local model endpoint is verified and ready."
        return ChatObservation(
            model=str(
                live.get("model_id")
                or profile.get("model_alias")
                or state.get("current_model")
                or ""
            ) or None,
            context=int(profile.get("context") or state.get("current_ctx") or 8192),
            slots=int(profile.get("slots") or (state.get("optimizations") or {}).get("parallel_slots") or 1),
            ready=ready,
            thermal_blocked=thermal_blocked,
            stale=stale,
            guidance=guidance,
        )


__all__ = [
    "ChatObservation", "ChatObservationService", "ChatSessionService",
    "ChatStreamResult", "ChatTransportError", "estimate_message_tokens",
    "trim_messages",
]
