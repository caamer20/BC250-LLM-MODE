"""Atomic local conversation storage shared by terminal and native chat."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .fsops import atomic_write_text, ensure_private_dir, fsync_directory

CONVERSATION_SCHEMA_VERSION = 1
MAX_STORED_MESSAGES = 2000
MAX_STORED_BYTES = 8 * 1024 * 1024
MAX_LIVE_MESSAGES = 500
MAX_LIVE_BYTES = 4 * 1024 * 1024
MAX_CONVERSATION_FILES = 200


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_conversation_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip()) or "default"
    if len(safe) > 96:
        safe = safe[:96]
    if safe in {".", ".."}:
        raise ValueError("invalid conversation identifier")
    return safe


def bounded_live_messages(
    messages: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    retained: list[dict[str, str]] = []
    size = 0
    for message in reversed(messages[-MAX_LIVE_MESSAGES:]):
        content = str(message.get("content") or "")
        encoded = content.encode("utf-8")
        if size + len(encoded) > MAX_LIVE_BYTES:
            break
        retained.append({"role": str(message.get("role") or "user"), "content": content})
        size += len(encoded)
    retained.reverse()
    return tuple(retained)


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    title: str
    archived: bool
    created_at: str
    updated_at: str
    last_model: str | None
    messages: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONVERSATION_SCHEMA_VERSION,
            "conversation_id": self.conversation_id,
            "title": self.title,
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_model": self.last_model,
            "messages": [dict(message) for message in self.messages],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "archived": self.archived,
            "updated_at": self.updated_at,
            "last_model": self.last_model,
            "message_count": len(self.messages),
        }


class ConversationService:
    def __init__(
        self,
        directory: str | Path,
        *,
        clock: Callable[[], str] = _utcnow,
        ids: Callable[[], str] | None = None,
    ) -> None:
        self._directory = Path(directory)
        self._clock = clock
        self._ids = ids or (lambda: f"conversation-{uuid.uuid4().hex[:12]}")

    def _path(self, conversation_id: str) -> Path:
        return self._directory / f"{safe_conversation_id(conversation_id)}.json"

    def create(self, title: str = "New conversation") -> ConversationRecord:
        return self.save(self._ids(), title=title, messages=())

    def save_named(
        self, name: str, messages: Sequence[Mapping[str, str]], *, last_model: str | None = None
    ) -> ConversationRecord:
        return self.save(
            safe_conversation_id(name), title=name.strip() or "default",
            messages=messages, last_model=last_model,
        )

    def save(
        self,
        conversation_id: str,
        *,
        title: str,
        messages: Sequence[Mapping[str, str]],
        archived: bool | None = None,
        last_model: str | None = None,
    ) -> ConversationRecord:
        if not title.strip() or len(title) > 160:
            raise ValueError("conversation title must contain 1..160 characters")
        if len(messages) > MAX_STORED_MESSAGES:
            raise ValueError(f"conversation exceeds {MAX_STORED_MESSAGES} messages")
        checked: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("conversation messages are malformed")
            checked.append({"role": str(role), "content": content})
        path = self._path(conversation_id)
        existing = self.load(conversation_id) if path.exists() else None
        now = self._clock()
        record = ConversationRecord(
            conversation_id=safe_conversation_id(conversation_id),
            title=title.strip(),
            archived=bool(existing.archived if archived is None and existing else archived),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            last_model=last_model if last_model is not None else (existing.last_model if existing else None),
            messages=tuple(checked),
        )
        payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        if len(payload.encode("utf-8")) > MAX_STORED_BYTES:
            raise ValueError("conversation exceeds the 8 MiB local storage bound")
        ensure_private_dir(self._directory)
        atomic_write_text(path, payload, mode=0o600)
        return record

    def load(self, conversation_id: str) -> ConversationRecord:
        path = self._path(conversation_id)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"No saved conversation named {conversation_id!r}") from exc
        if len(raw) > MAX_STORED_BYTES:
            raise ValueError("saved conversation exceeds the supported size")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Saved conversation {path.name} is malformed") from exc
        if isinstance(data, list):
            # Read-only compatibility for the pre-GUI conversation format.
            data = {
                "schema_version": CONVERSATION_SCHEMA_VERSION,
                "conversation_id": path.stem,
                "title": path.stem.replace("_", " "),
                "archived": False,
                "created_at": "",
                "updated_at": "",
                "last_model": None,
                "messages": data,
            }
        return self._decode(data, path)

    @staticmethod
    def _decode(data: Any, path: Path) -> ConversationRecord:
        if not isinstance(data, dict) or data.get("schema_version") != CONVERSATION_SCHEMA_VERSION:
            raise ValueError(f"Saved conversation {path.name} is malformed")
        messages = data.get("messages")
        if not isinstance(messages, list) or len(messages) > MAX_STORED_MESSAGES:
            raise ValueError(f"Saved conversation {path.name} is malformed")
        checked: list[dict[str, str]] = []
        for item in messages:
            if (
                not isinstance(item, dict)
                or item.get("role") not in {"system", "user", "assistant"}
                or not isinstance(item.get("content"), str)
            ):
                raise ValueError(f"Saved conversation {path.name} is malformed")
            checked.append({"role": item["role"], "content": item["content"]})
        conversation_id = data.get("conversation_id")
        title = data.get("title")
        if not isinstance(conversation_id, str) or not isinstance(title, str) or not title:
            raise ValueError(f"Saved conversation {path.name} is malformed")
        return ConversationRecord(
            conversation_id=safe_conversation_id(conversation_id),
            title=title[:160], archived=bool(data.get("archived")),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_model=(str(data["last_model"]) if data.get("last_model") else None),
            messages=tuple(checked),
        )

    def list(self, *, archived: bool = False, query: str = "", limit: int = 50) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 100:
            raise ValueError("conversation list limit must be within 1..100")
        if not self._directory.exists():
            return ()
        rows: list[dict[str, Any]] = []
        needle = query.strip().casefold()
        for path in sorted(self._directory.glob("*.json"))[:MAX_CONVERSATION_FILES]:
            try:
                record = self.load(path.stem)
            except ValueError:
                continue
            if record.archived != archived:
                continue
            if needle and needle not in record.title.casefold():
                continue
            rows.append(record.summary())
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return tuple(rows[:limit])

    def rename(self, conversation_id: str, title: str) -> ConversationRecord:
        record = self.load(conversation_id)
        return self.save(
            record.conversation_id, title=title, messages=record.messages,
            archived=record.archived, last_model=record.last_model,
        )

    def archive(self, conversation_id: str, archived: bool = True) -> ConversationRecord:
        record = self.load(conversation_id)
        return self.save(
            record.conversation_id, title=record.title, messages=record.messages,
            archived=archived, last_model=record.last_model,
        )

    def delete(self, conversation_id: str) -> None:
        path = self._path(conversation_id)
        if not path.exists():
            raise ValueError(f"No saved conversation named {conversation_id!r}")
        path.unlink()
        fsync_directory(self._directory)


__all__ = [
    "ConversationRecord", "ConversationService", "bounded_live_messages",
    "safe_conversation_id",
]
