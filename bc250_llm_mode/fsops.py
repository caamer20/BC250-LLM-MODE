"""Durable atomic file publication (ADR 001 staging/publish semantics).

Every durable artifact — the published database, the runtime handoff,
migration receipts, state JSON — follows the same six steps:

1. create the temporary file with restrictive permissions (mkstemp: 0600)
2. write and flush
3. fsync the temp file
4. apply/verify the final mode
5. atomically replace the target
6. fsync the parent directory (so the rename itself survives power loss)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def fsync_directory(directory: str | Path) -> None:
    """fsync a directory so a completed rename is durable."""
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_staged(source: str | Path, target: str | Path, *, mode: int = 0o600) -> None:
    """Durably publish an already-written staging file onto ``target``."""
    source = Path(source)
    target = Path(target)
    fd = os.open(str(source), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(source, mode)
    os.replace(source, target)
    fsync_directory(target.parent)


def atomic_write_bytes(path: str | Path, data: bytes, *, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
        fsync_directory(target.parent)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: str | Path, text: str, *, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def ensure_private_dir(path: str | Path, mode: int = 0o700) -> Path:
    """Create ``path`` (parents included) and enforce ``mode`` on it."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, mode)
    return directory