from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from bc250_llm_mode.catalog import model_by_id
from bc250_llm_mode import download
from bc250_llm_mode.download import required_download_space_gib, verify_sha256_manifest


def test_standard_download_space_includes_reserve() -> None:
    model = model_by_id("qwen38-9b")
    assert required_download_space_gib(model, "Q4_K_M") > model.weights_gib_by_quant["Q4_K_M"] + 0.9


def test_conversion_download_space_accounts_for_intermediates() -> None:
    model = model_by_id("defiant-fable-9b")
    assert required_download_space_gib(model, "Q5_K_M") == 46.0


def test_disk_preflight_credits_resumable_files(tmp_path: Path, monkeypatch) -> None:
    model_root = tmp_path / "model"
    source = model_root / "source"
    source.mkdir(parents=True)
    partial = source / "partial.gguf"
    with partial.open("wb") as handle:
        handle.truncate(int(1.5 * download.GIB))
    monkeypatch.setattr(
        download.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=int(0.6 * download.GIB)),
    )
    assert download._ensure_disk_space(source, 2.0) == pytest.approx(0.5)


def test_sha256_manifest_is_verified_streaming(tmp_path: Path) -> None:
    artifact = tmp_path / "model.gguf"
    artifact.write_bytes(b"small test artifact")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
    verify_sha256_manifest(artifact, manifest)


def test_sha256_manifest_rejects_corruption(tmp_path: Path) -> None:
    artifact = tmp_path / "model.gguf"
    artifact.write_bytes(b"corrupt")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{'0' * 64}  {artifact.name}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SHA-256 verification failed"):
        verify_sha256_manifest(artifact, manifest)
