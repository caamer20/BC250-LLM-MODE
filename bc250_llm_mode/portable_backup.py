"""Explicit portable conversation backups with bounded, verified import.

This is separate from machine configuration restore. Archives contain private
conversation files and selected user preferences, never operational authority,
credentials, model bytes, runtime state, or Open WebUI's database. Each imported
conversation is an atomic new file. Content-bound IDs make an interrupted
import safely resumable without overwriting an existing conversation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .chat_preferences import PromptTemplateService, MAX_TEMPLATE_FILE_BYTES
from .conversation_service import ConversationService, MAX_STORED_BYTES, MAX_CONVERSATION_FILES
from .fsops import atomic_write_text, ensure_private_dir, fsync_directory
from .profile_access import profile_access, require_writable_profile
from .services import UserPreferencesService

PORTABLE_VERSION = 1
MAX_PORTABLE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
PORTABLE_PREFERENCES = ("appearance", "ui_scale_percent", "reduced_motion")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_preferences(value):
    if not isinstance(value, dict) or set(value) - set(PORTABLE_PREFERENCES):
        raise ValueError("Portable settings may contain only appearance, interface scale and reduced motion.")
    checked = UserPreferencesService.validate(value)
    return {key: checked[key] for key in value}


@dataclass(frozen=True)
class PortablePreview:
    digest: str
    size_bytes: int
    conversations: tuple[dict, ...]
    preferences: dict
    templates: tuple[str, ...]


@dataclass(frozen=True)
class PortableImportResult:
    imported: int
    already_present: int
    remaining: int
    preferences_applied: bool = False
    templates_imported: int = 0
    complete: bool = False


def inspect_portable(path: Path, *, staging: Path | None = None) -> PortablePreview:
    """Read a held descriptor, validate every byte before publishing anything.

    Only uncompressed USTAR with a first manifest and exact ordered members is
    supported. No path extraction, links, PAX overrides, compression, or sparse
    members. Staging is a fresh private directory owned by the caller.
    """
    end = time.monotonic() + 60
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 1024 <= before.st_size <= MAX_PORTABLE_BYTES:
                raise ValueError("Invalid portable archive size or type.")
            digest = hashlib.sha256()
            def read(size):
                if not 0 <= size <= MAX_STORED_BYTES or time.monotonic() > end:
                    raise ValueError("Portable archive exceeds inspection bounds.")
                data = source.read(size)
                if len(data) != size:
                    raise ValueError("Portable archive is truncated.")
                digest.update(data)
                return data
            def member():
                header = read(512)
                if not any(header):
                    return None
                info = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                if info.type not in {tarfile.REGTYPE, tarfile.AREGTYPE} or info.linkname or info.sparse:
                    raise ValueError("Portable archive contains unsupported members.")
                if not 0 < info.size <= MAX_STORED_BYTES:
                    raise ValueError("Portable member exceeds its size limit.")
                return info
            first = member()
            if first is None or first.name != "manifest.json" or first.size > MAX_MANIFEST_BYTES:
                raise ValueError("Portable archive must begin with its bounded manifest.")
            manifest = json.loads(read(first.size))
            if any(read((-first.size) % 512)):
                raise ValueError("Invalid portable archive padding.")
            if (not isinstance(manifest, dict) or set(manifest) != {"format", "version", "created_at", "files"}
                    or manifest["format"] != "bc250-portable" or manifest["version"] != PORTABLE_VERSION
                    or not isinstance(manifest["created_at"], str) or len(manifest["created_at"]) > 40):
                raise ValueError("Unsupported portable archive manifest.")
            files = manifest["files"]
            if not isinstance(files, list) or not 1 <= len(files) <= MAX_CONVERSATION_FILES + 2:
                raise ValueError("Portable archive has too many members.")
            seen, original_ids, conversations = set(), set(), []
            preferences, templates = {}, {}
            for index, entry in enumerate(files):
                if (not isinstance(entry, dict) or set(entry) != {"name", "size", "sha256"}
                        or not isinstance(entry["name"], str) or type(entry["size"]) is not int
                        or not isinstance(entry["sha256"], str) or not _HEX.fullmatch(entry["sha256"])):
                    raise ValueError("Malformed portable inventory.")
                name = entry["name"]
                if (name in seen or not (re.fullmatch(r"conversation-[0-9]{4}\.json", name)
                                        or name in {"preferences.json", "templates.json"})):
                    raise ValueError("Unexpected or duplicate portable member.")
                seen.add(name)
                info = member()
                if info is None or info.name != name or info.size != entry["size"]:
                    raise ValueError("Portable inventory does not match its members.")
                if name == "preferences.json" and info.size > 1024 or name == "templates.json" and info.size > MAX_TEMPLATE_FILE_BYTES:
                    raise ValueError("Portable settings exceed their bounds.")
                raw = read(info.size)
                if hashlib.sha256(raw).hexdigest() != entry["sha256"] or any(read((-info.size) % 512)):
                    raise ValueError("Portable archive contents failed verification.")
                data = json.loads(raw)
                if name == "preferences.json":
                    preferences = _safe_preferences(data)
                elif name == "templates.json":
                    templates = PromptTemplateService.validate(data)
                else:
                    if not isinstance(data, dict) or not isinstance(data.get("conversation_id"), str):
                        raise ValueError("Invalid portable conversation.")
                    record = ConversationService._decode(data, Path(data["conversation_id"] + ".json"))
                    if record.conversation_id in original_ids or len(conversations) >= MAX_CONVERSATION_FILES:
                        raise ValueError("Duplicate conversation identity or quota exceeded.")
                    original_ids.add(record.conversation_id)
                    conversations.append({"file": name, **record.summary()})
                if staging is not None:
                    atomic_write_text(staging / name, raw.decode("utf-8"), mode=0o600)
            if member() is not None or any(read(512)):
                raise ValueError("Unexpected members or missing portable end marker.")
            while source.tell() < before.st_size:
                if any(read(min(65536, before.st_size - source.tell()))):
                    raise ValueError("Unexpected data after portable archive.")
            after = os.fstat(source.fileno())
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if identity(before) != identity(after):
                raise ValueError("Portable archive changed while being verified.")
            return PortablePreview(digest.hexdigest(), before.st_size, tuple(conversations), preferences, tuple(templates))
    except (OSError, ValueError, UnicodeError, tarfile.TarError, RecursionError, OverflowError) as exc:
        raise ValueError("Portable backup is unreadable, changed, unsupported, or failed verification. No existing conversation was replaced.") from exc


class PortableBackupService:
    def __init__(self, paths, conversations, templates, preferences):
        self.paths, self.conversations = paths, conversations
        self.templates, self.preferences = templates, preferences

    def export(self, destination: Path, *, preference_keys=(), include_templates=False, conversation_ids=None):
        destination = Path(destination)
        if set(preference_keys) - set(PORTABLE_PREFERENCES):
            raise ValueError("Unsupported portable preference selection.")
        # Exporting over app-owned state would damage the source we protect.
        if destination.resolve().is_relative_to(self.paths.app_dir.resolve()):
            raise ValueError("Choose an export destination outside the application profile.")
        temp_output = None
        with profile_access(self.paths.app_dir), profile_access(self.paths.conversations_dir, exclusive=True):
            require_writable_profile(self.paths.app_dir)
            with tempfile.TemporaryDirectory(prefix=".bc250-portable-", dir=destination.parent) as temporary:
                staging = Path(temporary)
                if conversation_ids is None:
                    conversation_ids = []
                    if self.paths.conversations_dir.exists():
                        with os.scandir(self.paths.conversations_dir) as entries:
                            for entry in entries:
                                if entry.name.endswith(".json"):
                                    conversation_ids.append(entry.name[:-5])
                                    if len(conversation_ids) > MAX_CONVERSATION_FILES:
                                        raise ValueError("Select at most 200 conversations for one portable backup.")
                if len(conversation_ids) > MAX_CONVERSATION_FILES or len(set(conversation_ids)) != len(conversation_ids):
                    raise ValueError("Invalid portable conversation selection.")
                files, total = [], 0
                def stage(name, value):
                    nonlocal total
                    raw = _json_bytes(value)
                    total += len(raw)
                    if len(raw) > MAX_STORED_BYTES or total > MAX_PORTABLE_BYTES - 1024 * 1024:
                        raise ValueError("This selection exceeds the 128 MiB portable backup limit.")
                    atomic_write_text(staging / name, raw.decode(), mode=0o600)
                    files.append({"name": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                for index, identifier in enumerate(sorted(conversation_ids)):
                    record = self.conversations.load(identifier)
                    stage(f"conversation-{index:04}.json", record.to_dict())
                if preference_keys:
                    current = self.preferences.current()
                    stage("preferences.json", _safe_preferences({k: current[k] for k in preference_keys}))
                if include_templates:
                    stage("templates.json", self.templates.list())
                if not files:
                    raise ValueError("There are no conversations or selected settings to export.")
                manifest = _json_bytes({"format": "bc250-portable", "version": PORTABLE_VERSION,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": files})
                fd, temp_output = tempfile.mkstemp(prefix=".bc250-backup-", dir=destination.parent)
                try:
                    with os.fdopen(fd, "wb") as output:
                        with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                            def add(name, payload):
                                info = tarfile.TarInfo(name)
                                info.size, info.mode = len(payload), 0o600
                                archive.addfile(info, io.BytesIO(payload))
                            add("manifest.json", manifest)
                            for entry in files:
                                with (staging / entry["name"]).open("rb") as source:
                                    payload = source.read(MAX_STORED_BYTES + 1)
                                if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                                    raise ValueError("Staged backup changed before publication.")
                                add(entry["name"], payload)
                        output.flush()
                        os.fsync(output.fileno())
                    preview = inspect_portable(Path(temp_output))
                    os.replace(temp_output, destination)
                    fsync_directory(destination.parent)
                    temp_output = None
                    return preview
                finally:
                    if temp_output is not None:
                        Path(temp_output).unlink(missing_ok=True)

    def inspect(self, path):
        return inspect_portable(Path(path))

    def import_archive(self, path, expected_digest, *, preference_keys=(), include_templates=False,
                       expected_preferences=None):
        if not isinstance(expected_digest, str) or not _HEX.fullmatch(expected_digest):
            raise ValueError("A verified portable preview is required.")
        if set(preference_keys) - set(PORTABLE_PREFERENCES):
            raise ValueError("Unsupported portable preference selection.")
        with profile_access(self.paths.app_dir):
            require_writable_profile(self.paths.app_dir)
            ensure_private_dir(self.paths.staging_dir)
            with tempfile.TemporaryDirectory(prefix="portable-", dir=self.paths.staging_dir) as temporary:
                staging = Path(temporary)
                preview = inspect_portable(Path(path), staging=staging)
                if preview.digest != expected_digest:
                    raise ValueError("The archive changed after preview. Inspect it again.")
                if set(preference_keys) - set(preview.preferences):
                    raise ValueError("Selected preferences are absent from the archive.")
                imported, existing, template_count, settings_done = 0, 0, 0, False
                mapping = {row["conversation_id"]: "import-" + uuid.uuid5(uuid.NAMESPACE_URL,
                    expected_digest + ":" + row["conversation_id"]).hex for row in preview.conversations}
                with profile_access(self.paths.conversations_dir, exclusive=True):
                    ensure_private_dir(self.paths.conversations_dir)
                    targets = []
                    for row in preview.conversations:
                        target = self.paths.conversations_dir / (mapping[row["conversation_id"]] + ".json")
                        if target.exists() or target.is_symlink():
                            record = self.conversations.load(target.stem)
                            if record.import_source_digest != expected_digest or record.import_source_id != row["conversation_id"]:
                                raise ValueError("An import destination already belongs to another conversation.")
                            existing += 1
                        else:
                            targets.append((row, target))
                    count = 0
                    if targets:
                        with os.scandir(self.paths.conversations_dir) as entries:
                            for entry in entries:
                                if entry.name.endswith(".json"):
                                    count += 1
                                    if count + len(targets) > MAX_CONVERSATION_FILES:
                                        raise ValueError("Import would exceed 200 conversation files. Export and remove an older conversation first.")
                    if len(targets) > MAX_CONVERSATION_FILES:
                        raise ValueError("Import exceeds the conversation quota.")
                    try:
                        for row, target in targets:
                            data = json.loads((staging / row["file"]).read_bytes())
                            data["conversation_id"] = target.stem
                            data["import_source_digest"] = expected_digest
                            data["import_source_id"] = row["conversation_id"]
                            data["parent_conversation_id"] = mapping.get(data.get("parent_conversation_id"))
                            if data["parent_conversation_id"] is None:
                                data["parent_message_index"] = None
                            data["revision"] = 1
                            # Link publication is atomic and refuses any destination
                            # that appeared after preflight, even an external writer.
                            canonical = ConversationService._decode(data, target)
                            staged = staging / (target.name + ".ready")
                            atomic_write_text(staged, _json_bytes(canonical.to_dict()).decode(), mode=0o600)
                            os.link(staged, target, follow_symlinks=False)
                            fsync_directory(target.parent)
                            imported += 1
                        if include_templates and preview.templates:
                            values = json.loads((staging / "templates.json").read_bytes())
                            template_count = self.templates.merge_import(values, expected_digest)
                        if preference_keys:
                            self.preferences.apply_portable({k: preview.preferences[k] for k in preference_keys},
                                                           expected=expected_preferences)
                            settings_done = True
                    except (OSError, ValueError):
                        return PortableImportResult(imported, existing, len(targets) - imported,
                                                    settings_done, template_count, False)
                return PortableImportResult(imported, existing, 0, settings_done, template_count, True)
