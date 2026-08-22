"""R2 hardening P0-4: durable atomic publication and restrictive permissions.

- atomic writes fsync the temp file AND the parent directory
- failed publications leave the target untouched and clean up temp files
- newly created databases are 0600; app-owned sensitive directories 0700
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bc250_llm_mode import fsops
from bc250_llm_mode.db import connect, initialize
from bc250_llm_mode.paths import AppPaths


def test_atomic_write_is_durable_and_private(tmp_path, monkeypatch):
    target = tmp_path / "out" / "artifact.json"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")

    fsync_calls = []
    real_fsync = os.fsync
    monkeypatch.setattr(fsops.os, "fsync", lambda fd: (fsync_calls.append(fd), real_fsync(fd))[1])

    fsops.atomic_write_text(target, "new-content")

    assert target.read_text(encoding="utf-8") == "new-content"
    assert (os.stat(target).st_mode & 0o777) == 0o600
    # The temp file AND the parent directory were both fsynced.
    assert len(fsync_calls) >= 2
    assert [p.name for p in target.parent.iterdir()] == ["artifact.json"]


def test_failed_publication_leaves_target_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "artifact.json"
    target.write_text("original", encoding="utf-8")

    def broken_replace(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(fsops.os, "replace", broken_replace)
    with pytest.raises(OSError):
        fsops.atomic_write_text(target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [target], "temp file must be cleaned up"


def test_new_database_is_created_private(tmp_path):
    path = tmp_path / "fresh" / "state.db"
    path.parent.mkdir(parents=True)
    conn = connect(path)
    initialize(conn)
    assert (os.stat(path).st_mode & 0o777) == 0o600
    conn.close()


def test_sensitive_directories_are_private(tmp_path):
    paths = AppPaths.temporary(tmp_path / "root")
    paths.ensure_directories()
    for directory in (
        paths.app_dir, paths.logs_dir, paths.conversations_dir,
        paths.backups_dir, paths.staging_dir, paths.migration_receipts_dir,
    ):
        assert directory.exists()
        assert (os.stat(directory).st_mode & 0o777) == 0o700, str(directory)


def test_publish_staged_fsyncs_and_sets_mode(tmp_path, monkeypatch):
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"payload")
    target = tmp_path / "published.bin"

    fsync_calls = []
    real_fsync = os.fsync
    monkeypatch.setattr(fsops.os, "fsync", lambda fd: (fsync_calls.append(fd), real_fsync(fd))[1])

    fsops.publish_staged(staged, target)

    assert not staged.exists()
    assert target.read_bytes() == b"payload"
    assert (os.stat(target).st_mode & 0o777) == 0o600
    assert len(fsync_calls) >= 2  # staged file + parent directory