"""Session 6A final checkpoint §4: production local import tests.

Real filesystem effects through the real ``AcquisitionHostAdapter``; no
network, no fake acquisition host.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "operations"))

from fakes import CrashInjector, FakeClock, SequenceIds, SimulatedProcessDeath  # noqa: E402

from bc250_llm_mode.acquisition_adapter import (  # noqa: E402
    AcquisitionHostAdapter,
    HostError,
)
from bc250_llm_mode.db import initialize_and_close  # noqa: E402
from bc250_llm_mode.operations.acquisition import (  # noqa: E402
    build_import_workflow,
    decode_import_request,
)
from bc250_llm_mode.operations.engine import ExecutionEngine  # noqa: E402
from bc250_llm_mode.operations.model import OperationState  # noqa: E402
from bc250_llm_mode.operations.repositories import OperationRepository  # noqa: E402
from bc250_llm_mode.operations.workflow import (  # noqa: E402
    EnqueueService,
    WorkflowRegistry,
)
from bc250_llm_mode.paths import AppPaths  # noqa: E402
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory  # noqa: E402


def build_gguf(architecture: str = "llama") -> bytes:
    """Minimal bounded-parse GGUF with one metadata entry."""
    key = b"general.architecture"
    value = architecture.encode()
    meta = (
        struct.pack("<Q", len(key)) + key
        + struct.pack("<I", 8)
        + struct.pack("<Q", len(value)) + value
    )
    header = (
        b"GGUF" + struct.pack("<I", 2)
        + struct.pack("<Q", 1)   # tensor count (> 0 required)
        + struct.pack("<Q", 1)   # metadata count
    )
    return header + meta + b"\x00" * 64


class ProductionHarness:
    def __init__(self, tmp_path: Path, gguf_bytes: bytes) -> None:
        self.app_dir = tmp_path / "app"
        self.paths = AppPaths.from_app_dir(self.app_dir)
        self.paths.ensure_directories()
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.clock = FakeClock()
        self.host = AcquisitionHostAdapter(self.paths, self.units)
        self.operation_ids = SequenceIds("op")
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        registry = WorkflowRegistry()
        registry.register(build_import_workflow(self.host))
        self.registry = registry.freeze()
        self._enqueue = EnqueueService(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.operation_ids,
        )
        self.operation_id = None
        self.source = tmp_path / "external" / "my-model.gguf"
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_bytes(gguf_bytes)
        record = self._enqueue.enqueue(
            operation_type="MODEL_IMPORT",
            payload={"source_path": str(self.source)},
            surface="test",
            operation_id="op-prod-1",
        )
        self.operation_id = record.id

    def engine(self, worker_id="worker-a"):
        return ExecutionEngine(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=self.effect_ids,
            worker_id=worker_id,
            lease_ttl_seconds=60,
            crash_hook=lambda step_key, pnt: self.injector.check(step_key, pnt),
        )

    def run_to_death(self, step_key, point):
        injector = CrashInjector()
        injector.arm(step_key, point)
        self.injector = injector
        try:
            self.engine("worker-a").execute_one(self.operation_id)
        except SimulatedProcessDeath:
            pass
        finally:
            self.injector = CrashInjector()

    def state(self):
        with self.units.begin() as conn:
            ops = OperationRepository(conn, clock=FakeClock())
            return ops.require(self.operation_id)

    def final_artifacts(self):
        root = self.paths.models_dir / ".bc250-artifacts"
        return list(root.rglob("*.gguf"))


@pytest.fixture()
def harness(tmp_path):
    return ProductionHarness(tmp_path, build_gguf())


def test_production_import_copies_valid_gguf_to_managed_digest_path(harness):
    outcome = harness.engine().execute_one(harness.operation_id)
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code == "MODEL_INSTALLED"
    assert harness.state().state is OperationState.SUCCEEDED
    artifacts = harness.final_artifacts()
    assert len(artifacts) == 1
    final = artifacts[0]
    with harness.units.begin() as conn:
        row = conn.execute(
            "SELECT i.alias, a.canonical_path FROM model_installations i "
            "JOIN model_artifacts a ON a.id = i.artifact_id"
        ).fetchone()
        assert row is not None
        assert row["canonical_path"] == str(final)


def test_production_import_leaves_source_byte_and_metadata_identical(harness):
    before = (harness.source.read_bytes(), harness.source.stat().st_mtime_ns)
    harness.engine().execute_one(harness.operation_id)
    after = (harness.source.read_bytes(), harness.source.stat().st_mtime_ns)
    assert before == after


def test_production_import_refuses_symlink_source(tmp_path):
    real = tmp_path / "real.gguf"
    real.write_bytes(build_gguf())
    link = tmp_path / "link.gguf"
    link.symlink_to(real)
    app = tmp_path / "app"
    paths = AppPaths.from_app_dir(app)
    request = decode_import_request({"source_path": str(link)})
    host = AcquisitionHostAdapter(paths, None)
    with pytest.raises(HostError):
        host.observe_local_source(request)


def test_production_import_invalid_gguf_quarantines_without_alias(tmp_path):
    harness = ProductionHarness(tmp_path, b"not-a-gguf-at-all")
    outcome = harness.engine().execute_one(harness.operation_id)
    assert outcome.reason_code == "ARTIFACT_QUARANTINED"
    assert harness.state().state is OperationState.FAILED_SAFE
    quarantine_root = harness.paths.models_dir / ".bc250-quarantine"
    assert len(list(quarantine_root.rglob("*.gguf"))) == 1
    with harness.units.begin() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM model_installations").fetchone()
        assert n["n"] == 0


def test_production_import_publication_death_converges_exactly_once(harness):
    harness.run_to_death("publish_artifact", "before_step_checkpoint")
    assert harness.final_artifacts(), "artifact must exist before checkpoint"
    harness.clock.advance(120)
    outcome = harness.engine("worker-b").execute_one(harness.operation_id)
    assert outcome.kind == "COMPLETED"
    assert outcome.reason_code in ("MODEL_INSTALLED", "MODEL_REUSED")
    artifacts = harness.final_artifacts()
    assert len(artifacts) == 1
    with harness.units.begin() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM model_installations").fetchone()
        assert n["n"] == 1
        staging_gone = not (harness.paths.model_staging_dir / harness.operation_id).exists()
        assert staging_gone


def test_production_import_duplicate_digest_reuses_one_final_file(tmp_path):
    first = ProductionHarness(tmp_path / "dup1", build_gguf())
    first_outcome = first.engine().execute_one(first.operation_id)
    assert first_outcome.reason_code == "MODEL_INSTALLED"

    second = ProductionHarness(tmp_path / "dup2", build_gguf())
    second.engine().execute_one(second.operation_id)
    # Each profile has exactly one managed file for the same bytes.
    assert len(second.final_artifacts()) == 1