"""U1.1 §9.5 subset: managed storage primitives (ADR 003)."""

from __future__ import annotations

import os

import pytest

from bc250_llm_mode import artifact_storage as storage
from bc250_llm_mode.paths import AppPaths


def test_streaming_hash_matches_known_digest(tmp_path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"x" * 10_000)
    digest, size = storage.streaming_sha256(p)
    assert digest.startswith("sha256:")
    assert size == 10_000
    assert storage.normalize_digest(digest) == digest


def test_normalize_digest_rejects_bad_form():
    with pytest.raises(ValueError):
        storage.normalize_digest("md5:abcd")


def test_publish_no_replace_is_atomic_and_dedupes(tmp_path):
    src = tmp_path / "candidate.gguf"
    payload = b"gguf-payload"
    src.write_bytes(payload)
    root = tmp_path / "artifacts"
    digest, _ = storage.streaming_sha256(src)

    dest = storage.publish_no_replace(src, root, digest)
    assert dest.exists()
    assert dest.read_bytes() == payload
    assert not list(root.rglob("*.tmp-publish"))
    assert not list(root.rglob(".incoming-*"))

    # Exact same content reuses without overwrite.
    again = storage.publish_no_replace(src, root, digest)
    assert again == dest

    # Tampered destination (same derived path, different bytes) is a collision.
    hex_part = digest.split(":")[1]
    tampered = root / hex_part[:2] / f"{digest}.gguf"
    tampered.write_bytes(b"tampered")
    src2 = tmp_path / "candidate2.gguf"
    src2.write_bytes(b"other-bytes-entirely")
    src2_digest, _ = storage.streaming_sha256(src2)
    forced = root / src2_digest.split(":")[1][:2] / f"{src2_digest}.gguf"
    forced.parent.mkdir(parents=True, exist_ok=True)
    forced.write_bytes(b"not-the-real-content")
    with pytest.raises(storage.PublicationCollision):
        storage.publish_no_replace(src2, root, src2_digest)


def test_quarantine_moves_candidate_and_writes_receipt(tmp_path):
    candidate = tmp_path / "staging" / "op-1" / "bad.gguf"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"invalid-gguf")
    qroot = tmp_path / "quarantine"
    digest, _ = storage.streaming_sha256(candidate)
    dest = storage.quarantine_candidate(
        candidate, qroot, "op-1", digest, "GGUF_INVALID"
    )
    assert dest.read_bytes() == b"invalid-gguf"
    receipt = storage.read_receipt(qroot / "op-1" / "quarantine.json")
    assert receipt["reason_code"] == "GGUF_INVALID"
    # No-replace reuse: identical digest+reason reuses prior evidence.
    again = storage.quarantine_candidate(
        candidate, qroot, "op-1", digest, "GGUF_INVALID"
    )
    assert again == dest
    # Mismatched reason at the same destination is a stable collision.
    with pytest.raises(storage.QuarantineCollision):
        storage.quarantine_candidate(
            candidate, qroot, "op-1", digest, "GGUF_LAYOUT_FORBIDDEN"
        )
    # Content that does not match its claimed digest is refused outright.
    with pytest.raises(storage.QuarantineCollision):
        storage.quarantine_candidate(
            candidate, qroot, "op-1",
            "sha256:" + "bb" * 32, "GGUF_INVALID",
        )
    # Unsafe operation ids never reach the filesystem.
    with pytest.raises(ValueError):
        storage.quarantine_candidate(
            candidate, qroot, "../escape", digest, "GGUF_INVALID"
        )


def test_publication_enforces_private_modes_and_temp_cleanup(tmp_path, monkeypatch):
    src = tmp_path / "candidate.gguf"
    src.write_bytes(b"payload")
    root = tmp_path / "artifacts"
    digest, _ = storage.streaming_sha256(src)

    storage.publish_no_replace(src, root, digest)
    hex_part = digest.split(":")[1]
    published = root / hex_part[:2] / f"{digest}.gguf"
    import stat

    assert stat.S_IMODE(published.stat().st_mode) == 0o600
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir()
    storage.write_receipt(receipts_dir / "pub.json", {"ok": True})
    assert stat.S_IMODE((receipts_dir / "pub.json").stat().st_mode) == 0o600

    # An injected hardlink failure leaves no incoming temporary behind.
    src2 = tmp_path / "c2.gguf"
    src2.write_bytes(b"other payload bytes")
    d2, _ = storage.streaming_sha256(src2)
    real_link = os.link

    def boom(*a, **k):
        raise OSError("injected link failure")

    monkeypatch.setattr(os, "link", boom)
    with pytest.raises(OSError):
        storage.publish_no_replace(src2, root, d2)
    monkeypatch.setattr(os, "link", real_link)
    leftovers = [
        p for p in (root / d2.split(":")[1][:2]).glob(".incoming-*")
    ]
    assert not leftovers


def test_contained_refuses_escape_and_app_paths_derive_hidden_roots(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    assert storage.contained(root, root / "op-1" / "x")
    assert not storage.contained(root, tmp_path / "outside")

    paths = AppPaths.from_app_dir(tmp_path / "profile")
    assert paths.model_staging_dir == paths.models_dir / ".bc250-staging"
    assert paths.model_quarantine_dir == paths.models_dir / ".bc250-quarantine"
    assert paths.model_artifacts_dir.parent.name == ".bc250-artifacts"