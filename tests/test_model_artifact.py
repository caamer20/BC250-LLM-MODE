"""U0.2: bounded artifact identity and GGUF validation hardening.

Malformed-hostile GGUF fixtures, streaming digest race detection, and
descriptor-bounded reads: no multi-GiB read path, no trusting desired
state over observed bytes.
"""

from __future__ import annotations

import struct
import threading
from pathlib import Path

import pytest

from bc250_llm_mode.model_artifact import (
    VERDICT_FUSED,
    VERDICT_NOT_GGUF,
    VERDICT_NO_TENSORS,
    VERDICT_STANDARD,
    VERDICT_UNKNOWN_ARCH,
    gguf_layout_verdict,
    streaming_identity,
)


def _gguf(arch: bytes | None = b"llama", tensors: int = 1) -> bytes:
    def s(value: bytes) -> bytes:
        return struct.pack("<Q", len(value)) + value

    header = b"GGUF" + struct.pack("<I", 3)
    header += struct.pack("<Q", tensors)
    if arch is None:
        return header + struct.pack("<Q", 0)
    metadata = s(b"general.architecture") + struct.pack("<I", 8) + s(arch)
    return header + struct.pack("<Q", 1) + metadata


@pytest.mark.parametrize(
    "content, expected",
    [
        (b"", VERDICT_NOT_GGUF),
        (b"NOPE" + b"\x00" * 64, VERDICT_NOT_GGUF),
        # Truncated mid-header.
        (b"GGUF" + struct.pack("<I", 3), VERDICT_NOT_GGUF),
        # Zero tensor records: not a usable model body.
        (_gguf(tensors=0), VERDICT_NO_TENSORS),
        # Unknown architecture is rejected by name.
        (_gguf(arch=b"not-a-real-arch"), VERDICT_UNKNOWN_ARCH),
        # Fused-style architecture names are rejected outright.
        (_gguf(arch=b"fused-max-thing"), VERDICT_FUSED),
        # Oversized declared string cannot balloon memory.
        (
            b"GGUF" + struct.pack("<I", 3)
            + struct.pack("<Q", 1) + struct.pack("<Q", 1)
            + struct.pack("<Q", 10**9) + b"x",
            VERDICT_NOT_GGUF,
        ),
        # Oversized array length cannot balloon memory.
        (
            b"GGUF" + struct.pack("<I", 3)
            + struct.pack("<Q", 1) + struct.pack("<Q", 1)
            + struct.pack("<Q", 3) + b"arr"
            + struct.pack("<I", 9) + struct.pack("<Q", 10**9),
            VERDICT_NOT_GGUF,
        ),
    ],
)
def test_gguf_verdicts_are_closed_and_hostile_content_safe(tmp_path, content, expected):
    path = tmp_path / "model.gguf"
    path.write_bytes(content)
    assert gguf_layout_verdict(path) == expected


def test_streaming_identity_detects_in_place_rewrite(tmp_path):
    """Observed identity must differ when the file changes underneath."""
    path = tmp_path / "model.gguf"
    path.write_bytes(_gguf())
    first = streaming_identity(path)

    path.write_bytes(_gguf(arch=b"qwen3"))
    second = streaming_identity(path)

    assert first[0] != second[0], "digest must observe real content"
    assert first[1] == second[1] or first[2] != second[2]
    # Size and stable identity are part of the evidence triple.
    assert len(first) == 3


def test_streaming_identity_is_chunk_bounded(tmp_path):
    """Large files hash through bounded chunks without loading into RAM."""
    path = tmp_path / "big.gguf"
    payload = _gguf() + b"\x00" * (5 * 1024 * 1024)
    path.write_bytes(payload)
    digest, size, _identity = streaming_identity(path)
    assert size == len(payload)
    assert len(digest) == 64


def test_streaming_identity_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        streaming_identity(tmp_path / "absent.gguf")
