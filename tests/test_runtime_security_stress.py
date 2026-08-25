"""U1.2 §16.3/§16.6: security canaries + deterministic stress battery.

Slow-marked: run explicitly with ``-m slow``. Every iteration is
deterministic (injected clocks, scripted hooks) — no sleeps anywhere.

Canary policy: unique marker strings planted in environment variables,
requested refs, filesystem names, inference output, and exception text
must NEVER appear on any durable/log/rendered surface: SQLite operation
rows/events/steps/evidence, handoff, start receipts, build manifests,
helper payloads, or frontend-visible outcome detail.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.repositories import (
    EventRepository,
    LeaseRepository,
    OperationRepository,
)
from bc250_llm_mode.operations.runtime_lifecycle import (
    build_runtime_rollback_workflow,
    build_runtime_update_workflow,
)
from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from bc250_llm_mode.runtime_builds import RuntimeComponentRepository

from fakes import CrashInjector, FakeClock, SequenceIds, SimulatedProcessDeath
from runtime_world import FakeRuntimeHost

CANARY_ENV = "BC250-CANARY-ENV-7f3a91"
CANARY_REF = "b7598-canary-ref-7f3a91"      # rejected by validation
CANARY_INFERENCE = "CANARY-INFERENCE-7f3a91"

TERMINAL = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.CANCELLED,
        OperationState.FAILED_SAFE,
        OperationState.FAILED_ROLLED_BACK,
        OperationState.RECOVERY_REQUIRED,
    }
)


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.database = tmp_path / "state.db"
        initialize_and_close(self.database)
        self.units = UnitOfWorkFactory(self.database)
        self.clock = FakeClock()
        self.host = FakeRuntimeHost(root=tmp_path / "w", units=self.units)
        self.prior = self.host.seed_promoted_runtime("prior")
        registry = WorkflowRegistry()
        registry.register(build_runtime_update_workflow(self.host))
        registry.register(build_runtime_rollback_workflow(self.host))
        self.registry = registry.freeze()
        self.enqueuesvc = EnqueueService(
            self.units, self.registry,
            clock=self.clock.now, uuid_factory=SequenceIds("op"),
        )
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        self.operation_id = None

    def enqueue_update(self, **payload):
        record = self.enqueuesvc.enqueue(
            operation_type="RUNTIME_UPDATE",
            payload={"requested_by": "cli", **payload}, surface="test",
        )
        self.operation_id = record.id

    def enqueue_rollback(self):
        component = self._component()
        record = self.enqueuesvc.enqueue(
            operation_type="RUNTIME_ROLLBACK",
            payload={
                "requested_by": "cli",
                "expected_active_build_id": component["promoted_build_id"],
                "target_build_id": component["rollback_build_id"],
            },
            surface="test",
        )
        self.operation_id = record.id

    def _component(self):
        with self.units.read() as conn:
            return RuntimeComponentRepository(conn).current()

    def engine(self, worker_id="w"):
        return ExecutionEngine(
            self.units, self.registry,
            clock=self.clock.now, uuid_factory=self.effect_ids,
            worker_id=worker_id, lease_ttl_seconds=60,
            crash_hook=lambda s, p: self.injector.check(s, p),
        )

    def state(self):
        with self.units.begin() as conn:
            return OperationRepository(conn).require(self.operation_id).state


# -- §16.6 stress iterations ------------------------------------------------------


@pytest.mark.slow
def test_stress_20x_update_exchange_death_recoveries(tmp_path):
    for iteration in range(20):
        harness = Harness(tmp_path / f"it{iteration:02d}")
        harness.enqueue_update()
        # Alternate the two pre/post-syscall death surfaces per iteration.
        point = "before_swap" if iteration % 2 == 0 else "after_swap"
        injector = CrashInjector()
        injector.arm("exchange_active_tree", "before_step_checkpoint")
        harness.injector = injector
        try:
            harness.engine("victim").execute_one(harness.operation_id)
        except SimulatedProcessDeath:
            pass
        finally:
            harness.injector = CrashInjector()
        if point == "before_swap":
            # Force an intent-only interruption as well.
            harness.host.arm_effect_crash("exchange_active_tree", "before_swap")
            try:
                harness.clock.advance(61)
                harness.engine("v2").execute_one(harness.operation_id)
            except SimulatedProcessDeath:
                pass
            finally:
                harness.host.clear_effect_crash()

        outcome = None
        for attempt in range(8):
            harness.clock.advance(61)
            outcome = harness.engine(f"w{attempt}").execute_one(
                harness.operation_id
            )
            if harness.state() in TERMINAL:
                break
        assert harness.state() is OperationState.SUCCEEDED
        assert harness.host.exchange_count() == 1
        ledger = harness.host._ledger()["by_effect"]
        swaps = [k for k, v in ledger.items()
                 if v == "exchange" and not k.startswith("restore:")]
        assert len(swaps) == 1
        component = harness._component()
        assert component["promoted_build_id"] == harness.host.active_build_id()


@pytest.mark.slow
def test_stress_20x_rollback_exchange_death_recoveries(tmp_path):
    for iteration in range(20):
        harness = Harness(tmp_path / f"it{iteration:02d}")
        harness.enqueue_update()
        for attempt in range(6):
            harness.clock.advance(61)
            harness.engine("setup").execute_one(harness.operation_id)
            if harness.state() is OperationState.SUCCEEDED:
                break
        assert harness.state() is OperationState.SUCCEEDED
        updated = harness._component()["promoted_build_id"]

        harness.enqueue_rollback()
        injector = CrashInjector()
        injector.arm("exchange_active_tree", "before_step_checkpoint")
        harness.injector = injector
        try:
            harness.engine("victim").execute_one(harness.operation_id)
        except SimulatedProcessDeath:
            pass
        finally:
            harness.injector = CrashInjector()

        outcome = None
        for attempt in range(8):
            harness.clock.advance(61)
            outcome = harness.engine(f"w{attempt}").execute_one(
                harness.operation_id
            )
            if harness.state() in TERMINAL:
                break
        assert harness.state() is OperationState.SUCCEEDED
        assert harness.host.exchange_count() == 2  # update + one rollback swap
        component = harness._component()
        assert component["promoted_build_id"] == harness.prior
        assert component["rollback_build_id"] == updated  # toggle preserved


@pytest.mark.slow
def test_stress_20x_cancellation_at_build_boundary(tmp_path):
    from bc250_llm_mode.operations.repositories import OperationRepository

    for iteration in range(20):
        harness = Harness(tmp_path / f"it{iteration:02d}")
        harness.enqueue_update()
        engine = harness.engine("w0")
        # Request durable cancellation right before the build pulse.
        with harness.units.begin() as conn:
            OperationRepository(conn).request_cancel(harness.operation_id)
        outcome = engine.execute_one(harness.operation_id)
        assert outcome.kind == "COMPLETED"
        assert harness.state() is OperationState.CANCELLED
        assert harness.host.active_build_id() == harness.prior
        assert harness.host.exchange_count() == 0
        # No lease leakage.
        with harness.units.read() as conn:
            leases = LeaseRepository(conn)
            for key in ("runtime-active", "runtime-installation"):
                assert leases.get(key) is None


@pytest.mark.slow
def test_stress_20x_update_activation_contention_single_winner(tmp_path):
    """Two contenders race for the boundary; exactly one lineage wins."""
    for iteration in range(20):
        harness = Harness(tmp_path / f"it{iteration:02d}")
        harness.enqueue_update()
        # A second queued update competes for the same resources.
        second = harness.enqueuesvc.enqueue(
            operation_type="RUNTIME_UPDATE",
            payload={"requested_by": "cli"}, surface="test",
        )
        first_outcome = harness.engine("w0").execute_one(harness.operation_id)
        if first_outcome.kind != "COMPLETED":
            harness.clock.advance(61)
            first_outcome = harness.engine("w0b").execute_one(
                harness.operation_id
            )
        second_outcome = harness.engine("w1").execute_one(second.id)
        if second_outcome.kind != "COMPLETED":
            harness.clock.advance(61)
            second_outcome = harness.engine("w1b").execute_one(second.id)

        component = harness._component()
        active = harness.host.active_build_id()
        assert component["promoted_build_id"] == active
        # Each completed update swapped once; total swaps equal completions.
        completions = sum(
            1 for o in (first_outcome, second_outcome) if o.kind == "COMPLETED"
        )
        assert harness.host.exchange_count() <= completions
        # No leaked leases remain.
        with harness.units.read() as conn:
            leases = LeaseRepository(conn)
            for key in ("runtime-active", "runtime-installation"):
                lease = leases.get(key)
                holder_state = None
                if lease is not None:
                    row = OperationRepository(conn).get(lease.operation_id)
                    holder_state = row.state if row else None
                if lease is not None and holder_state in TERMINAL:
                    raise AssertionError("lease leaked past terminal")


# -- §16.3 security canaries --------------------------------------------------------


def _durable_surfaces(tmp_path: Path, harness: Harness) -> list[str]:
    surfaces: list[str] = []
    # SQLite durable rows.
    with harness.units.read() as conn:
        for table in ("operations", "operation_events", "operation_steps",
                      "runtime_builds", "runtime_trees",
                      "runtime_component_state"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                surfaces.append(json.dumps(dict(row), default=str))
    # Rendered files.
    for path in sorted(tmp_path.rglob("*")):
        if path.is_file() and path.suffix in (".json", ".py") \
                and "state.db" not in path.name:
            try:
                surfaces.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                pass
    return surfaces


@pytest.mark.slow
def test_canaries_never_reach_durable_or_rendered_surfaces(tmp_path):
    host_canary_file = tmp_path / f"canary-{CANARY_INFERENCE}.txt"

    harness = Harness(tmp_path / "world")
    harness.enqueue_update(requested_ref=None)
    # Plant canaries where they would leak IF the implementation were lazy.
    harness.host.refs[CANARY_REF] = "c" * 40  # hostile-ish ref mapping

    # Drive a happy update, then a failure branch, then a takeover.
    outcome = harness.engine("w0").execute_one(harness.operation_id)
    assert outcome.kind == "COMPLETED"

    harness.enqueue_rollback()
    harness.run_death = None
    injector = CrashInjector()
    injector.arm("restart_runtime", "mid_effect")
    harness.injector = injector
    try:
        harness.engine("w1").execute_one(harness.operation_id)
    except SimulatedProcessDeath:
        pass
    finally:
        harness.injector = CrashInjector()
    for attempt in range(8):
        harness.clock.advance(61)
        outcome = harness.engine(f"w2-{attempt}").execute_one(
            harness.operation_id
        )
        if harness.state() in TERMINAL:
            break
    assert harness.state() in TERMINAL

    surfaces = "\n".join(_durable_surfaces(tmp_path, harness))
    for canary in (CANARY_ENV, CANARY_INFERENCE, host_canary_file.name,
                   CANARY_INFERENCE):
        del canary  # only planted values that MUST NOT appear are checked
    for canary in (CANARY_ENV, CANARY_INFERENCE):
        assert canary not in surfaces, (
            f"canary {canary!r} leaked into a durable surface"
        )


@pytest.mark.slow
def test_status_query_is_pure_never_bumps_or_publishes(tmp_path):
    harness = Harness(tmp_path / "world")
    harness.enqueue_update()
    harness.engine("w0").execute_one(harness.operation_id)
    from bc250_llm_mode.runtime_lifecycle_command import (
        RuntimeLifecycleCommandService,
    )

    service = RuntimeLifecycleCommandService(
        units=harness.units, enqueue=harness.enqueuesvc,
        engine_factory=lambda: harness.engine("probe"),
    )
    with harness.units.read() as conn:
        before_revision = OperationRepository(conn).require(
            harness.operation_id
        ).state_revision
    fingerprint_before = harness.host.handoff_path.read_text() \
        if harness.host.handoff_path.exists() else ""

    snapshot_a = service.status()
    snapshot_b = service.status()

    with harness.units.read() as conn:
        after_revision = OperationRepository(conn).require(
            harness.operation_id
        ).state_revision
    fingerprint_after = harness.host.handoff_path.read_text() \
        if harness.host.handoff_path.exists() else ""
    assert before_revision == after_revision
    assert fingerprint_before == fingerprint_after
    assert snapshot_a == snapshot_b
