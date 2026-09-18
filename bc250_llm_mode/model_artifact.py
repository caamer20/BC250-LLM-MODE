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
MAX_HEADER_BYTES = 64 * 1024 * 1024
MAX_ARRAY_VALUES = 1_000_000

GGUF_MAGIC = b"GGUF"
KNOWN_ARCHITECTURES = frozenset(
    {
        "llama",
        "qwen2",
        "qwen2moe",
        "qwen3",
        "qwen3moe",
        "qwen35",
        "lfm2",
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
VERDICT_RUNTIME_REQUIRED = "rejected_runtime_required"


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

    Walk all bounded metadata: a familiar architecture does not establish
    compatibility when later keys require custom runtime transforms. Tokenizer
    arrays are skipped without retaining their contents. Any parse failure,
    oversized structure, or unknown/fused architecture is a closed rejection
    verdict — never an exception to the caller.
    """
    try:
        with Path(path).open("rb") as fh:
            header_limit = min(os.fstat(fh.fileno()).st_size, MAX_HEADER_BYTES)

            def _read(count: int) -> bytes:
                if count < 0 or fh.tell() + count > header_limit:
                    raise ValueError("GGUF metadata exceeds its bounded region")
                return _read_exact(fh, count)

            def _skip_bytes(count: int) -> None:
                if count < 0 or fh.tell() + count > header_limit:
                    raise ValueError("GGUF metadata exceeds its bounded region")
                fh.seek(count, 1)

            magic = _read(4)
            if magic != GGUF_MAGIC:
                return VERDICT_NOT_GGUF
            (version,) = struct.unpack("<I", _read(4))
            if not 1 <= version <= 3:
                return VERDICT_NOT_GGUF
            (tensor_count,) = struct.unpack("<Q", _read(8))
            (meta_count,) = struct.unpack("<Q", _read(8))
            if tensor_count == 0:
                return VERDICT_NO_TENSORS
            if meta_count > MAX_META_ENTRIES:
                return VERDICT_FUSED

            def _string_length() -> int:
                (length,) = struct.unpack("<Q", _read(8))
                if length > MAX_STRING_CHARS:
                    raise ValueError("metadata string too long")
                return length

            def _read_string() -> str:
                return _read(_string_length()).decode("utf-8", "replace")

            # BOOL is one byte; UINT64, INT64 and FLOAT64 are eight bytes.
            sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
                     10: 8, 11: 8, 12: 8}
            remaining_array_values = MAX_ARRAY_VALUES

            def _skip_value(vtype: int, depth: int = 0) -> None:
                nonlocal remaining_array_values
                if depth > 3:
                    raise ValueError("metadata arrays are nested too deeply")
                if vtype == 8:  # string
                    _skip_bytes(_string_length())
                    return
                if vtype == 9:  # array
                    (elem_type,) = struct.unpack("<I", _read(4))
                    (count,) = struct.unpack("<Q", _read(8))
                    if count > remaining_array_values:
                        raise ValueError("metadata array too long")
                    remaining_array_values -= count
                    if elem_type in sizes:
                        _skip_bytes(count * sizes[elem_type])
                    elif elem_type in {8, 9}:
                        for _ in range(count):
                            _skip_value(elem_type, depth + 1)
                    else:
                        raise ValueError("unknown metadata array type")
                    return
                size = sizes.get(vtype)
                if size is None:
                    raise ValueError("unknown metadata type")
                _skip_bytes(size)

            architecture = None
            for _ in range(meta_count):
                key = _read_string()
                (vtype,) = struct.unpack("<I", _read(4))
                if key.startswith("prism.hadamard."):
                    return VERDICT_RUNTIME_REQUIRED
                if key == "general.file_type" and vtype == 4:
                    (file_type,) = struct.unpack("<I", _read(4))
                    if file_type in {141, 142, 143}:
                        return VERDICT_RUNTIME_REQUIRED
                    continue
                if key == "general.architecture" and vtype == 8:
                    arch = _read_string().strip().lower()
                    if "fused" in arch or "max" in arch:
                        return VERDICT_FUSED
                    if arch not in KNOWN_ARCHITECTURES:
                        return VERDICT_UNKNOWN_ARCH
                    if architecture is not None:
                        raise ValueError("duplicate architecture metadata")
                    architecture = arch
                else:
                    _skip_value(vtype)
            return VERDICT_STANDARD if architecture else VERDICT_UNKNOWN_ARCH
    except (OSError, ValueError, struct.error):
        return VERDICT_NOT_GGUF


def digest_prefixed(digest: str) -> str:
    """Stable short evidence form (never used as sole identity)."""
    return f"sha256:{digest[:16]}"
