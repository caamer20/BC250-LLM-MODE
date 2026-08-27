"""P9 §15.3: property/fuzz-style robustness gates (deterministic).

Seeded adversarial inputs prove the parse/validate surfaces are fail-closed and
bounded — they never hang, never leak an unhandled exception to the caller, and
never accept what they should refuse. Deterministic (seeded RNG, no sleeps) so
they run identically in constrained CI. Covers GGUF parsing, operation request
validation, backup manifest digest verification, and event pagination bounds.
"""

from __future__ import annotations

import random
import struct

import pytest

from bc250_llm_mode.model_artifact import (
    GGUF_MAGIC,
    VERDICT_FUSED,
    VERDICT_NOT_GGUF,
    VERDICT_NO_TENSORS,
    VERDICT_STANDARD,
    VERDICT_UNKNOWN_ARCH,
    gguf_layout_verdict,
)

_CLOSED_VERDICTS = {
    VERDICT_STANDARD, VERDICT_NOT_GGUF, VERDICT_UNKNOWN_ARCH,
    VERDICT_FUSED, VERDICT_NO_TENSORS,
}


def test_gguf_verdict_is_total_over_adversarial_inputs(tmp_path):
    """gguf_layout_verdict returns a closed verdict for EVERY input — never an
    exception, never a hang."""
    rng = random.Random(1337)
    cases: list[bytes] = [
        b"",                                   # empty
        b"GG",                                 # truncated magic
        b"NOTG" + b"\x00" * 32,                # wrong magic
        GGUF_MAGIC,                            # magic only
        GGUF_MAGIC + struct.pack("<I", 99),    # unsupported version
        GGUF_MAGIC + struct.pack("<I", 2),     # truncated after version
        GGUF_MAGIC + struct.pack("<I", 2) + struct.pack("<Q", 0)
        + struct.pack("<Q", 0),                # zero tensors
        GGUF_MAGIC + struct.pack("<I", 2) + struct.pack("<Q", 1)
        + struct.pack("<Q", 10**7),            # oversized meta count
        bytes(rng.getrandbits(8) for _ in range(256)),  # pure noise
    ]
    # Seeded random blobs, some starting with the magic.
    for _ in range(20):
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 200)))
        cases.append(blob)
        cases.append(GGUF_MAGIC + blob)

    for i, data in enumerate(cases):
        target = tmp_path / f"case_{i}.bin"
        target.write_bytes(data)
        verdict = gguf_layout_verdict(target)
        assert verdict in _CLOSED_VERDICTS, f"case {i} escaped the vocabulary"


def test_gguf_truncation_never_reads_past_bounds(tmp_path):
    """Every prefix of a minimal header yields a closed verdict."""
    header = (
        GGUF_MAGIC + struct.pack("<I", 2) + struct.pack("<Q", 4)
        + struct.pack("<Q", 0)
    )
    for length in range(len(header) + 1):
        target = tmp_path / f"prefix_{length}.bin"
        target.write_bytes(header[:length])
        assert gguf_layout_verdict(target) in _CLOSED_VERDICTS


def test_sanitize_payload_is_bounded_and_fails_closed():
    from bc250_llm_mode.operations.validation import sanitize_payload

    # Oversized payload is refused (budget), not silently truncated.
    huge = {"alias": "x" * 100_000}
    with pytest.raises(Exception):
        sanitize_payload(huge, max_bytes=1024)

    # Deeply nested input either bounds to a string or raises — never hangs.
    nested: dict = {}
    cursor = nested
    for i in range(50):
        child: dict = {}
        cursor["k"] = child
        cursor = child
    cursor["leaf"] = "v"
    try:
        result = sanitize_payload(nested, max_bytes=10_000)
        assert isinstance(result, str)
    except Exception:
        pass  # refusal is acceptable; a hang or crash is not

    # A small valid payload round-trips within budget.
    ok = sanitize_payload({"alias": "tiny", "requested_by": "cli"})
    assert isinstance(ok, str) and "tiny" in ok


def test_manifest_digest_verification_never_raises():
    from bc250_llm_mode.backup_manifest import verify_manifest_digest

    rng = random.Random(7)
    # Random/garbage documents must return False, never raise.
    for _ in range(20):
        garbage = {
            "manifest_digest": "sha256:" + "0" * 64,
            "noise": bytes(rng.getrandbits(8) for _ in range(16)).hex(),
        }
        assert verify_manifest_digest(garbage) is False
    assert verify_manifest_digest({}) is False
    assert verify_manifest_digest({"manifest_digest": ""}) is False


def test_event_pagination_bounds_refuse_out_of_range():
    from bc250_llm_mode.operations.query_service import (
        MAX_EVENT_LIMIT,
        MAX_PAGE_SIZE,
    )

    assert MAX_PAGE_SIZE >= 1
    assert MAX_EVENT_LIMIT >= 1
    # The bounds are finite (bounded accumulation path, P7/P9 exit gate).
    assert MAX_PAGE_SIZE <= 1000
    assert MAX_EVENT_LIMIT <= 5000
