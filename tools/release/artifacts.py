"""Bounded artifact inventory + digests for the release tooling (C1 §C1.1).

Builds an ``ArtifactInventory`` (name, sha256, size, media_type) over a
``dist/``-style directory. Bounded: refuses overly large inventories and files
above a size cap so a release audit cannot be coerced into hashing unbounded
data. C3.3: identity is the content sha256 (never the filename); symlinks and
special files are rejected.

G1 (§4.3, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): the PURE identity types
(``Artifact`` / ``ArtifactInventory``) moved to the package module
``bc250_llm_mode.release_artifacts`` so the evaluator can bind decisions to
exact artifact subjects; this repository-only tooling keeps the disk I/O and
re-exports the names for compatibility. G3 adds inventory v2 roles here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# G1: pure identity types live in the package; re-exported for compatibility.
from bc250_llm_mode.release_artifacts import (  # noqa: F401
    Artifact,
    ArtifactInventory,
)

MAX_ARTIFACTS = 64
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB per artifact
_CHUNK = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def media_type_for(name: str) -> str:
    """Canonical media type for a release artifact (never trusts content)."""
    if name.endswith(".whl"):
        return "application/vnd.pypi.wheel.v1"
    if name.endswith((".tar.gz", ".tgz")):
        return "application/gzip"
    if name.endswith(".json"):
        return "application/json"
    if name.endswith((".txt", ".sha256")):
        return "text/plain"
    return "application/octet-stream"


def build_inventory(dist_dir: str | Path) -> ArtifactInventory:
    """Inventory the regular files directly under ``dist_dir`` (bounded).

    C3.3: identity is the content sha256, NEVER the filename. Symlinks and
    special files are rejected so a release audit cannot be pointed through a
    link or device node.
    """
    root = Path(dist_dir)
    artifacts: list[Artifact] = []
    if not root.is_dir():
        return ArtifactInventory()
    files = sorted(p for p in root.iterdir() if not p.is_dir())
    if len(files) > MAX_ARTIFACTS:
        raise ValueError(f"too many artifacts ({len(files)} > {MAX_ARTIFACTS})")
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"artifact must be a regular file: {path.name}")
        size = path.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise ValueError(f"artifact too large: {path.name}")
        artifacts.append(Artifact(name=path.name, sha256=sha256_file(path),
                                  size=size,
                                  media_type=media_type_for(path.name)))
    return ArtifactInventory(artifacts=tuple(artifacts))
