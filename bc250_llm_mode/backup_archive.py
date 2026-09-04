"""Bounded, exact-member inspection of the supported uncompressed backup format.

The database bytes, not just the manifest, are authenticated before any restore
publication. Reads and optional staging use one held, non-symlink descriptor.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backup_manifest import verify_manifest_digest
from .db import SCHEMA_VERSION
from .operations.validation import OperationValidationError

MANIFEST_NAME = "backup-manifest.json"
MAX_DATABASE_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_DATABASE_BYTES + MAX_MANIFEST_BYTES + 64 * 1024


@dataclass(frozen=True)
class InspectedBackup:
    manifest: dict[str, Any]
    archive_digest: str
    size_bytes: int
    identity: tuple[int, int, int, int]


def inspect_archive(path: Path, *, destination: Path | None = None,
                    cancelled=lambda: False, heartbeat=lambda: None, timeout: float = 120.0) -> InspectedBackup:
    """Inspect or stage only the exact supported file set; never extractall."""
    def refuse(reason):
        raise OperationValidationError("BACKUP_INVALID: " + reason)

    deadline = time.monotonic() + timeout
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 1024 <= before.st_size <= MAX_ARCHIVE_BYTES:
                refuse("archive type or size")
            digest = hashlib.sha256()

            def read(size):
                if cancelled() or time.monotonic() > deadline:
                    refuse("inspection cancelled or timed out")
                heartbeat()
                data = source.read(size)
                if len(data) != size:
                    refuse("truncated archive")
                digest.update(data)
                return data

            seen = {}
            manifest = None
            headers = 0
            while True:
                header = read(512)
                if header == bytes(512):
                    if read(512) != bytes(512):
                        refuse("invalid end marker")
                    while source.tell() < before.st_size:
                        if any(read(min(65536, before.st_size - source.tell()))):
                            refuse("data after end marker")
                    break
                headers += 1
                if headers > 8:
                    refuse("too many headers")
                member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                if member.type == tarfile.XHDTYPE:
                    # Older backups produced Python's mtime-only PAX header.
                    # Bound it before reading; no path/size/link overrides.
                    if not 0 < member.size <= 4096:
                        refuse("extended metadata size")
                    pax = read(member.size)
                    read((-member.size) % 512)
                    while pax:
                        length, sep, rest = pax.partition(b" ")
                        if not sep or not length.isdigit():
                            refuse("invalid extended metadata")
                        count = int(length)
                        if count <= len(length) + 1 or count > len(pax):
                            refuse("invalid extended metadata length")
                        field = pax[len(length) + 1:count]
                        if not field.startswith(b"mtime=") or not field.endswith(b"\n"):
                            refuse("unsupported extended metadata")
                        pax = pax[count:]
                    continue
                if member.name not in {MANIFEST_NAME, "state.db"} or member.name in seen:
                    refuse("unexpected or duplicate member")
                if not member.isreg() or member.sparse or member.linkname:
                    refuse("member must be a regular file")
                limit = MAX_MANIFEST_BYTES if member.name == MANIFEST_NAME else MAX_DATABASE_BYTES
                if not 0 < member.size <= limit:
                    refuse("member size")
                payload_hash = hashlib.sha256()
                manifest_bytes = bytearray()
                target = None
                try:
                    if destination is not None:
                        # destination is a fresh private owned staging directory.
                        out_fd = os.open(destination / member.name,
                                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                        target = os.fdopen(out_fd, "wb")
                    remaining = member.size
                    while remaining:
                        chunk = read(min(1024 * 1024, remaining))
                        remaining -= len(chunk)
                        payload_hash.update(chunk)
                        if member.name == MANIFEST_NAME:
                            manifest_bytes.extend(chunk)
                        if target is not None:
                            target.write(chunk)
                    if target is not None:
                        target.flush()
                        os.fsync(target.fileno())
                finally:
                    if target is not None:
                        target.close()
                read((-member.size) % 512)
                seen[member.name] = (member.size, payload_hash.hexdigest())
                if member.name == MANIFEST_NAME:
                    manifest = json.loads(manifest_bytes)
            if set(seen) != {MANIFEST_NAME, "state.db"} or not isinstance(manifest, dict):
                refuse("incomplete archive")
            if not verify_manifest_digest(manifest):
                refuse("manifest digest mismatch")
            if (manifest.get("backup_manifest_schema_version") != 1
                    or type(manifest.get("database_schema_version")) is not int
                    or not 1 <= manifest["database_schema_version"] <= SCHEMA_VERSION):
                refuse("unsupported schema")
            if manifest.get("model_bytes_included") is not False:
                refuse("unsupported inclusion claim")
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
                refuse("invalid file inventory")
            entry = files[0]
            if (entry.get("relative_path") != "state.db"
                    or type(entry.get("size_bytes")) is not int
                    or (entry["size_bytes"], entry.get("sha256")) != seen["state.db"]
                    or entry.get("mode") != 0o600):
                refuse("database bytes differ from manifest")
            after = os.fstat(source.fileno())
            identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            if identity(before) != identity(after) or before.st_ctime_ns != after.st_ctime_ns:
                refuse("archive changed during inspection")
            return InspectedBackup(manifest, digest.hexdigest(), before.st_size, identity(before))
    except OperationValidationError:
        raise
    except (OSError, ValueError, tarfile.TarError, UnicodeError, RecursionError) as exc:
        raise OperationValidationError("BACKUP_INVALID: unreadable or malformed archive") from exc
