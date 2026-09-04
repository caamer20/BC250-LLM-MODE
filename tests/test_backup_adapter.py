"""C2 §C2.3/§C2.4/§C2.7: backup/restore host adapter (fake-world gate).

Exercises the production adapter's exact semantics in a temporary profile:
the backup create round trip (snapshot -> stage -> no-replace publish -> verify
-> record), archive-collision refusal, secret-free manifest, and the restore
path (digest-bound source validation -> contained staging -> staged validation
-> profile exchange -> post-verify -> promote). A fake exchange stands in for
the Linux-only atomic renameat2 so the restore logic is verified on any
platform; the real helper is gated to Linux in its own test.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from bc250_llm_mode.backup_adapter import MANIFEST_NAME, BackupHostAdapter
from bc250_llm_mode.db import initialize_file
from bc250_llm_mode.operations.backup import (
    BackupCreateRequestV1,
    BackupRestoreRequestV1,
)
from bc250_llm_mode.operations.validation import OperationValidationError
from bc250_llm_mode.operations.workflow import EffectContext
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


def _fake_exchange(active, candidate, *, approved_root):
    """Simulate renameat2 RENAME_EXCHANGE (swap two directory names)."""
    active_p, candidate_p = Path(active), Path(candidate)
    tmp = active_p.parent / (active_p.name + ".swap-tmp")
    active_p.rename(tmp)
    candidate_p.rename(active_p)
    tmp.rename(candidate_p)


def _profile(tmp_path):
    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    conn = initialize_file(paths.database_path)
    conn.close()
    return paths


def _ctx(operation_id, request, prior=None):
    return EffectContext(
        operation_id=operation_id,
        step_key="step",
        external_effect_id="eff-1",
        inputs={},
        prior_outputs=prior or {},
        request=request,
    )


def _seed_operation(units, op_id, op_type):
    """The adapter references operations(id); seed the row the engine would
    normally create so the FK is satisfied in the fake-world unit test."""
    with units.begin() as conn:
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version, "
            "request_json, state, created_at, updated_at) "
            "VALUES (?, ?, 1, '{}', 'RUNNING', 't0', 't1')",
            (op_id, op_type),
        )


def _create_backup(adapter, paths, units, op_id="op-bk-1",
                   label="backups/bk-1.tar"):
    _seed_operation(units, op_id, "BACKUP_CREATE")
    request = BackupCreateRequestV1(destination_label=label)
    ctx = _ctx(op_id, request)
    adapter.snapshot_database(ctx)
    adapter.inventory_and_stage(ctx)
    published = adapter.publish_archive(ctx)
    adapter.verify_archive(ctx)
    record = adapter.record_backup(ctx)
    with units.begin() as conn:
        conn.execute("UPDATE operations SET state = 'SUCCEEDED' WHERE id = ?", (op_id,))
    return request, ctx, published, record


def test_backup_create_round_trip(tmp_path):
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths)

    request, ctx, published, record = _create_backup(adapter, paths, units)

    archive = paths.backups_dir / "backups" / "bk-1.tar"
    assert archive.is_file()
    assert record["disposition"] == "BACKUP_CREATED"
    # The archive carries the secret-free manifest + snapshot only.
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
        doc = json.loads(tar.extractfile(MANIFEST_NAME).read())
    assert names == {MANIFEST_NAME, "state.db"}
    assert doc["model_bytes_included"] is False
    assert "manifest_digest" in doc
    # Staging was cleaned up.
    assert not (paths.staging_dir / f"backup-{ctx.operation_id}").exists()
    # The backup was recorded + verified.
    with units.read() as conn:
        row = conn.execute(
            "SELECT verification_state, encryption_mode FROM backup_sets "
            "WHERE backup_id = ?", (record["backup_id"],)).fetchone()
    assert row["verification_state"] == "verified"
    assert row["encryption_mode"] == "none"


def test_backup_collision_never_overwrites(tmp_path):
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths)
    _create_backup(adapter, paths, units, op_id="op-1", label="backups/dup.tar")
    # A second create to the same label refuses before clobbering.
    request = BackupCreateRequestV1(destination_label="backups/dup.tar")
    ctx = _ctx("op-2", request)
    adapter.snapshot_database(ctx)
    adapter.inventory_and_stage(ctx)
    with pytest.raises(OperationValidationError) as exc:
        adapter.publish_archive(ctx)
    assert "BACKUP_COLLISION" in str(exc.value)


def test_backup_destination_traversal_refused(tmp_path):
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths)
    request = BackupCreateRequestV1(destination_label="../escape.tar")
    ctx = _ctx("op-x", request)
    with pytest.raises(OperationValidationError):
        adapter._archive_path(request.destination_label)


def test_restore_round_trip_with_fake_exchange(tmp_path):
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths, exchange=_fake_exchange)

    # Create a backup, then mutate the live profile so restore is observable.
    _, _, _, record = _create_backup(adapter, paths, units, op_id="op-bk")
    digest = record["manifest_digest"]
    marker = paths.app_dir / "post-backup-marker.txt"
    marker.write_text("mutated-after-backup")

    request = BackupRestoreRequestV1(
        backup_id=record["backup_id"], confirmation_digest=digest)
    op_id = "op-rs-1"
    _seed_operation(units, op_id, "BACKUP_RESTORE")
    ctx = _ctx(op_id, request)

    from bc250_llm_mode.profile_access import profile_access
    with profile_access(paths.app_dir, exclusive=True):
        adapter.validate_source(ctx)
        adapter.stage_candidate(ctx)
        adapter.validate_staged(ctx)
        adapter.publish_exchange(ctx)
        adapter.verify_post_restore(ctx)
        terminal = adapter.promote_or_rollback(ctx)

    assert terminal["disposition"] == "RESTORE_PUBLISHED"
    # Files outside the database backup are preserved, never discarded.
    assert (paths.app_dir / "post-backup-marker.txt").read_text() == "mutated-after-backup"
    # ...and the restored database is present + intact.
    assert (paths.database_path).is_file()
    conn = sqlite3.connect(str(paths.database_path))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    # The prior profile is retained under the candidate dir for rollback.
    candidate = adapter._restore_staging_profile(op_id)
    assert candidate.exists()
    # The terminal restore record is in the restored DB (keyed by restore_id;
    # the restored DB has no row for this operation, so no operation FK).
    with units.read() as conn2:
        row = conn2.execute(
            "SELECT publish_state, post_verify_state FROM restore_attempts "
            "WHERE restore_id = ?", (f"rs-{op_id}",)).fetchone()
    assert row["publish_state"] == "published"
    assert row["post_verify_state"] == "passed"


def test_restore_confirmation_digest_mismatch_refused(tmp_path):
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths, exchange=_fake_exchange)
    _, _, _, record = _create_backup(adapter, paths, units, op_id="op-bk")

    request = BackupRestoreRequestV1(
        backup_id=record["backup_id"], confirmation_digest="f" * 64)
    ctx = _ctx("op-rs-bad", request)
    with pytest.raises(OperationValidationError) as exc:
        adapter.validate_source(ctx)
    assert "confirmation_digest" in str(exc.value)


def test_restore_unknown_backup_refused(tmp_path):
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths, exchange=_fake_exchange)
    request = BackupRestoreRequestV1(
        backup_id="nope", confirmation_digest="a" * 64)
    ctx = _ctx("op-rs-x", request)
    with pytest.raises(OperationValidationError) as exc:
        adapter.validate_source(ctx)
    assert "SOURCE_INVALID" in str(exc.value)
