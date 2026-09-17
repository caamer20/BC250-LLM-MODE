"""Atomic local conversation storage shared by terminal and native chat."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager, closing
from functools import wraps
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .fsops import atomic_write_text, ensure_private_dir, fsync_directory
from .chat_preferences import ConversationOptions
from .document_service import DocumentSource, validate_sources, message_with_sources

CONVERSATION_SCHEMA_VERSION = 2
MAX_STORED_MESSAGES = 2000
MAX_STORED_BYTES = 8 * 1024 * 1024
MAX_LIVE_MESSAGES = 500
MAX_LIVE_BYTES = 4 * 1024 * 1024
MAX_CONVERSATION_FILES = 200
MAX_DRAFT_BYTES = 32 * 1024


class ConversationConflict(ValueError):
    """An explicitly observed conversation revision has changed."""


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        from .profile_access import profile_access, require_writable_profile
        with profile_access(self._directory.parent):
            require_writable_profile(self._directory.parent)
            with profile_access(self._directory, exclusive=True):
                return method(self, *args, **kwargs)
    return call


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
        sources = validate_sources(message.get("sources", ()))
        source_bytes = sum(len(source.text.encode()) for source in sources)
        if size + len(encoded) + source_bytes > MAX_LIVE_BYTES:
            break
        record = {"role": str(message.get("role") or "user"), "content": content}
        if sources:
            record["sources"] = [source.to_dict() for source in sources]
        retained.append(record)
        size += len(encoded) + source_bytes
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
    draft: str = ""
    revision: int = 1
    result_classification: str | None = None
    options: ConversationOptions = ConversationOptions()
    parent_conversation_id: str | None = None
    parent_message_index: int | None = None
    import_source_digest: str | None = None
    import_source_id: str | None = None
    draft_sources: tuple[DocumentSource, ...] = ()

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
            "draft": self.draft,
            "revision": self.revision,
            "result_classification": self.result_classification,
            "options": self.options.to_dict(),
            "parent_conversation_id": self.parent_conversation_id,
            "parent_message_index": self.parent_message_index,
            "import_source_digest": self.import_source_digest,
            "import_source_id": self.import_source_id,
            "draft_sources": [source.to_dict() for source in self.draft_sources],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "archived": self.archived,
            "updated_at": self.updated_at,
            "last_model": self.last_model,
            "message_count": len(self.messages),
            "has_draft": bool(self.draft or self.draft_sources),
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
        self._seen: dict[str, int] = {}

    def _path(self, conversation_id: str) -> Path:
        return self._directory / f"{safe_conversation_id(conversation_id)}.json"

    def create(self, title: str = "New conversation") -> ConversationRecord:
        return self.save(self._ids(), title=title, messages=())

    def save_named(
        self, name: str, messages: Sequence[Mapping[str, str]], *, last_model: str | None = None,
        options: ConversationOptions | None = None,
    ) -> ConversationRecord:
        return self.save(
            safe_conversation_id(name), title=name.strip() or "default",
            messages=messages, last_model=last_model, options=options,
        )

    @_locked
    def save(
        self,
        conversation_id: str,
        *,
        title: str,
        messages: Sequence[Mapping[str, str]],
        archived: bool | None = None,
        last_model: str | None = None,
        draft: str | None = None,
        expected_revision: int | None = None,
        result_classification: str | None = None,
        options: ConversationOptions | None = None,
        parent_conversation_id: str | None = None,
        parent_message_index: int | None = None,
        draft_sources=None,
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
            item = {"role": str(role), "content": content}
            sources = validate_sources(message.get("sources", ()))
            if sources:
                if role != "user":
                    raise ValueError("Documents may be attached only to user messages.")
                item["sources"] = [source.to_dict() for source in sources]
            checked.append(item)
        path = self._path(conversation_id)
        existing = self._load(conversation_id) if path.exists() else None
        if existing is not None and (expected_revision if expected_revision is not None else self._seen.get(conversation_id, existing.revision)) != existing.revision:
            raise ConversationConflict("Conversation changed in another client. Reload before saving; your text is kept in this window.")
        if existing is None and self._directory.exists():
            with os.scandir(self._directory) as entries:
                count = 0
                for entry in entries:
                    if entry.name.endswith(".json"):
                        count += 1
                        if count >= MAX_CONVERSATION_FILES:
                            raise ValueError("Conversation storage is full (200 files). Export and delete a conversation before creating another.")
        draft_value = existing.draft if draft is None and existing else (draft or "")
        if not isinstance(draft_value, str) or len(draft_value.encode("utf-8")) > MAX_DRAFT_BYTES:
            raise ValueError("conversation draft exceeds the 32 KiB storage bound")
        from .chat_lifecycle import CLASSIFICATIONS
        if result_classification is not None and result_classification not in CLASSIFICATIONS:
            raise ValueError("unknown conversation result classification")
        if options is not None and not isinstance(options, ConversationOptions):
            raise ValueError("invalid conversation settings")
        if parent_conversation_id is not None and (safe_conversation_id(parent_conversation_id) != parent_conversation_id
                or type(parent_message_index) is not int or not 0 <= parent_message_index < MAX_STORED_MESSAGES):
            raise ValueError("invalid conversation origin")
        now = self._clock()
        record = ConversationRecord(
            conversation_id=safe_conversation_id(conversation_id),
            title=title.strip(),
            archived=bool(existing.archived if archived is None and existing else archived),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            last_model=last_model if last_model is not None else (existing.last_model if existing else None),
            messages=tuple(checked),
            draft=draft_value,
            revision=existing.revision + 1 if existing else 1,
            result_classification=result_classification or (existing.result_classification if existing else None),
            options=options or (existing.options if existing else ConversationOptions()),
            parent_conversation_id=existing.parent_conversation_id if existing else parent_conversation_id,
            parent_message_index=existing.parent_message_index if existing else parent_message_index,
            import_source_digest=existing.import_source_digest if existing else None,
            import_source_id=existing.import_source_id if existing else None,
            draft_sources=(existing.draft_sources if draft_sources is None and existing
                           else validate_sources(draft_sources or ())),
        )
        payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        if len(payload.encode("utf-8")) > MAX_STORED_BYTES:
            raise ValueError("conversation exceeds the 8 MiB local storage bound")
        ensure_private_dir(self._directory)
        atomic_write_text(path, payload, mode=0o600)
        self._seen[record.conversation_id] = record.revision
        return record

    def load(self, conversation_id: str) -> ConversationRecord:
        record = self._load(conversation_id)
        self._seen[record.conversation_id] = record.revision
        return record

    def _load(self, conversation_id: str) -> ConversationRecord:
        path = self._path(conversation_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                import stat
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("conversation must be a regular file")
                raw = source.read(MAX_STORED_BYTES + 1)
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
                "draft": "",
            }
        return self._decode(data, path)

    @staticmethod
    def _decode(data: Any, path: Path) -> ConversationRecord:
        if not isinstance(data, dict) or data.get("schema_version") not in {1, CONVERSATION_SCHEMA_VERSION}:
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
            message = {"role": item["role"], "content": item["content"]}
            sources = validate_sources(item.get("sources", ()))
            if sources:
                if item["role"] != "user":
                    raise ValueError("Saved documents have an invalid role.")
                message["sources"] = [source.to_dict() for source in sources]
            checked.append(message)
        conversation_id = data.get("conversation_id")
        title = data.get("title")
        draft = data.get("draft", "")
        if not isinstance(conversation_id, str) or not isinstance(title, str) or not title:
            raise ValueError(f"Saved conversation {path.name} is malformed")
        if conversation_id != path.stem or safe_conversation_id(conversation_id) != conversation_id:
            raise ValueError("Saved conversation identity does not match its file.")
        if not isinstance(draft, str) or len(draft.encode("utf-8")) > MAX_DRAFT_BYTES:
            raise ValueError(f"Saved conversation {path.name} is malformed")
        origin = data.get("import_source_digest")
        origin_id = data.get("import_source_id")
        if origin is not None or origin_id is not None:
            if (not isinstance(origin, str) or re.fullmatch(r"[0-9a-f]{64}", origin) is None
                    or not isinstance(origin_id, str) or safe_conversation_id(origin_id) != origin_id):
                raise ValueError("Saved import origin is malformed.")
        from .chat_lifecycle import CLASSIFICATIONS
        return ConversationRecord(
            conversation_id=safe_conversation_id(conversation_id),
            title=title[:160], archived=bool(data.get("archived")),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_model=(str(data["last_model"]) if data.get("last_model") else None),
            messages=tuple(checked),
            draft=draft,
            revision=max(1, int(data.get("revision", 1))),
            result_classification=(data.get("result_classification") if data.get("result_classification") in CLASSIFICATIONS else None),
            options=ConversationOptions.from_dict(data.get("options", {})),
            parent_conversation_id=(data.get("parent_conversation_id")
                                    if isinstance(data.get("parent_conversation_id"), str) else None),
            parent_message_index=(data.get("parent_message_index")
                                  if type(data.get("parent_message_index")) is int else None),
            import_source_digest=origin,
            import_source_id=origin_id,
            draft_sources=validate_sources(data.get("draft_sources", ())),
        )

    @_locked
    def branch(self, conversation_id: str, message_index: int, draft: str, *, expected_revision: int) -> ConversationRecord:
        """Edit a user turn into a new file; the source is never rewritten."""
        record = self.load(conversation_id)
        if record.revision != expected_revision:
            raise ConversationConflict("Conversation changed. Reload before branching; your edit is kept.")
        if type(message_index) is not int or not 0 <= message_index < len(record.messages) or record.messages[message_index]["role"] != "user":
            raise ValueError("Choose a user message to edit.")
        return self.save(self._ids(), title=record.title[:149] + " (branch)",
                         messages=record.messages[:message_index], draft=draft,
                         draft_sources=record.messages[message_index].get("sources", ()),
                         last_model=record.last_model, options=record.options,
                         parent_conversation_id=record.conversation_id,
                         parent_message_index=message_index)

    @_locked
    def from_summary(self, conversation_id: str, summary: str, *, expected_revision: int) -> ConversationRecord:
        record = self.load(conversation_id)
        if record.revision != expected_revision:
            raise ConversationConflict("Conversation changed. Review a fresh summary before continuing.")
        if not summary.strip() or len(summary.encode()) > MAX_DRAFT_BYTES or not record.messages:
            raise ValueError("A summary must contain 1–32 KiB of text from a nonempty conversation.")
        return self.save(self._ids(), title=record.title[:148] + " (summary)",
                         messages=({"role": "user", "content": "Reviewed summary of our earlier conversation:\n\n" + summary},),
                         last_model=record.last_model, options=record.options,
                         parent_conversation_id=record.conversation_id,
                         parent_message_index=len(record.messages) - 1)

    def save_draft(self, conversation_id: str, draft: str, *, expected_revision: int | None = None, sources=None) -> ConversationRecord:
        record = self.load(conversation_id)
        return self.save(
            record.conversation_id,
            title=record.title,
            messages=record.messages,
            archived=record.archived,
            last_model=record.last_model,
            draft=draft, expected_revision=expected_revision,
            draft_sources=sources,
        )

    def export_markdown(
        self,
        conversation_id: str,
        destination: str | Path,
        *,
        redact: bool = False,
    ) -> Path:
        """Write one explicit, bounded local export atomically.

        A redacted export preserves roles/order but never writes prompt or
        completion content. The caller chooses the destination and whether a
        full-content export is appropriate.
        """
        from .conversation_ux import redact_conversation_for_export

        record = self.load(conversation_id)
        if not record.messages:
            raise ValueError("Nothing to export; the conversation is empty")
        messages = []
        for offset in range(0, len(record.messages), 500):
            messages.extend(redact_conversation_for_export(
                [dict(item) for item in record.messages[offset:offset + 500]], redact=redact))
        safe_title = (
            "Redacted conversation" if redact else
            (" ".join(record.title.split())[:160] or "Conversation")
        )
        lines = [f"# BC250 LLM MODE — {safe_title}", ""]
        if record.options.instructions and not redact:
            lines.extend(("## Conversation instructions", "", record.options.instructions, ""))
        for message in messages:
            role = {
                "user": "You", "assistant": "Assistant", "system": "System",
            }.get(message["role"], "Message")
            lines.extend((f"## {role}", "", message_with_sources(message)["content"], ""))
        target = Path(destination)
        atomic_write_text(target, "\n".join(lines), mode=0o600)
        return target

    @_locked
    def list(self, *, archived: bool = False, query: str = "", limit: int = 50, offset: int = 0) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 100:
            raise ValueError("conversation list limit must be within 1..100")
        if not self._directory.exists():
            return ()
        if not 0 <= offset <= 10000:
            raise ValueError("invalid conversation page")
        index = self._directory / ".index.sqlite3"
        # Metadata index lives alongside private conversations, never in the
        # operational database. Existing over-limit histories remain searchable.
        with closing(sqlite3.connect(index)) as conn:
            index.chmod(0o600)
            conn.execute("CREATE TABLE IF NOT EXISTS summaries (id TEXT PRIMARY KEY, stamp TEXT, title TEXT, archived INTEGER, updated TEXT, summary TEXT)")
            seen = set()
            with os.scandir(self._directory) as entries:
                for entry in entries:
                    if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                        continue
                    if len(seen) >= 10000:
                        raise ValueError("History exceeds 10000 files; move a reviewed export to an archive directory before browsing")
                    identifier = entry.name[:-5]
                    seen.add(identifier)
                    st = entry.stat(follow_symlinks=False)
                    stamp = f"{st.st_ino}:{st.st_mtime_ns}:{st.st_size}"
                    old = conn.execute("SELECT stamp FROM summaries WHERE id=?", (identifier,)).fetchone()
                    if old and old[0] == stamp:
                        continue
                    try:
                        record = self._load(identifier)
                    except ValueError:
                        summary = {"conversation_id": identifier, "title": "Needs repair: " + identifier[:96],
                                   "archived": False, "updated_at": "", "last_model": None,
                                   "message_count": 0, "has_draft": False, "invalid": True}
                        conn.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?,?)", (
                            identifier, stamp, summary["title"].casefold(), 0, "", json.dumps(summary)))
                        continue
                    conn.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?,?)", (
                        identifier, stamp, record.title.casefold(), int(record.archived),
                        record.updated_at, json.dumps(record.summary())))
            for (identifier,) in conn.execute("SELECT id FROM summaries").fetchall():
                if identifier not in seen:
                    conn.execute("DELETE FROM summaries WHERE id=?", (identifier,))
            rows = conn.execute("SELECT summary FROM summaries WHERE archived=? AND instr(title,?)>0 ORDER BY updated DESC, id DESC LIMIT ? OFFSET ?",
                                (int(archived), query.strip().casefold(), limit, offset)).fetchall()
            conn.commit()
        return tuple(json.loads(row[0]) for row in rows)

    @_locked
    def rename(self, conversation_id: str, title: str) -> ConversationRecord:
        record = self.load(conversation_id)
        return self.save(
            record.conversation_id, title=title, messages=record.messages,
            archived=record.archived, last_model=record.last_model,
        )

    @_locked
    def archive(self, conversation_id: str, archived: bool = True) -> ConversationRecord:
        record = self.load(conversation_id)
        return self.save(
            record.conversation_id, title=record.title, messages=record.messages,
            archived=archived, last_model=record.last_model,
        )

    @_locked
    def delete(self, conversation_id: str) -> None:
        path = self._path(conversation_id)
        if not path.exists():
            raise ValueError(f"No saved conversation named {conversation_id!r}")
        path.unlink()
        self._seen.pop(conversation_id, None)
        index = self._directory / ".index.sqlite3"
        if index.is_file():
            with closing(sqlite3.connect(index)) as conn:
                conn.execute("PRAGMA secure_delete=ON")
                conn.execute("DELETE FROM summaries WHERE id=?", (safe_conversation_id(conversation_id),))
                conn.commit()
        fsync_directory(self._directory)


__all__ = [
    "ConversationRecord", "ConversationService", "MAX_DRAFT_BYTES",
    "bounded_live_messages",
    "safe_conversation_id",
]
