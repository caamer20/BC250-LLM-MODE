"""R2.2: import failure recovery — no published database on any failure."""

import json
from pathlib import Path

import pytest

from bc250_llm_mode.legacy_import import LegacyImportError, import_legacy_state
from bc250_llm_mode.db import connect
from bc250_llm_mode.paths import AppPaths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def paths(tmp_path):
    paths = AppPaths.temporary(tmp_path)
    paths.ensure_directories()
    return paths


def _write_valid_legacy(paths):
    source = paths.app_dir / "state.json"
    payload = json.loads((FIXTURES / "state_v5.json").read_text(encoding="utf-8"))
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source


def test_corrupt_json_leaves_original_untouched_and_no_database(paths):
    source = paths.app_dir / "state.json"
    source.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(LegacyImportError, match="corrupt"):
        import_legacy_state(paths, source=source)
    assert source.read_text(encoding="utf-8") == "{ this is not json"
    assert not paths.database_path.exists()


def test_failure_before_publication_leaves_no_database(monkeypatch, paths):
    _write_valid_legacy(paths)
    monkeypatch.setattr(
        "bc250_llm_mode.legacy_import.LegacyImporter._import_mapped",
        lambda self, conn, migrated: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(RuntimeError, match="disk full"):
        import_legacy_state(paths)
    assert not paths.database_path.exists()
    assert not (paths.staging_dir / "state.db").exists()


def test_integrity_failure_blocks_publication(monkeypatch, paths):
    _write_valid_legacy(paths)
    monkeypatch.setattr("bc250_llm_mode.legacy_import.integrity_ok", lambda conn: False)
    with pytest.raises(LegacyImportError, match="integrity"):
        import_legacy_state(paths)
    assert not paths.database_path.exists()


def test_reimport_after_publication_is_refused_idempotently(paths):
    _write_valid_legacy(paths)
    import_legacy_state(paths)
    with pytest.raises(LegacyImportError, match="already exists"):
        import_legacy_state(paths)
    # The published database was untouched by the refused attempt.
    conn = connect(paths.database_path)
    count = conn.execute("SELECT COUNT(*) c FROM model_installations").fetchone()["c"]
    assert count == 1


def test_import_lock_prevents_concurrent_migrations(paths):
    _write_valid_legacy(paths)
    # Simulate a concurrent migration holding the lock.
    paths.migration_lock_path.write_text("", encoding="utf-8")
    with pytest.raises(LegacyImportError, match="import lock"):
        import_legacy_state(paths)
    assert not paths.database_path.exists()