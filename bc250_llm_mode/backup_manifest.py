"""P8 §14.1: backup manifest model (pure, no I/O).

A versioned backup manifest identifies everything needed to restore the
appliance WITHOUT secrets: application release + database schema version,
legacy-import provenance, runtime builds + selected known-good lineage,
managed model artifact metadata + aliases (large bytes only on an explicit
space-aware choice), settings/setup evidence/thermal baseline/operation-history
policy, gateway/integration configuration metadata, per-file digests/sizes/
modes with relative containment, and the backup tool version + manifest digest.

This module is pure — it builds and validates manifest documents only. It never
reads the filesystem, never touches SQLite, and never includes secret material.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

BACKUP_MANIFEST_SCHEMA_VERSION = 1
BACKUP_TOOL_VERSION = "1.0.0"

# Keys that must NEVER appear anywhere in a backup manifest (secret canary).
_FORBIDDEN_SECRET_KEYS = frozenset({
    "secret", "token", "password", "passphrase", "api_key", "apikey",
    "credential", "private_key",
})


class BackupManifestError(ValueError):
    """Raised when a manifest is malformed or carries secret material."""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Stable sha256 over the canonical manifest (excluding the digest field)."""
    body = {k: v for k, v in manifest.items() if k != "manifest_digest"}
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _assert_no_secrets(obj: Any, path: str = "$") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            for forbidden in _FORBIDDEN_SECRET_KEYS:
                if forbidden in lowered:
                    raise BackupManifestError(
                        f"secret-like key {key!r} at {path} is forbidden in a "
                        f"backup manifest")
            _assert_no_secrets(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _assert_no_secrets(value, f"{path}[{i}]")


def _assert_relative_containment(paths: list[str]) -> None:
    for rel in paths:
        if rel.startswith("/") or rel.startswith("\\"):
            raise BackupManifestError(f"absolute path not contained: {rel!r}")
        parts = rel.replace("\\", "/").split("/")
        if ".." in parts:
            raise BackupManifestError(f"path traversal not contained: {rel!r}")


@dataclass(frozen=True)
class FileEntry:
    """One backed-up file: digest, size, mode, relative contained path."""

    relative_path: str
    sha256: str
    size_bytes: int
    mode: int = 0o600

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class BackupManifest:
    """Versioned backup manifest (P8 §14.1). Never carries secrets."""

    application_release: str
    database_schema_version: int
    backup_tool_version: str = BACKUP_TOOL_VERSION
    created_at: str = ""
    legacy_import_provenance: dict[str, Any] | None = None
    runtime_builds: list[dict[str, Any]] = field(default_factory=list)
    known_good_lineage: dict[str, Any] | None = None
    model_artifacts: list[dict[str, Any]] = field(default_factory=list)
    model_bytes_included: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    thermal_state: dict[str, Any] = field(default_factory=dict)
    operation_history_policy: dict[str, Any] = field(default_factory=dict)
    gateway_config_metadata: dict[str, Any] = field(default_factory=dict)
    files: list[FileEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "backup_manifest_schema_version": BACKUP_MANIFEST_SCHEMA_VERSION,
            "application_release": self.application_release,
            "database_schema_version": self.database_schema_version,
            "backup_tool_version": self.backup_tool_version,
            "created_at": self.created_at,
            "legacy_import_provenance": self.legacy_import_provenance,
            "runtime_builds": list(self.runtime_builds),
            "known_good_lineage": self.known_good_lineage,
            "model_artifacts": list(self.model_artifacts),
            "model_bytes_included": self.model_bytes_included,
            "settings": dict(self.settings),
            "thermal_state": dict(self.thermal_state),
            "operation_history_policy": dict(self.operation_history_policy),
            "gateway_config_metadata": dict(self.gateway_config_metadata),
            "files": [f.to_dict() for f in self.files],
        }
        doc["manifest_digest"] = manifest_digest(doc)
        return doc


def build_backup_manifest(manifest: BackupManifest) -> dict[str, Any]:
    """Build + validate a manifest document. Refuses secret material and
    non-contained file paths (P8 §14.1/§14.2 fail-closed)."""
    doc = manifest.to_dict()
    _assert_no_secrets(doc)
    _assert_relative_containment([f.relative_path for f in manifest.files])
    return doc


def verify_manifest_digest(doc: dict[str, Any]) -> bool:
    """True when the recorded digest matches the recomputed digest."""
    recorded = doc.get("manifest_digest")
    if not recorded:
        return False
    return manifest_digest(doc) == recorded
