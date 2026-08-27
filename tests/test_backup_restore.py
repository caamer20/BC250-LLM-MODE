"""P8 §14.2/§14.3: fail-closed backup restore validation (pure).

Pins the dry-run restore gate: tampered, partial, wrong-key, path-traversal,
newer-schema, unsupported-schema, low-space, permission, and identity failures
all refuse BEFORE any mutation, leaving the current profile untouched. A fully
valid archive passes with every check recorded.
"""

from __future__ import annotations

from bc250_llm_mode.backup_manifest import (
    BackupManifest,
    FileEntry,
    build_backup_manifest,
)
from bc250_llm_mode.backup_restore import (
    RestoreRefusalCode,
    validate_restore,
)


def _valid_doc(schema_version: int = 9):
    return build_backup_manifest(BackupManifest(
        application_release="1.0.0",
        database_schema_version=schema_version,
        files=[FileEntry(relative_path="db.sqlite", sha256="abc",
                         size_bytes=100)],
    ))


def _kwargs(**overrides):
    base = dict(
        manifest_doc=_valid_doc(),
        archive_complete=True,
        key_matches=True,
        contained_paths=True,
        current_schema_version=9,
        available_space_bytes=10_000,
        required_space_bytes=1_000,
        writable_target=True,
    )
    base.update(overrides)
    return base


def test_valid_archive_passes_all_checks():
    result = validate_restore(**_kwargs())
    assert result.ok is True
    assert result.refusal_code is None
    assert "manifest-integrity" in result.checks_passed
    assert "identity" in result.checks_passed


def test_missing_manifest_refuses():
    result = validate_restore(**_kwargs(manifest_doc=None))
    assert result.ok is False
    assert result.refusal_code == RestoreRefusalCode.INCOMPLETE_ARCHIVE.value


def test_tampered_manifest_refuses():
    doc = _valid_doc()
    doc["application_release"] = "9.9.9"  # breaks digest
    result = validate_restore(**_kwargs(manifest_doc=doc))
    assert result.ok is False
    assert result.refusal_code == RestoreRefusalCode.TAMPERED_MANIFEST.value


def test_incomplete_archive_refuses():
    result = validate_restore(**_kwargs(archive_complete=False))
    assert result.refusal_code == RestoreRefusalCode.INCOMPLETE_ARCHIVE.value


def test_wrong_key_refuses():
    result = validate_restore(**_kwargs(key_matches=False))
    assert result.refusal_code == RestoreRefusalCode.WRONG_KEY.value


def test_path_traversal_refuses():
    result = validate_restore(**_kwargs(contained_paths=False))
    assert result.refusal_code == RestoreRefusalCode.PATH_TRAVERSAL.value


def test_newer_schema_refuses():
    result = validate_restore(**_kwargs(
        manifest_doc=_valid_doc(schema_version=10),
        current_schema_version=9))
    assert result.refusal_code == RestoreRefusalCode.NEWER_SCHEMA.value


def test_unsupported_manifest_schema_refuses():
    doc = _valid_doc()
    doc["backup_manifest_schema_version"] = 99
    # Recompute is NOT done — the gate treats an unsupported manifest schema as
    # a refusal regardless of digest (digest check reads the recorded field).
    result = validate_restore(**_kwargs(manifest_doc=doc))
    assert result.refusal_code in (
        RestoreRefusalCode.UNSUPPORTED_SCHEMA.value,
        RestoreRefusalCode.TAMPERED_MANIFEST.value,
    )


def test_low_space_refuses():
    result = validate_restore(**_kwargs(
        available_space_bytes=500, required_space_bytes=1_000))
    assert result.refusal_code == RestoreRefusalCode.LOW_SPACE.value


def test_permission_denied_refuses():
    result = validate_restore(**_kwargs(writable_target=False))
    assert result.refusal_code == RestoreRefusalCode.PERMISSION_DENIED.value


def test_identity_mismatch_refuses():
    result = validate_restore(**_kwargs(identity_matches=False))
    assert result.refusal_code == RestoreRefusalCode.IDENTITY_MISMATCH.value


def test_result_serializes():
    doc = validate_restore(**_kwargs()).to_dict()
    assert doc["ok"] is True and doc["schema_version"] == 1
