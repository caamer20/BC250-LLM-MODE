"""P6 §12.1: the Model Library read model.

Pins the §12.1 contract against a REAL temporary database: every library
field is surfaced (identity, provenance, digest, trust/storage, references,
fit, usage, pinned, deletion eligibility), identity is never a mutable tag,
and the CLI ``models library`` verb emits the same composed read model.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from bc250_llm_mode import model_library as library_mod
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.model_library import (
    MODEL_LIBRARY_SCHEMA_VERSION,
    ModelLibraryQueryService,
)
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repositories import ModelLibraryMetaRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

CATALOG_ID = "lfm25-26b"
CATALOG_QUANT = "Q4_K_M"
DIGEST = hashlib.sha256(b"tiny-model-bytes").hexdigest()


class LibraryWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.service = ModelLibraryQueryService(self.units, self.paths)

    def seed_artifact(
        self,
        artifact_id="art-1",
        *,
        content_digest=DIGEST,
        trust_state="VERIFIED",
        storage_state="MANAGED",
        catalog_id=None,
        quarantine_reason=None,
    ) -> None:
        with self.units.begin() as conn:
            conn.execute(
                "INSERT INTO model_artifacts (id, content_digest, byte_size,"
                " canonical_path, storage_state, trust_state, format,"
                " architecture, tensor_count, source_kind, source_repo,"
                " source_revision, catalog_id, license_id,"
                " quarantine_reason_code, created_at, validated_at) VALUES"
                " (?, ?, 16, '/models/tiny.gguf', ?, ?, 'gguf',"
                " 'llama', 32, 'catalog', 'org/repo', 'rev-1', ?, 'apache-2.0',"
                " ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (
                    artifact_id, content_digest, storage_state, trust_state,
                    catalog_id, quarantine_reason,
                ),
            )

    def seed_installation(
        self, alias="tiny", *, artifact_id="art-1", quant=CATALOG_QUANT
    ) -> None:
        with self.units.begin() as conn:
            conn.execute(
                "INSERT INTO model_installations (alias, path, quant,"
                " display_name, sampling_json, provenance, validation_status,"
                " imported_at, artifact_id) VALUES (?, '/models/tiny.gguf',"
                " ?, 'Tiny Model', '{}', 'test', 'validated',"
                " '2026-01-01T00:00:00Z', ?)",
                (alias, quant, artifact_id),
            )

    def set_active(self, alias: str) -> None:
        with self.units.begin() as conn:
            conn.execute(
                "INSERT INTO known_good_runtime (id, model_alias, context,"
                " slots, profile_id, runtime_json, runtime_fingerprint,"
                " runtime_component_identity, verified_at) VALUES"
                " (1, ?, 8192, 1, 'p', '{}', 'fp', 'llamacpp:sha256:abc',"
                " '2026-01-15T00:00:00Z')",
                (alias,),
            )


@pytest.fixture
def world(tmp_path):
    return LibraryWorld(tmp_path)


def test_model_library_module_is_query_only_by_construction():
    """AST guard: the library read model may only open READ units and may
    never import process/HTTP machinery (P6 §12.1)."""
    source = Path(library_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"subprocess", "httpx"}, alias.name
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"subprocess", "httpx"}, node.module
        if isinstance(node, ast.Attribute):
            # units.read() only — never units.begin()
            assert node.attr != "begin", "model library must be query-only"
    assert "elevated(" not in source


def test_empty_library_has_schema_version(world):
    doc = world.service.to_dict()
    assert doc["schema_version"] == MODEL_LIBRARY_SCHEMA_VERSION
    assert doc["count"] == 0
    assert doc["entries"] == []


def test_library_surfaces_every_12_1_field(world):
    world.seed_artifact(catalog_id=CATALOG_ID)
    world.seed_installation()
    entries = world.service.entries()
    assert len(entries) == 1
    e = entries[0]
    # identity / provenance
    assert e.alias == "tiny"
    assert e.display_name == "Tiny Model"
    assert e.artifact_id == "art-1"
    assert e.content_digest == DIGEST
    assert e.byte_size == 16
    assert e.format == "gguf"
    assert e.architecture == "llama"
    assert e.tensor_count == 32
    assert e.source_repo == "org/repo"
    assert e.source_revision == "rev-1"
    assert e.catalog_id == CATALOG_ID
    assert e.license_id == "apache-2.0"
    # trust / storage
    assert e.trust_state == "VERIFIED"
    assert e.storage_state == "MANAGED"
    assert e.quarantine_reason_code is None
    assert e.validation_status == "validated"
    # references
    assert e.active is False
    assert e.known_good is False
    # library meta defaults
    assert e.pinned is False
    assert e.last_used_at is None
    assert e.last_verified_inference_at is None
    assert e.benchmark_summary == {}


def test_identity_is_digest_not_mutable_tag(world):
    world.seed_artifact(catalog_id=CATALOG_ID)
    world.seed_installation()
    e = world.service.entries()[0]
    # The canonical identity is the immutable content digest + artifact id,
    # never the display name or a remote tag.
    assert e.content_digest == DIGEST
    assert e.artifact_id == "art-1"
    assert e.display_name != e.content_digest


def test_active_and_known_good_references(world):
    world.seed_artifact()
    world.seed_installation()
    world.set_active("tiny")
    e = world.service.entries()[0]
    assert e.active is True
    assert e.known_good is True


def test_fit_verdict_computed_for_catalog_model(world):
    world.seed_artifact(catalog_id=CATALOG_ID)
    world.seed_installation(quant=CATALOG_QUANT)
    e = world.service.entries(context=8192, slots=1)[0]
    assert e.fit_verdict in ("FITS", "TIGHT", "NO-FIT")
    assert e.fit_detail


def test_fit_unknown_without_catalog_entry(world):
    world.seed_artifact(catalog_id=None)
    world.seed_installation()
    e = world.service.entries()[0]
    assert e.fit_verdict is None
    assert e.fit_detail is None


def test_deletion_blockers_active_and_known_good(world):
    world.seed_artifact()
    world.seed_installation()
    world.set_active("tiny")
    e = world.service.entries()[0]
    assert e.deletion_eligible is False
    assert "active-model" in e.deletion_blockers
    assert "known-good-runtime" in e.deletion_blockers


def test_deletion_eligible_for_inactive_verified_model(world):
    world.seed_artifact()
    world.seed_installation()
    e = world.service.entries()[0]
    assert e.deletion_eligible is True
    assert e.deletion_blockers == ()


def test_quarantined_model_is_not_destructively_removable(world):
    world.seed_artifact(
        trust_state="QUARANTINED", storage_state="QUARANTINED",
        quarantine_reason="digest-mismatch")
    world.seed_installation()
    e = world.service.entries()[0]
    assert e.deletion_eligible is False
    assert "quarantined-inspect-first" in e.deletion_blockers
    assert e.quarantine_reason_code == "digest-mismatch"


def test_meta_repository_pin_and_usage(world):
    world.seed_artifact()
    world.seed_installation()
    with world.units.begin() as conn:
        meta = ModelLibraryMetaRepository(conn)
        meta.set_pin("tiny", True)
        meta.touch_used("tiny")
        meta.record_verified_inference(
            "tiny", {"tokens_per_second": 42.0})
    e = world.service.entries()[0]
    assert e.pinned is True
    assert e.last_used_at is not None
    assert e.last_verified_inference_at is not None
    assert e.benchmark_summary == {"tokens_per_second": 42.0}


def test_meta_cascades_on_alias_removal(world):
    world.seed_artifact()
    world.seed_installation()
    with world.units.begin() as conn:
        ModelLibraryMetaRepository(conn).set_pin("tiny", True)
    with world.units.begin() as conn:
        conn.execute("DELETE FROM model_installations WHERE alias = 'tiny'")
    with world.units.read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM model_library_meta WHERE alias='tiny'"
        ).fetchone()
    assert row["n"] == 0


def test_to_dict_round_trip(world):
    world.seed_artifact(catalog_id=CATALOG_ID)
    world.seed_installation()
    doc = world.service.to_dict(context=8192, slots=1)
    assert doc["count"] == 1
    entry = doc["entries"][0]
    assert entry["alias"] == "tiny"
    assert entry["content_digest"] == DIGEST
    assert entry["deletion_eligible"] is True
    # JSON-serializable end to end.
    json.dumps(doc)


def test_cli_models_library_verb(world, monkeypatch, capsys):
    from bc250_llm_mode import __main__ as entry
    from bc250_llm_mode.app import Application

    world.seed_artifact(catalog_id=CATALOG_ID)
    world.seed_installation()

    application = Application.compose(world.paths)
    monkeypatch.setattr(
        Application, "compose",
        classmethod(lambda cls, *a, **k: application))
    assert entry.cli(("models", "library")) == 0
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["schema_version"] == MODEL_LIBRARY_SCHEMA_VERSION
    assert doc["count"] == 1
    assert doc["entries"][0]["alias"] == "tiny"
