"""Bounded artifact inventory + digests for the release tooling (C1 §C1.1).

Builds an ``ArtifactInventory`` (name, sha256, size) over a ``dist/``-style
directory. Bounded: refuses overly large inventories and files above a size
cap so a release audit cannot be coerced into hashing unbounded data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_ARTIFACTS = 64
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB per artifact
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Artifact:
    name: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class ArtifactInventory:
    artifacts: tuple[Artifact, ...] = ()

    def by_name(self) -> dict[str, Artifact]:
        return {a.name: a for a in self.artifacts}

    def to_dict(self) -> dict[str, Any]:
        return {"artifacts": [a.to_dict() for a in self.artifacts]}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def build_inventory(dist_dir: str | Path) -> ArtifactInventory:
    """Inventory the regular files directly under ``dist_dir`` (bounded)."""
    root = Path(dist_dir)
    artifacts: list[Artifact] = []
    if not root.is_dir():
        return ArtifactInventory()
    files = sorted(p for p in root.iterdir() if p.is_file())
    if len(files) > MAX_ARTIFACTS:
        raise ValueError(f"too many artifacts ({len(files)} > {MAX_ARTIFACTS})")
    for path in files:
        size = path.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise ValueError(f"artifact too large: {path.name}")
        artifacts.append(Artifact(name=path.name, sha256=sha256_file(path),
                                  size=size))
    return ArtifactInventory(artifacts=tuple(artifacts))
