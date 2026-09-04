"""C2 §C2.7: backup/restore command service driven through the REAL engine.

Composes a production ``Application`` over temporary paths and drives
``BACKUP_CREATE v1`` and ``BACKUP_RESTORE v1`` end to end through the shared
frozen registry + engine factory (enqueue -> execute_one -> terminal). The
restore's Linux-only atomic exchange is replaced with a platform-neutral fake
on the composed adapter so the full durable restore path is verified on any
platform; the real renameat2 helper stays Linux-gated in its own test.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.paths import AppPaths


def _fake_exchange(active, candidate, *, approved_root):
    active_p, candidate_p = Path(active), Path(candidate)
    tmp = active_p.parent / (active_p.name + ".swap-tmp")
    active_p.rename(tmp)
    candidate_p.rename(active_p)
    tmp.rename(candidate_p)


def _compose(tmp_path):
    paths = AppPaths.temporary(tmp_path / "profile")
    app = Application.compose(paths)
    app.backup._adapter._quiesce = lambda: {"active": False}
    return paths, app


def test_engine_backup_create_list_verify(tmp_path):
    paths, app = _compose(tmp_path)
    outcome = app.backup.create_backup("backups/bk.tar", requested_by="cli")
    assert outcome.ok and outcome.status == "CREATED"

    backups = app.backup.list_backups()
    assert len(backups) == 1
    bid = backups[0]["backup_id"]
    assert backups[0]["verification_state"] == "verified"
    assert app.backup.verify_backup(bid)["valid"] is True

    # restore inspect is a query-only dry run bound to the manifest digest.
    inspect = app.backup.restore_inspect(bid)
    assert inspect["restorable"] is True
    assert len(inspect["confirmation_digest"]) == 64


def test_engine_backup_create_refuses_collision(tmp_path):
    paths, app = _compose(tmp_path)
    assert app.backup.create_backup("backups/dup.tar").ok
    second = app.backup.create_backup("backups/dup.tar")
    assert not second.ok
    # The existing archive is untouched (exactly one backup recorded).
    assert len(app.backup.list_backups()) == 1


def test_engine_backup_create_refuses_encryption(tmp_path):
    paths, app = _compose(tmp_path)
    outcome = app.backup.create_backup("backups/e.tar", encrypt=True)
    assert outcome.status == "ENCRYPTION_UNAVAILABLE"
    assert app.backup.list_backups() == []


def test_engine_restore_round_trip(tmp_path):
    paths, app = _compose(tmp_path)
    # Use a platform-neutral exchange so the durable restore path runs here.
    app.backup._adapter._exchange = _fake_exchange

    assert app.backup.create_backup("backups/bk.tar").ok
    bid = app.backup.list_backups()[0]["backup_id"]
    digest = app.backup.restore_inspect(bid)["confirmation_digest"]

    # Mutate the live profile after the backup so the restore is observable.
    (paths.app_dir / "after-backup.txt").write_text("post-backup mutation")

    outcome = app.backup.restore_start(bid, digest, requested_by="cli")
    assert outcome.ok and outcome.status == "RESTORED"

    # Locally retained data survives the database configuration restore.
    assert (paths.app_dir / "after-backup.txt").read_text() == "post-backup mutation"
    conn = sqlite3.connect(str(paths.database_path))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_engine_restore_refuses_digest_mismatch(tmp_path):
    paths, app = _compose(tmp_path)
    app.backup._adapter._exchange = _fake_exchange
    assert app.backup.create_backup("backups/bk.tar").ok
    bid = app.backup.list_backups()[0]["backup_id"]
    outcome = app.backup.restore_start(bid, "0" * 64, requested_by="cli")
    assert outcome.status == "CONFIRMATION_MISMATCH"


def test_engine_restore_refuses_unknown_backup(tmp_path):
    paths, app = _compose(tmp_path)
    outcome = app.backup.restore_start("nope", "a" * 64)
    assert outcome.status == "UNKNOWN_BACKUP"
