"""P7 §13.3: conversation UX presentation contract (pure, no I/O).

Headless-testable presentation semantics for the conversation list: the
model/context/slot profile indicator, a clear "active model changed since the
last message" signal, rename/archive/delete confirmation copy with a recovery
policy, copy/export with a privacy warning + optional redaction, and bounded
search. Local-only defaults are preserved: nothing here syncs to the cloud.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONVERSATION_UX_SCHEMA_VERSION = 1

_MAX_SEARCH_RESULTS = 50
_MAX_EXPORT_MESSAGES = 500
_REDACTED = "[REDACTED]"


def model_changed_since_last_message(
    last_message_model: str | None, active_model: str | None
) -> bool:
    """True when the active model differs from the model that produced the
    last assistant message (P7 §13.3)."""
    if last_message_model is None:
        return False
    return last_message_model != active_model


def model_change_notice(
    last_message_model: str | None, active_model: str | None
) -> str | None:
    if not model_changed_since_last_message(last_message_model, active_model):
        return None
    return (
        f"The active model changed since the last message "
        f"({last_message_model!r} -> {active_model!r}). Re-send to use the "
        f"current model."
    )


def profile_indicator(
    *, model: str | None, context: int | None, slots: int | None
) -> str:
    """Compact model/context/slot profile label for a conversation."""
    parts = []
    if model:
        parts.append(str(model))
    if context:
        parts.append(f"ctx {context}")
    if slots:
        parts.append(f"slots {slots}")
    return " · ".join(parts) if parts else "no profile"


def export_privacy_warning() -> str:
    return (
        "Exporting a conversation copies your prompt and completion text. "
        "Conversations are stored locally only; sharing an export may reveal "
        "sensitive content. Consider redaction before sharing."
    )


def redact_conversation_for_export(
    messages: list[dict[str, str]], *, redact: bool = True
) -> list[dict[str, str]]:
    """Bounded, optionally-redacted export copy (P7 §13.3).

    With ``redact`` True the message CONTENT is replaced; roles and order are
    preserved so the structure stays readable. Never stores prompt content in
    the redacted output.
    """
    bounded = messages[:_MAX_EXPORT_MESSAGES]
    if not redact:
        return [dict(m) for m in bounded]
    return [
        {"role": m.get("role", "user"), "content": _REDACTED} for m in bounded
    ]


def conversation_action_confirmation(action: str) -> dict[str, Any]:
    """Explicit confirmation copy + recovery policy for mutating actions."""
    policies = {
        "rename": {
            "prompt": "Rename this conversation?",
            "recovery": "The previous name is kept in the conversation "
                        "metadata and can be restored.",
            "destructive": False,
        },
        "archive": {
            "prompt": "Archive this conversation? It will be hidden from "
                      "the default list.",
            "recovery": "Archived conversations can be unarchived at any "
                        "time; nothing is deleted.",
            "destructive": False,
        },
        "delete": {
            "prompt": "Permanently delete this conversation? This cannot be "
                      "undone.",
            "recovery": "None — deletion is permanent once confirmed.",
            "destructive": True,
        },
    }
    if action not in policies:
        raise ValueError(f"unknown conversation action: {action!r}")
    return {"action": action, **policies[action]}


def bounded_search(
    conversations: list[dict[str, Any]], query: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Bounded case-insensitive title search (P7 §13.3)."""
    if limit <= 0:
        return []
    limit = min(limit, _MAX_SEARCH_RESULTS)
    needle = query.strip().lower()
    if not needle:
        return list(conversations[:limit])
    matches = []
    for convo in conversations:
        title = str(convo.get("title") or convo.get("name") or "").lower()
        if needle in title:
            matches.append(convo)
            if len(matches) >= limit:
                break
    return matches


def streaming_status_text(
    *, tokens_emitted: int, elapsed_s: float, first_token_ms: int | None
) -> str:
    """Accessible streaming status line (bounded performance indicator)."""
    if tokens_emitted <= 0:
        return "waiting for first token…"
    rate = tokens_emitted / elapsed_s if elapsed_s > 0 else 0.0
    ttft = f" · first token {first_token_ms} ms" if first_token_ms else ""
    return f"{tokens_emitted} tokens · {rate:.1f} tok/s{ttft}"
