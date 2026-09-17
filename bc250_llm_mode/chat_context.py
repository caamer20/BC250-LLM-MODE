"""Bounded context selection with explicit, non-destructive history omission.

Model-aware counts are requested only during an explicit preflight, never on
Tk refresh. Failure falls back to an identified local estimate. No text is
written to logs, events, or operational state.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import httpx

from .http_deadline import response_deadline

MAX_MESSAGES = 500
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_COUNT_RESPONSE_BYTES = 8 * 1024 * 1024


def estimate_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    # A displayed estimate, not a guarantee for arbitrary model tokenizers.
    return sum(max(1, (len(m["content"].encode("utf-8")) + 2) // 3) + 12 for m in messages) + 8


@dataclass(frozen=True)
class ContextPlan:
    messages: tuple[dict[str, str], ...]
    included: tuple[int, ...]
    excluded: tuple[int, ...]
    prompt_tokens: int
    prompt_limit: int
    response_tokens: int
    estimated: bool

    @property
    def fits(self):
        return self.prompt_tokens <= self.prompt_limit

    @property
    def description(self):
        count = "Estimated" if self.estimated else "Model-counted"
        exclusion = (
            f" {len(self.excluded)} earlier message(s) will not reach the model; saved history is unchanged."
            if self.excluded else " All conversation messages are included."
        )
        return (f"{count} prompt: {self.prompt_tokens:,} / {self.prompt_limit:,} tokens · "
                f"response allowance: {self.response_tokens:,}.{exclusion}")


def select_context(messages, context: int, reserve: int = 2048, *, counter=None) -> ContextPlan:
    """Keep instructions and the newest complete turns, with source indices.

    A bounded binary search selects a suffix of user turns. System messages
    are pinned regardless of their position. An oversized latest turn is
    returned as not fitting, never silently truncated or split.
    """
    if type(context) is not int or type(reserve) is not int or context <= 0 or reserve <= 0:
        raise ValueError("Context and response allowance must be positive integers.")
    reserve = min(reserve, max(64, context // 2))
    if context <= reserve:
        raise ValueError("Context must leave room for a prompt.")
    items = tuple({"role": m["role"], "content": m["content"]} for m in messages)
    if len(items) > 2010 or any(m["role"] not in {"system", "user", "assistant"} or not isinstance(m["content"], str) for m in items):
        raise ValueError("Conversation exceeds the supported message bounds.")
    pinned = {i for i, m in enumerate(items) if m["role"] == "system"}
    starts = [i for i, m in enumerate(items) if m["role"] == "user"] or [len(items)]
    counter = counter or estimate_tokens
    sizes = tuple(len(m["content"].encode()) for m in items)
    estimated_costs = tuple(max(1, (size + 2) // 3) + 12 for size in sizes)

    def candidate(start):
        indices = tuple(i for i in range(len(items)) if i in pinned or i >= start)
        selected = tuple(items[i] for i in indices)
        bounded = (len(selected) <= MAX_MESSAGES and sum(sizes[i] for i in indices) <= MAX_REQUEST_BYTES
                   and all(sizes[i] <= 256 * 1024 for i in indices))
        count = ((8 + sum(estimated_costs[i] for i in indices)) if counter is estimate_tokens else counter(selected)) if bounded else context + 1
        if type(count) is not int or count < 0:
            raise ValueError("Invalid tokenizer response.")
        return indices, selected, count, bounded

    low, high = 0, len(starts) - 1
    first = candidate(starts[0])
    if first[3] and first[2] <= context - reserve:
        included, selected, count, _ = first
        included_set = set(included)
        return ContextPlan(selected, included, tuple(i for i in range(len(items)) if i not in included_set),
                           count, context - reserve, reserve, counter is estimate_tokens)
    # At most 11 candidates for the 2001-message persisted bound.
    while low < high:
        middle = (low + high) // 2
        _, _, tokens, bounded = candidate(starts[middle])
        if bounded and tokens <= context - reserve:
            high = middle
        else:
            low = middle + 1
    included, selected, count, _ = candidate(starts[low])
    included_set = set(included)
    return ContextPlan(selected, included, tuple(i for i in range(len(items)) if i not in included_set),
                       count, context - reserve, reserve, counter is estimate_tokens)


class ChatContextService:
    def __init__(self, *, http_client=None, clock: Callable[[], float] = time.monotonic):
        self.http = http_client
        self.clock = clock

    def prepare(self, state, messages, *, response_tokens=2048, cancellation=None):
        context = int(state.get("current_ctx") or 8192)
        port = int(state.get("server_port") or 8080)
        if not 1 <= port <= 65535:
            raise ValueError("Invalid local model port.")
        end = self.clock() + 6.0
        from .bounded_json_http import BoundedJSONHTTP
        http = self.http or BoundedJSONHTTP(maximum_bytes=MAX_COUNT_RESPONSE_BYTES, total_seconds=6,
                                           cancellation=cancellation)

        def post(endpoint, payload):
            if cancellation:
                cancellation.raise_if_cancelled()
            remaining = end - self.clock()
            if remaining <= 0:
                raise TimeoutError("Tokenizer deadline exceeded")
            with http.stream("POST", f"http://127.0.0.1:{port}/{endpoint}",
                                  json=payload, timeout=min(remaining, 2.0),
                                  follow_redirects=False, trust_env=False,
                                  headers={"Accept-Encoding": "identity"}) as response:
                with response_deadline(response, max(0.001, end - self.clock())):
                    if cancellation:
                        from .http_deadline import interrupt_response
                        cancellation.bind_interrupt(lambda: interrupt_response(response))
                    try:
                        response.raise_for_status()
                        chunks = bytearray()
                        for chunk in (response.iter_raw() if hasattr(response, "iter_raw") else response.iter_bytes()):
                            if cancellation:
                                cancellation.raise_if_cancelled()
                            chunks.extend(chunk)
                            if len(chunks) > MAX_COUNT_RESPONSE_BYTES or self.clock() >= end:
                                raise ValueError("Tokenizer response exceeds its bounds")
                        data = json.loads(chunks)
                        if not isinstance(data, dict):
                            raise ValueError("Invalid tokenizer response")
                        return data
                    finally:
                        if cancellation:
                            cancellation.bind_interrupt(None)

        cache = {}
        def count(selected):
            key = tuple((m["role"], m["content"]) for m in selected)
            if key in cache:
                return cache[key]
            rendered = post("apply-template", {"messages": list(selected)})
            prompt = rendered.get("prompt")
            if not isinstance(prompt, str) or len(prompt.encode()) > MAX_REQUEST_BYTES:
                raise ValueError("Invalid chat template result")
            result = post("tokenize", {"content": prompt, "add_special": False, "parse_special": True})
            tokens = result.get("tokens")
            if not isinstance(tokens, list) or len(tokens) > 1_000_000 or any(type(t) is not int or t < 0 for t in tokens):
                raise ValueError("Invalid token list")
            cache[key] = len(tokens)
            return len(tokens)

        try:
            return select_context(messages, context, response_tokens, counter=count)
        except (OSError, ValueError, TypeError, httpx.HTTPError):
            if cancellation:
                cancellation.raise_if_cancelled()
            return select_context(messages, context, response_tokens)
