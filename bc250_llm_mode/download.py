from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .catalog import ModelEntry, validate_artifact
from .logging_utils import CommandRunner


GIB = 1024**3
DOWNLOAD_RESERVE_GIB = 1.0


def required_download_space_gib(model: ModelEntry, quant: str) -> float:
    """Conservative free-space requirement without buffering model data in RAM."""
    if model.temporary_disk_gib is not None:
        return model.temporary_disk_gib
    return model.weights_gib_by_quant[quant] * 1.05 + DOWNLOAD_RESERVE_GIB


def _existing_tree_gib(root: Path) -> float:
    if not root.exists():
        return 0.0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total / GIB


def _ensure_disk_space(destination: Path, required_gib: float) -> float:
    probe = destination
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    existing_gib = _existing_tree_gib(destination.parent)
    remaining_gib = max(0.25, required_gib - existing_gib)
    free_gib = shutil.disk_usage(probe).free / GIB
    if free_gib < remaining_gib:
        raise RuntimeError(
            f"Insufficient model-storage space: {free_gib:.1f} GiB free, "
            f"but this operation may still require approximately {remaining_gib:.1f} GiB "
            f"({existing_gib:.1f} GiB is already present). "
            "Free space or choose a smaller model before downloading."
        )
    return remaining_gib


def verify_sha256_manifest(artifact: Path, manifest: Path) -> None:
    """Verify one artifact against a conventional SHA256SUMS file, streaming it."""
    expected: str | None = None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        name = fields[1].lstrip("*")
        if Path(name).name == artifact.name:
            expected = fields[0].lower()
            break
    if expected is None:
        raise RuntimeError(f"{manifest.name} has no checksum for {artifact.name}")
    digest = hashlib.sha256()
    with artifact.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().lower()
    if actual != expected:
        raise RuntimeError(
            f"SHA-256 verification failed for {artifact.name}; remove the corrupt file and retry"
        )
