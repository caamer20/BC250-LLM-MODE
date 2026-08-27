"""P8 §14.1: backup manifest model (pure).

Pins the versioned backup manifest: every §14.1 identity field present, a
stable manifest digest, secret material refused, and relative path containment
enforced.
"""

from __future__ import annotations

import pytest

from bc250_llm_mode.backup_manifest import (
    BACKUP_MANIFEST_SCHEMA_VERSION,
    BackupManifest,
    BackupManifestError,
    FileEntry,
    build_backup_manifest,
    manifest_digest,
    verify_manifest_digest,
)


def _manifest(**overrides):
    defaults = dict(
        application_release="1.0.0",
        database_schema_version=9,
        created_at="2026-01-01T00:00:00Z",
        model_artifacts=[{"alias": "tiny", "sha256": "abc", "size_bytes": 10}],
        settings={"current_model": "tiny"},
        thermal_state={"latched": False},
        files=[FileEntry(relative_path="db.sqlite", sha256="abc",
                         size_bytes=100)],
    )
    defaults.update(overrides)
    return BackupManifest(**defaults)


def test_manifest_carries_every_identity_field():
    doc = build_backup_manifest(_manifest(
        legacy_import_provenance={"source": "legacy.json"},
        runtime_builds=[{"build_id": "llamacpp:sha256:abc"}],
        known_good_lineage={"runtime": "llamacpp:sha256:abc"},
        gateway_config_metadata={"provisioned": True},
        operation_history_policy={"retention_days": 30},
    ))
    assert doc["backup_manifest_schema_version"] == BACKUP_MANIFEST_SCHEMA_VERSION
    assert doc["application_release"] == "1.0.0"
    assert doc["database_schema_version"] == 9
    assert doc["backup_tool_version"]
    assert doc["legacy_import_provenance"] == {"source": "legacy.json"}
    assert doc["runtime_builds"] and doc["known_good_lineage"]
    assert doc["model_artifacts"] and doc["model_bytes_included"] is False
    assert doc["settings"] and doc["thermal_state"]
    assert doc["operation_history_policy"] and doc["gateway_config_metadata"]
    assert doc["files"][0]["relative_path"] == "db.sqlite"
    assert doc["manifest_digest"].startswith("sha256:")


def test_manifest_digest_is_stable_and_verifiable():
    doc = build_backup_manifest(_manifest())
    assert verify_manifest_digest(doc) is True
    # Tampering breaks the digest.
    tampered = dict(doc)
    tampered["application_release"] = "9.9.9"
    assert verify_manifest_digest(tampered) is False
    # Deterministic: same input -> same digest.
    assert manifest_digest(doc) == manifest_digest(build_backup_manifest(_manifest()))


def test_manifest_refuses_secret_material():
    with pytest.raises(BackupManifestError):
        build_backup_manifest(_manifest(
            settings={"hf_token": "secret-value"}))
    with pytest.raises(BackupManifestError):
        build_backup_manifest(_manifest(
            gateway_config_metadata={"api_key": "x"}))


def test_manifest_refuses_non_contained_paths():
    with pytest.raises(BackupManifestError):
        build_backup_manifest(_manifest(
            files=[FileEntry(relative_path="/etc/passwd", sha256="x",
                             size_bytes=1)]))
    with pytest.raises(BackupManifestError):
        build_backup_manifest(_manifest(
            files=[FileEntry(relative_path="../escape/file", sha256="x",
                             size_bytes=1)]))


def test_model_bytes_default_excluded():
    doc = build_backup_manifest(_manifest())
    assert doc["model_bytes_included"] is False
