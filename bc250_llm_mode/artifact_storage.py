"""Managed artifact storage primitives (U1.1 / ADR 003).

Bounded streaming hash, safe operation-owned staging roots, fsynced
receipts, and atomic no-replace publication into the content-addressed
managed namespace. This module is the ONLY production writer of final
artifact paths.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

CHUNK_BYTES = 4 * 1024 * 1024  # 4 MiB fixed hash/copy chunk
MAX_RECEIPT_BYTES = 64 * 1024


class PublicationCollision(RuntimeError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"PUBLICATION_COLLISION: {path.name}")
        self.code = "PUBLICATION_COLLISION"


def normalize_digest(hex_digest: str) -> str:
    digest = hex_digest.strip().lower()
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError(f"invalid sha256 digest form: {digest[:16]}...")
    return digest


def streaming_sha256(path: Path, *, on_chunk=None) -> tuple[str, int]:
    """Full-file SHA-256 + size with bounded memory; never trusts prefixes."""
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
            if on_chunk is not None:
                on_chunk(total)
    return f"sha256:{h.hexdigest()}", total


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def contained(root: Path, candidate: Path) -> bool:
    """Refuse any path that escapes the exact root (no symlink traversal)."""
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


def write_receipt(path: Path, payload: dict) -> None:
    data = json.dumps(payload, sort_keys=True).encode()
    if len(data) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds bounded size")
    tmp = path.with_suffix(".tmp-receipt")
    tmp.write_bytes(data)
    with open(tmp, "rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_receipt(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_RECEIPT_BYTES:
            return None
        return json.loads(raw)
    except (OSError, ValueError):
        return None


def publish_no_replace(
    source: Path,
    artifacts_root: Path,
    content_digest: str,
) -> Path:
    """Atomically move a validated candidate into managed storage.

    The destination is derived solely from the verified content digest and
    is created with O_EXCL semantics; an existing identical file means the
    caller should reuse, and an existing different file is a collision.
    """
    hex_part = content_digest.split(":", 1)[1]
    dest_dir = artifacts_root / hex_part[:2]
    ensure_private_dir(dest_dir)
    dest = dest_dir / f"{content_digest}.gguf"
    if dest.exists():
        existing_digest, _ = streaming_sha256(dest)
        if existing_digest == content_digest:
            return dest  # exact-existing reuse
        raise PublicationCollision(dest)
    tmp = dest_dir / f".incoming-{os.getpid()}-{hex_part[:8]}"
    with open(source, "rb") as src, open(tmp, "wb") as out:
        while True:
            chunk = src.read(CHUNK_BYTES)
            if not chunk:
                break
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    try:
        os.link(tmp, dest)  # no-replace publication
    except FileExistsError:
        tmp.unlink(missing_ok=True)
        existing_digest, _ = streaming_sha256(dest)
        if existing_digest == content_digest:
            return dest
        raise PublicationCollision(dest)
    finally:
        tmp.unlink(missing_ok=True)
    # fsync the parent so the published name survives power loss.
    fd = os.open(dest_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return dest


def quarantine_candidate(
    candidate: Path,
    quarantine_root: Path,
    operation_id: str,
    content_digest: str,
    reason_code: str,
) -> Path:
    """Move an invalid complete candidate into private quarantine."""
    dest_dir = ensure_private_dir(quarantine_root / operation_id)
    dest = dest_dir / f"{content_digest}.gguf"
    if dest.exists():
        dest.unlink()
    os.replace(candidate, dest)
    write_receipt(
        dest_dir / "quarantine.json",
        {
            "content_digest": content_digest,
            "reason_code": reason_code,
            "operation_id": operation_id,
        },
    )
    return dest