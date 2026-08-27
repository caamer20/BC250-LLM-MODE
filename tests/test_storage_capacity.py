"""P6 §12.3: capacity and deduplication reporting (query-only).

Pins the §12.3 contract against a REAL temporary profile: logical vs physical
size, dedup savings, staging/quarantine bytes, free space, low-space warning,
ranked cleanup suggestions, and a dry-run cleanup report that NEVER deletes.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from bc250_llm_mode import storage_capacity as capacity_mod
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.storage_capacity import (
    STORAGE_CAPACITY_SCHEMA_VERSION,
    StorageCapacityService,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

DIGEST_A = hashlib.sha256(b"model-a-bytes").hexdigest()
DIGEST_B = hashlib.sha256(b"model-b-bytes").hexdigest()


class CapacityWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.paths.models_dir.mkdir(parents=True, exist_ok=True)
        self.service = StorageCapacityService(self.units, self.paths)

    def seed_artifact(self, artifact_id, digest, size, path):
        with self.units.begin() as conn:
            conn.execute(
                "INSERT INTO model_artifacts (id, content_digest, byte_size,"
                " canonical_path, storage_state, trust_state, format,"
                " created_at, validated_at) VALUES"
                " (?, ?, ?, ?, 'MANAGED', 'VERIFIED', 'gguf',"
                " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (artifact_id, digest, size, str(path)),
            )

    def seed_alias(self, alias, artifact_id, path):
        with self.units.begin() as conn:
            conn.execute(
                "INSERT INTO model_installations (alias, path, quant,"
                " display_name, sampling_json, provenance, validation_status,"
                " imported_at, artifact_id) VALUES (?, ?, 'Q4_K_M',"
                " ?, '{}', 'test', 'validated',"
                " '2026-01-01T00:00:00Z', ?)",
                (alias, str(path), alias, artifact_id),
            )


@pytest.fixture
def world(tmp_path):
    return CapacityWorld(tmp_path)


def test_storage_capacity_module_is_query_only_by_construction():
    """AST guard: report-only, no writes/process/HTTP (P6 §12.3)."""
    source = Path(capacity_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"subprocess", "httpx"}, alias.name
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"subprocess", "httpx"}, node.module
        if isinstance(node, ast.Attribute):
            assert node.attr != "begin", "storage capacity must be query-only"
    assert "elevated(" not in source


def test_report_schema_and_empty_profile(world):
    report = world.service.report()
    assert report["schema_version"] == STORAGE_CAPACITY_SCHEMA_VERSION
    assert report["logical_unique_bytes"] == 0
    assert report["logical_installed_bytes"] == 0
    assert report["dedup_savings_bytes"] == 0
    assert report["staging_bytes"] == 0
    assert report["quarantine_bytes"] == 0
    assert report["reserved_bytes"] == 0
    assert report["free_bytes"] >= 0


def test_report_dedup_savings_for_shared_artifact(world):
    model_file = world.paths.models_dir / "a.gguf"
    model_file.write_bytes(b"model-a-bytes")
    world.seed_artifact("art-a", DIGEST_A, 13, model_file)
    # Two aliases share ONE artifact -> dedup savings = one copy.
    world.seed_alias("alias-1", "art-a", model_file)
    world.seed_alias("alias-2", "art-a", model_file)
    report = world.service.report()
    assert report["logical_unique_bytes"] == 13
    assert report["logical_installed_bytes"] == 26
    assert report["dedup_savings_bytes"] == 13


def test_report_physical_staging_quarantine_bytes(world):
    # Physical file in models_dir.
    (world.paths.models_dir / "real.gguf").write_bytes(b"x" * 100)
    # Staging leftover.
    staging = world.paths.model_staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "op-leftover.gguf").write_bytes(b"y" * 50)
    # Quarantine.
    quarantine = world.paths.model_quarantine_dir
    quarantine.mkdir(parents=True, exist_ok=True)
    (quarantine / "bad.gguf").write_bytes(b"z" * 25)
    report = world.service.report()
    assert report["physical_bytes"] >= 175
    assert report["staging_bytes"] == 50
    assert report["quarantine_bytes"] == 25


def test_low_space_warning_threshold(world):
    # Set an absurdly high threshold so the warning trips.
    service = StorageCapacityService(
        world.units, world.paths, low_space_bytes=10**18)
    report = service.report()
    assert report["low_space_warning"] is True
    assert report["low_space_threshold_bytes"] == 10**18


def test_cleanup_suggestions_ranked_by_safety(world):
    # Staging leftover (rank 1).
    staging = world.paths.model_staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "op-leftover.gguf").write_bytes(b"y" * 50)
    # Quarantine (rank 2).
    quarantine = world.paths.model_quarantine_dir
    quarantine.mkdir(parents=True, exist_ok=True)
    (quarantine / "bad.gguf").write_bytes(b"z" * 25)
    # Unreferenced artifact (rank 3).
    orphan = world.paths.models_dir / "orphan.gguf"
    orphan.write_bytes(b"orphan-bytes")
    world.seed_artifact("art-orphan", DIGEST_B, 12, orphan)
    suggestions = world.service.cleanup_suggestions()
    kinds = [s["kind"] for s in suggestions]
    assert kinds == [
        "staging-leftover", "quarantine", "unreferenced-artifact"]
    ranks = [s["rank"] for s in suggestions]
    assert ranks == sorted(ranks)
    # Every suggestion carries an exact identity + reason.
    for s in suggestions:
        assert s["identity"]
        assert s["reason"]
        assert s["bytes"] >= 0


def test_referenced_artifact_is_never_suggested(world):
    model_file = world.paths.models_dir / "referenced.gguf"
    model_file.write_bytes(b"referenced-bytes")
    world.seed_artifact("art-ref", DIGEST_A, 16, model_file)
    world.seed_alias("active-alias", "art-ref", model_file)
    suggestions = world.service.cleanup_suggestions()
    assert all(s["kind"] != "unreferenced-artifact" for s in suggestions)


def test_dry_run_cleanup_never_deletes(world):
    staging = world.paths.model_staging_dir
    staging.mkdir(parents=True, exist_ok=True)
    leftover = staging / "op-leftover.gguf"
    leftover.write_bytes(b"y" * 50)
    result = world.service.dry_run_cleanup()
    assert result["deleted_anything"] is False
    assert result["deleted_bytes"] == 0
    assert result["reclaimable_bytes"] >= 50
    assert result["note"] == "dry-run only; nothing was deleted"
    # The file is still physically present.
    assert leftover.exists()
    json.dumps(result)


def test_cli_storage_report_and_cleanup(world, monkeypatch, capsys):
    from bc250_llm_mode import __main__ as entry
    from bc250_llm_mode.app import Application

    app = Application.compose(world.paths)
    monkeypatch.setattr(
        Application, "compose", classmethod(lambda cls, *a, **k: app))
    assert entry.cli(("storage", "report")) == 0
    report = json.loads(capsys.readouterr().out.strip())
    assert report["schema_version"] == STORAGE_CAPACITY_SCHEMA_VERSION
    assert entry.cli(("storage", "cleanup")) == 0
    cleanup = json.loads(capsys.readouterr().out.strip())
    assert cleanup["deleted_anything"] is False
