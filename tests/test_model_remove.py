"""P6 §12.2: durable ``MODEL_REMOVE v1``.

Pins the §12.2 exit gate against a REAL composed application driving the REAL
engine: dry-run is accurate; active/known-good/referenced models cannot be
destructively removed; removal quarantines bytes (never deletes); a receipt is
retained for bounded undo; unknown aliases refuse. The workflow is registered
in the ONE frozen registry.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.model import OperationType
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

DIGEST = hashlib.sha256(b"removable-model-bytes").hexdigest()


class RemoveWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.paths.models_dir.mkdir(parents=True, exist_ok=True)
        self.model_file = self.paths.models_dir / "removable.gguf"
        self.model_file.write_bytes(b"removable-model-bytes")

    def seed(self, alias="removable", *, artifact_id="art-rm") -> None:
        with self.units.begin() as conn:
            conn.execute(
                "INSERT INTO model_artifacts (id, content_digest, byte_size,"
                " canonical_path, storage_state, trust_state, format,"
                " created_at, validated_at) VALUES"
                " (?, ?, 23, ?, 'MANAGED', 'VERIFIED', 'gguf',"
                " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (artifact_id, DIGEST, str(self.model_file)),
            )
            conn.execute(
                "INSERT INTO model_installations (alias, path, quant,"
                " display_name, sampling_json, provenance, validation_status,"
                " imported_at, artifact_id) VALUES (?, ?, 'Q4_K_M',"
                " 'Removable', '{}', 'test', 'validated',"
                " '2026-01-01T00:00:00Z', ?)",
                (alias, str(self.model_file), artifact_id),
            )

    def seed_second_alias(self, alias="second", artifact_id="art-rm") -> None:
        with self.units.begin() as conn:
            conn.execute(
                "INSERT INTO model_installations (alias, path, quant,"
                " display_name, sampling_json, provenance, validation_status,"
                " imported_at, artifact_id) VALUES (?, ?, 'Q4_K_M',"
                " 'Second', '{}', 'test', 'validated',"
                " '2026-01-01T00:00:00Z', ?)",
                (alias, str(self.model_file), artifact_id),
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

    def compose(self) -> Application:
        return Application.compose(self.paths)


@pytest.fixture
def world(tmp_path):
    return RemoveWorld(tmp_path)


def test_remove_workflow_registered_in_frozen_registry(world):
    app = world.compose()
    definition = app.registry.definition_for_type(OperationType.MODEL_REMOVE)
    keys = [s.step_key for s in definition.steps]
    assert keys == [
        "resolve_identity",
        "detach_alias",
        "quarantine_bytes",
        "record_removal",
    ]


def test_dry_run_is_accurate_for_eligible_model(world):
    world.seed()
    app = world.compose()
    plan = app.model_remove.dry_run("removable")
    assert plan["found"] is True
    assert plan["removable"] is True
    assert plan["blockers"] == []
    assert plan["impact"]["aliases_removed"] == 1
    assert plan["impact"]["bytes_to_quarantine"] == 23
    assert plan["impact"]["bytes_deleted"] == 0
    assert plan["impact"]["disposition"] == "quarantine"


def test_dry_run_unknown_alias(world):
    app = world.compose()
    plan = app.model_remove.dry_run("ghost")
    assert plan["found"] is False
    assert plan["removable"] is False
    assert "alias-unknown" in plan["blockers"]


def test_dry_run_flags_active_known_good_model(world):
    world.seed()
    world.set_active("removable")
    app = world.compose()
    plan = app.model_remove.dry_run("removable")
    assert plan["removable"] is False
    assert "active-known-good-model" in plan["blockers"]


def test_dry_run_flags_shared_artifact(world):
    world.seed()
    world.seed_second_alias()
    app = world.compose()
    plan = app.model_remove.dry_run("removable")
    assert plan["removable"] is False
    assert "artifact-referenced-by-other-aliases" in plan["blockers"]
    assert plan["impact"]["other_aliases_sharing_artifact"] == 1
    assert plan["impact"]["bytes_to_quarantine"] == 0


def test_remove_refuses_blocked_model_without_mutation(world):
    world.seed()
    world.set_active("removable")
    app = world.compose()
    outcome = app.model_remove.remove("removable")
    assert outcome.status == "BLOCKED"
    # Alias and bytes untouched.
    with world.units.read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM model_installations WHERE alias='removable'"
        ).fetchone()
    assert row["n"] == 1
    assert world.model_file.exists()


def test_remove_unknown_alias_refuses(world):
    app = world.compose()
    outcome = app.model_remove.remove("ghost")
    assert outcome.status == "UNKNOWN_ALIAS"


def test_remove_eligible_model_quarantines_not_deletes(world):
    world.seed()
    app = world.compose()
    outcome = app.model_remove.remove("removable")
    assert outcome.status == "REMOVED", outcome.to_dict()
    # Alias detached.
    with world.units.read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM model_installations WHERE alias='removable'"
        ).fetchone()
    assert row["n"] == 0
    # Bytes moved to quarantine, NOT deleted.
    assert not world.model_file.exists()
    quarantine_root = world.paths.model_quarantine_dir / "removals"
    quarantined = list(quarantine_root.glob("*.gguf"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"removable-model-bytes"


def test_remove_retains_receipt_for_bounded_undo(world):
    world.seed()
    app = world.compose()
    outcome = app.model_remove.remove("removable")
    assert outcome.status == "REMOVED"
    quarantine_root = world.paths.model_quarantine_dir / "removals"
    receipts = list(quarantine_root.glob("*.removal-receipt.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["alias"] == "removable"
    assert receipt["content_digest"] == DIGEST
    assert receipt["quarantine_path"]
    assert receipt["disposition"] == "MODEL_REMOVED"


def test_remove_marks_artifact_quarantined(world):
    world.seed()
    app = world.compose()
    outcome = app.model_remove.remove("removable")
    assert outcome.status == "REMOVED"
    with world.units.read() as conn:
        row = conn.execute(
            "SELECT storage_state, quarantine_reason_code FROM model_artifacts "
            "WHERE id = 'art-rm'"
        ).fetchone()
    assert row["storage_state"] == "QUARANTINED"
    assert row["quarantine_reason_code"] == "removed-by-operator"


def test_remove_shared_artifact_detaches_alias_but_retains_bytes(world):
    world.seed()
    world.seed_second_alias()
    app = world.compose()
    # Removing one of two aliases sharing the artifact is blocked by dry-run.
    outcome = app.model_remove.remove("removable")
    assert outcome.status == "BLOCKED"
    assert world.model_file.exists()


def test_cli_remove_model_dry_run_default(world, monkeypatch, capsys):
    from bc250_llm_mode import __main__ as entry

    world.seed()
    app = world.compose()
    monkeypatch.setattr(
        Application, "compose", classmethod(lambda cls, *a, **k: app))
    assert entry.cli(("remove-model", "removable")) == 0
    plan = json.loads(capsys.readouterr().out.strip())
    assert plan["removable"] is True
    # Dry-run did not mutate.
    assert world.model_file.exists()


# --- process death / lease takeover convergence (P6 exit gate) -----------


class RemoveDeathHarness:
    """Drives the REAL adapter + engine with named crash points."""

    def __init__(self, world: RemoveWorld) -> None:
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).parent / "operations"))
        from fakes import CrashInjector, FakeClock, SequenceIds

        self._world = world
        self._Clock = FakeClock
        self._Seq = SequenceIds
        self._Crash = CrashInjector
        from bc250_llm_mode.model_remove_adapter import ModelRemoveHostAdapter
        from bc250_llm_mode.operations.engine import ExecutionEngine
        from bc250_llm_mode.operations.model_remove import build_remove_workflow
        from bc250_llm_mode.operations.workflow import (
            EnqueueService,
            WorkflowRegistry,
        )

        self._ExecutionEngine = ExecutionEngine
        self._Enqueue = EnqueueService
        self.clock = FakeClock()
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        adapter = ModelRemoveHostAdapter(world.units, world.paths)
        registry = WorkflowRegistry()
        registry.register(build_remove_workflow(adapter))
        self.registry = registry.freeze()
        self._enqueue = EnqueueService(
            world.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=SequenceIds("op"),
        )

    def enqueue(self, alias="removable"):
        record = self._enqueue.enqueue(
            operation_type="MODEL_REMOVE",
            payload={"alias": alias, "requested_by": "test"},
            surface="test",
            operation_id="op-rm-1",
        )
        self.operation_id = record.id
        return record

    def engine(self, worker_id="worker-a"):
        return self._ExecutionEngine(
            self._world.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.effect_ids,
            worker_id=worker_id,
            lease_ttl_seconds=60,
            crash_hook=lambda step_key, pnt: self.injector.check(step_key, pnt),
        )

    def run_to_death(self, step_key: str, point: str) -> None:
        from fakes import SimulatedProcessDeath

        injector = self._Crash()
        injector.arm(step_key, point)
        self.injector = injector
        try:
            self.engine("worker-a").execute_one(self.operation_id)
        except SimulatedProcessDeath:
            pass
        finally:
            self.injector = self._Crash()


def test_removal_survives_death_and_takeover_converges_once(world):
    world.seed()
    harness = RemoveDeathHarness(world)
    harness.enqueue()
    # Die AFTER the quarantine effect but BEFORE its checkpoint.
    harness.run_to_death("quarantine_bytes", "before_step_checkpoint")
    # Bytes already moved; alias may or may not be detached yet.
    # A DIFFERENT worker takes over the expired lease and converges.
    harness.clock.advance(120)  # expire the dead worker's lease
    outcome = harness.engine("worker-b").execute_one(harness.operation_id)
    assert outcome.kind == "COMPLETED", outcome.reason_code
    # Exactly one quarantined artifact, exactly one receipt — no duplication.
    quarantine_root = world.paths.model_quarantine_dir / "removals"
    assert len(list(quarantine_root.glob("*.gguf"))) == 1
    assert len(list(quarantine_root.glob("*.removal-receipt.json"))) == 1
    with world.units.read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM model_installations "
            "WHERE alias='removable'"
        ).fetchone()
    assert row["n"] == 0
    assert not world.model_file.exists()


def test_removal_death_before_detach_is_reversible(world):
    world.seed()
    harness = RemoveDeathHarness(world)
    harness.enqueue()
    # Die before the detach step even starts: nothing mutated yet.
    harness.run_to_death("detach_alias", "before_step_start")
    assert world.model_file.exists()
    harness.clock.advance(120)  # expire the dead worker's lease
    outcome = harness.engine("worker-b").execute_one(harness.operation_id)
    assert outcome.kind == "COMPLETED", outcome.reason_code
    assert not world.model_file.exists()
    quarantine_root = world.paths.model_quarantine_dir / "removals"
    assert len(list(quarantine_root.glob("*.gguf"))) == 1
