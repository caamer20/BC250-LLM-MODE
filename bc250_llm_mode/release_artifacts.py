"""G1 §4.3/§G1.2 + G3 §G3.1 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan):
pure artifact subject/inventory identity types for the release evaluator.

The release decision must bind itself to the exact candidate artifacts, so the
PURE identity types live in the package (no I/O) and the repository-only
tooling (``tools/release/artifacts.py``) builds inventories from disk and
re-exports these names. Identity is the content sha256 — NEVER the filename.

G3: inventory schema v2 adds required artifact-ROLE classification
(python-wheel / python-sdist / checksums / cyclonedx-sbom / release-manifest)
and a canonical inventory digest carried by decisions and manifests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

INVENTORY_SCHEMA_VERSION = 2

# Closed artifact-role vocabulary (§G3.1). Roles are assigned by the tooling
# from exact file names; duplicate non-empty roles are refused.
ARTIFACT_ROLES: tuple[str, ...] = (
    "python-wheel", "python-sdist", "checksums", "cyclonedx-sbom",
    "release-manifest",
)


@dataclass(frozen=True)
class Artifact:
    """One release artifact subject. Identity = content sha256 (C3.3)."""

    name: str
    sha256: str
    size: int = 0
    media_type: str = ""
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "sha256": self.sha256, "size": self.size,
                "media_type": self.media_type, "role": self.role}


@dataclass(frozen=True)
class ArtifactInventory:
    """The candidate's complete artifact set (pure, immutable).

    ``inventory_digest`` is the canonical sha256 over the sorted
    ``(name, sha256)`` subject pairs — the binding the decision/manifest carry
    so artifact substitution is detectable end to end.
    """

    artifacts: tuple[Artifact, ...] = ()

    def by_name(self) -> dict[str, Artifact]:
        return {a.name: a for a in self.artifacts}

    def inventory_digest(self) -> str:
        pairs = sorted((a.name, a.sha256) for a in self.artifacts)
        body = json.dumps(pairs, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "inventory_digest": self.inventory_digest(),
        }
