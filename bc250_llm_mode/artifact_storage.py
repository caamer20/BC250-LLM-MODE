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
import tempfile
from pathlib import Path

CHUNK_BYTES = 4 * 1024 * 1024  # 4 MiB fixed hash/copy chunk
MAX_RECEIPT_BYTES = 64 * 1024


class PublicationCollision(RuntimeError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"PUBLICATION_COLLISION: {path.name}")
        self.code = "PUBLICATION_COLLISION"


class QuarantineCollision(RuntimeError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"QUARANTINE_COLLISION: {path.name}")
        self.code = "QUARANTINE_COLLISION"


def validate_operation_id(operation_id: str) -> str:
    """Reject any operation id that could create separators or escape roots."""
    import re

    if not isinstance(operation_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,64}", operation_id
    ):
        raise ValueError(f"unsafe operation id: {operation_id!r}")
    return operation_id


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
    # U1.1 §3.5: unique 0600 temporary, atomic replacement, parent fsync,
    # cleanup on every failure without touching the target.
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=".tmp-receipt", dir=str(path.parent)
    )
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_receipt(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_RECEIPT_BYTES:
            return None
        return json.loads(raw)
    except (OSError, ValueError):
        return None


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _identity_via_nofollow(path: Path) -> str | None:
    """Open an existing destination WITHOUT symlink following and prove a
    regular file before hashing; returns its digest or None if absent."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except (FileNotFoundError, OSError):
        return None
    try:
        import stat as stat_module

        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode):
            raise PublicationCollision(path)
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
        return f"sha256:{h.hexdigest()}"
    finally:
        os.close(fd)


def publish_no_replace(
    source: Path,
    artifacts_root: Path,
    content_digest: str,
) -> Path:
    """Atomically move a validated candidate into managed storage.

    The destination is derived solely from the verified content digest and
    is created with O_EXCL semantics; an existing identical file means the
    caller should reuse, and an existing different file is a collision.
    Incoming temporaries are unique, 0600, and cleaned on every failure.
    """
    content_digest = normalize_digest(content_digest)
    hex_part = content_digest.split(":", 1)[1]
    dest_dir = ensure_private_dir(artifacts_root / hex_part[:2])
    dest = dest_dir / f"{content_digest}.gguf"
    existing = _identity_via_nofollow(dest)
    if existing is not None:
        if existing == content_digest:
            return dest  # exact-existing reuse
        raise PublicationCollision(dest)
    fd, tmp_name = tempfile.mkstemp(prefix=".incoming-", dir=str(dest_dir))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            with open(source, "rb") as src:
                while True:
                    chunk = src.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o600)
        # Defense in depth: published bytes must equal the claimed digest.
        actual_digest, _ = streaming_sha256(tmp)
        if actual_digest != content_digest:
            raise PublicationCollision(source)
        try:
            os.link(tmp, dest)  # no-replace publication
        except FileExistsError:
            existing = _identity_via_nofollow(dest)
            if existing == content_digest:
                return dest
            raise PublicationCollision(dest)
        _fsync_dir(dest_dir)
        return dest
    finally:
        # The incoming temporary never survives; the published destination
        # is a separate durable name and is never touched here.
        tmp.unlink(missing_ok=True)


def quarantine_candidate(
    candidate: Path,
    quarantine_root: Path,
    operation_id: str,
    content_digest: str,
    reason_code: str,
) -> Path:
    """Move an invalid complete candidate into private quarantine.

    Quarantine is NO-REPLACE (U1.1 §3.1/§3.5): prior evidence is never
    unlinked or overwritten. An exact existing digest+reason is reused; a
    mismatched destination is a stable collision condition.
    """
    validate_operation_id(operation_id)
    content_digest = normalize_digest(content_digest)
    dest_dir = ensure_private_dir(quarantine_root / operation_id)
    dest = dest_dir / f"{content_digest}.gguf"
    receipt_path = dest_dir / "quarantine.json"
    existing = _identity_via_nofollow(dest)
    if existing is not None:
        receipt = read_receipt(receipt_path) or {}
        if existing == content_digest and receipt.get("reason_code") in (
            None,
            reason_code,
        ):
            return dest  # exact reuse of prior evidence
        raise QuarantineCollision(dest)

    def _write_evidence() -> None:
        write_receipt(
            receipt_path,
            {
                "content_digest": content_digest,
                "reason_code": reason_code,
                "operation_id": operation_id,
            },
        )

    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".incoming-q", dir=str(dest_dir))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            with open(candidate, "rb") as src:
                while True:
                    chunk = src.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        # Defense in depth: the staged bytes must equal the claimed digest
        # before any durable quarantine name is created.
        actual_digest, _ = streaming_sha256(tmp)
        if actual_digest != content_digest:
            raise QuarantineCollision(candidate)
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, dest)  # no-replace quarantine publication
        except FileExistsError:
            existing = _identity_via_nofollow(dest)
            if existing == content_digest:
                _write_evidence()
                return dest
            raise QuarantineCollision(dest)
        _write_evidence()
        _fsync_dir(dest_dir)
        return dest
    finally:
        tmp.unlink(missing_ok=True)