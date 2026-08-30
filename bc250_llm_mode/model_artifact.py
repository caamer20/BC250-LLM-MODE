"""Bounded GGUF artifact identity (Session 5C plan §5.2).

Content digests stream in fixed-size chunks; GGUF header inspection reads
only the small leading metadata region with hard entry/size caps. No
multi-GiB file is ever loaded into host RAM, and no path escapes the
injected roots.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

CHUNK_BYTES = 1024 * 1024  # 1 MiB streaming reads
MAX_META_ENTRIES = 4096
MAX_STRING_CHARS = 131072

GGUF_MAGIC = b"GGUF"
KNOWN_ARCHITECTURES = frozenset(
    {
        "llama",
        "qwen2",
        "qwen2moe",
        "qwen3",
        "qwen3moe",
        "qwen35",
        "phi3",
        "gemma",
        "gemma2",
        "gemma3",
        "mistral",
        "mixtral",
        "deepseek2",
        "stablelm",
    }
)

VERDICT_STANDARD = "standard"
VERDICT_NOT_GGUF = "rejected_not_gguf"
VERDICT_UNKNOWN_ARCH = "rejected_unknown_architecture"
VERDICT_FUSED = "rejected_fused_layout"
VERDICT_NO_TENSORS = "rejected_no_tensor_data"


def streaming_identity(path: Path) -> tuple[str, int, str]:
    """Return ``(sha256_hex, byte_size, file_identity)`` via bounded reads."""
    import hashlib

    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
            h.update(chunk)
            size += len(chunk)
    stat = os.stat(path)
    identity = f"{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ino}"
    return h.hexdigest(), size, identity


def _read_exact(fh, count: int) -> bytes:
    data = fh.read(count)
    if len(data) != count:
        raise ValueError("truncated GGUF header")
    return data


def gguf_layout_verdict(path: Path) -> str:
    """Bounded GGUF header walk: fused/MAX and missing layouts are rejected.

    Reads the magic, version, tensor count, and walks metadata key/value
    pairs only far enough to find ``general.architecture``. Any parse
    failure, oversized structure, or unknown/fused architecture is a
    closed rejection verdict — never an exception to the caller.
    """
    try:
        with Path(path).open("rb") as fh:
            magic = _read_exact(fh, 4)
            if magic != GGUF_MAGIC:
                return VERDICT_NOT_GGUF
            (version,) = struct.unpack("<I", _read_exact(fh, 4))
            if not 1 <= version <= 3:
                return VERDICT_NOT_GGUF
            (tensor_count,) = struct.unpack("<Q", _read_exact(fh, 8))
            (meta_count,) = struct.unpack("<Q", _read_exact(fh, 8))
            if tensor_count == 0:
                return VERDICT_NO_TENSORS
            if meta_count > MAX_META_ENTRIES:
                return VERDICT_FUSED

            def _read_string() -> str:
                (length,) = struct.unpack("<Q", _read_exact(fh, 8))
                if length > MAX_STRING_CHARS:
                    raise ValueError("metadata string too long")
                return _read_exact(fh, length).decode("utf-8", "replace")

            def _skip_value(vtype: int) -> None:
                sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 8, 8: 8,
                         9: 8, 10: 8}
                if vtype == 8:  # string
                    _read_string()
                    return
                if vtype == 9:  # array
                    (elem_type,) = struct.unpack("<I", _read_exact(fh, 4))
                    (count,) = struct.unpack("<Q", _read_exact(fh, 8))
                    if count > MAX_META_ENTRIES:
                        raise ValueError("metadata array too long")
                    for _ in range(count):
                        _skip_value(elem_type)
                    return
                size = sizes.get(vtype)
                if size is None:
                    raise ValueError("unknown metadata type")
                fh.seek(size, 1)

            for _ in range(meta_count):
                key = _read_string()
                (vtype,) = struct.unpack("<I", _read_exact(fh, 4))
                if key == "general.architecture" and vtype == 8:
                    arch = _read_string().strip().lower()
                    if "fused" in arch or "max" in arch:
                        return VERDICT_FUSED
                    if arch not in KNOWN_ARCHITECTURES:
                        return VERDICT_UNKNOWN_ARCH
                    return VERDICT_STANDARD
                _skip_value(vtype)
            return VERDICT_UNKNOWN_ARCH
    except (OSError, ValueError, struct.error):
        return VERDICT_NOT_GGUF


def digest_prefixed(digest: str) -> str:
    """Stable short evidence form (never used as sole identity)."""
    return f"sha256:{digest[:16]}"
