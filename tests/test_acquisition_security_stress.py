"""U1.1 §9.3/§9.4: security canaries and no-sleep stress battery.

Marked ``slow`` like the other verification-battery gates: excluded from
the default suite by pyproject addopts and run explicitly via
``pytest -q -m slow tests/test_acquisition_security_stress.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

sys.path.insert(0, str(Path(__file__).parent / "operations"))

from acquisition_world import FakeAcquisitionHost  # noqa: E402
from fakes import CrashInjector, FakeClock, SequenceIds, SimulatedProcessDeath  # noqa: E402

from bc250_llm_mode.db import initialize_and_close  # noqa: E402
from bc250_llm_mode.operations.acquisition import build_import_workflow  # noqa: E402
from bc250_llm_mode.operations.engine import ExecutionEngine  # noqa: E402
from bc250_llm_mode.operations.model import OperationState  # noqa: E402
from bc250_llm_mode.operations.workflow import (  # noqa: E402
    EnqueueService,
    WorkflowRegistry,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory  # noqa: E402


class StressHarness:
    def __init__(self, tmp_path, host) -> None:
        self.root = tmp_path / "profile"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()
        self.host = host
        registry = WorkflowRegistry()
        registry.register(build_import_workflow(host))
        self.registry = registry.freeze()
        counter = {"n": 0}

        def op_ids():
            counter["n"] += 1
            return f"op-gen-{counter['n']}"

        self._enqueue = EnqueueService(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=op_ids,
        )

    def enqueue(self, source_path, *, operation_id):
        record = self._enqueue.enqueue(
            operation_type="MODEL_IMPORT",
            payload={"source_path": str(source_path)},
            surface="stress",
            operation_id=operation_id,
        )
        return record.id

    def engine(self, worker_id="w"):
        return ExecutionEngine(
            self.units,
            self.registry,
            clock=self.clock.now,
            uuid_factory=SequenceIds("eff"),
            worker_id=worker_id,
            lease_ttl_seconds=60,
        )


def _durable_text(units, operation_id: str) -> str:
    chunks = []
    with units.begin() as conn:
        for table, key in (
            ("operations", "id"),
            ("operation_steps", "operation_id"),
            ("operation_events", "operation_id"),
        ):
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {key} = ?",
                (operation_id,),
            ).fetchall()
            for row in rows:
                blob = dict(row)
                # The private request row may carry the absolute local path;
                # every OTHER durable surface must be clean.
                blob.pop("request_json", None)
                chunks.append(str(blob))
    return "\n".join(chunks)


def test_secret_and_path_canaries_never_reach_durable_evidence(tmp_path):
    """§9.3: token/path canaries are absent from every durable surface."""
    host = FakeAcquisitionHost(tmp_path / "canary-world")
    harness = StressHarness(tmp_path / "canary-profile", host)
    source = host.seed_local_source()
    operation_id = harness.enqueue(source, operation_id="op-canary")
    outcome = harness.engine().execute_one(operation_id)
    assert outcome.kind == "COMPLETED"
    durable = _durable_text(harness.units, operation_id)
    assert "HF_TOKEN_CANARY" not in durable
    assert "Authorization" not in durable
    assert "signed-redirect-CANARY" not in durable
    # The source basename must not leak into steps/events/progress.
    assert source.name not in durable
    # The request row (private) is allowed to carry the absolute path.
    with harness.units.begin() as conn:
        request_row = conn.execute(
            "SELECT request_json FROM operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
    assert str(source) in request_row["request_json"]


@pytest.mark.parametrize("iteration", range(20))
def test_stress_publication_death_takeover_converges_20_times(
    tmp_path, iteration
):
    """§9.4: publication death/takeover converges deterministically."""
    world_root = tmp_path / f"stress-{iteration}" / "world"
    profile = tmp_path / f"stress-{iteration}" / "profile"
    host = FakeAcquisitionHost(world_root)
    harness = StressHarness(profile, host)
    source = host.seed_local_source()
    operation_id = harness.enqueue(source, operation_id=f"op-s-{iteration}")

    injector = CrashInjector()
    injector.arm("publish_artifact", "before_step_checkpoint")
    engine = ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=SequenceIds("eff-a"),
        worker_id="worker-a",
        lease_ttl_seconds=60,
        crash_hook=lambda step_key, pnt: injector.check(step_key, pnt),
    )
    try:
        engine.execute_one(operation_id)
    except SimulatedProcessDeath:
        pass
    assert host.final_path().exists()

    harness.clock.advance(120)
    outcome = ExecutionEngine(
        harness.units,
        harness.registry,
        clock=harness.clock.now,
        uuid_factory=SequenceIds("eff-b"),
        worker_id="worker-b",
        lease_ttl_seconds=60,
    ).execute_one(operation_id)

    assert outcome.kind == "COMPLETED"
    assert host.counts["transfer"] == 1
    assert host.counts["materialize"] == 1
    assert host.counts["publication"] == 1
    assert host.counts["registration"] == 1
    with harness.units.begin() as conn:
        record = conn.execute(
            "SELECT state FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        assert record["state"] == OperationState.SUCCEEDED.value
    import gc

    gc.collect()


@pytest.mark.parametrize("iteration", range(20))
def test_stress_duplicate_content_operations_reuse_exactly(tmp_path, iteration):
    """§9.4: duplicate content across sequential operations stays exact."""
    world_root = tmp_path / f"dups-{iteration}" / "world"
    profile = tmp_path / f"dups-{iteration}" / "profile"
    host = FakeAcquisitionHost(world_root)
    harness = StressHarness(profile, host)
    source = host.seed_local_source()
    first = harness.enqueue(source, operation_id=f"op-d1-{iteration}")
    second = harness.enqueue(source, operation_id=f"op-d2-{iteration}")

    harness.engine("w-a").execute_one(first)
    harness.clock.advance(120)
    second_outcome = harness.engine("w-b").execute_one(second)
    assert second_outcome.kind == "COMPLETED"

    # Exactly one managed artifact file (the seeded external source and
    # staging intermediates are outside the managed namespace).
    managed = list((world_root / "artifacts").rglob("*.gguf"))
    assert len(managed) == 1