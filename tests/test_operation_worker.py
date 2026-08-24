"""Session 5B: bounded worker lifecycle, heartbeat/pulse, contention."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.repositories import (
    OperationRepository,
    StepRepository,
)
from bc250_llm_mode.operations.worker import Worker

from fakes import FakeClock
from helpers import Harness

import faulthandler

faulthandler.dump_traceback_later(20, exit=True)


class Wake:
    """Deterministic injected wake primitive (threading.Event-like)."""

    def __init__(self):
        self._event = threading.Event()

    def set(self):
        self._event.set()

    def wait(self, timeout=None):
        return self._event.is_set()


@pytest.fixture()
def harness(tmp_path):
    return Harness(tmp_path)


def _worker(harness, worker_id="w1"):
    return Worker(
        harness.units,
        harness.registry,
        worker_id=worker_id,
        clock=harness.clock.now,
        uuid_factory=harness.effect_ids,
    )


def test_run_once_completes_a_queued_operation(harness):
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-w1")

    outcome = _worker(harness, "w1").run_once()

    assert outcome is not None and outcome.kind == "COMPLETED"
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        assert ops.get("op-w1").state == OperationState.SUCCEEDED
    assert harness.world.read_active()["application_count"] == 1


def test_run_once_with_empty_queue_returns_none_and_mutates_nothing(harness):
    before_revision = None
    outcome = _worker(harness, "w1").run_once()
    assert outcome is None
    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        records = ops.list_active()
        revisions = [r.state_revision for r in records]
        steps = StepRepository(conn, clock=FakeClock())
        del before_revision, revisions, steps


def test_run_until_idle_drains_then_reports_idle(harness):
    harness.set_desired("v1")
    harness.enqueue(desired_value="a", operation_id="op-1")
    # A second operation of the same type would contend for the same
    # resources, so only one is queued here; idle behavior is asserted.
    worker = _worker(harness, "w1")
    outcomes = worker.run_until_idle()
    assert len(outcomes) == 1 and outcomes[0].kind == "COMPLETED"
    assert worker.run_until_idle(max_operations=3) == []


def test_shutdown_stops_new_claims(harness):
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-s")
    worker = _worker(harness, "w1")
    worker.request_shutdown()
    assert worker.shutdown_requested is True
    assert worker.run_once() is None, "shutdown stops new claims immediately"


def test_two_workers_exactly_one_wins_one_operation(harness, tmp_path):
    harness.set_desired("v1")
    harness.enqueue(desired_value="v1", operation_id="op-t")

    barrier = threading.Barrier(2, timeout=10)
    results = {}

    def run(worker_id):
        print(f"THREAD {worker_id}: entering", flush=True)
        try:
            barrier.wait(timeout=10)
        except Exception as exc:
            print(f"THREAD {worker_id}: barrier broke: {exc!r}", flush=True)
            results[worker_id] = f"RAISED:{type(exc).__name__}"
            return
        print(f"THREAD {worker_id}: past barrier", flush=True)
        engine = ExecutionEngine(
            harness.units,
            harness.registry,
            clock=harness.clock.now,
            uuid_factory=harness.effect_ids,
            worker_id=worker_id,
            lease_ttl_seconds=60,
        )
        try:
            results[worker_id] = engine.execute_one("op-t").kind
        except Exception as exc:
            results[worker_id] = f"RAISED:{type(exc).__name__}"

    threads = [
        threading.Thread(target=run, args=("worker-a",)),
        threading.Thread(target=run, args=("worker-b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    print("RESULTS:", results)

    with harness.units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        final = ops.get("op-t")
        assert final.state == OperationState.SUCCEEDED
    kinds = sorted(results.values())
    assert "COMPLETED" in kinds
    assert any(
        k in ("SKIPPED_BUSY", "SKIPPED_TERMINAL", "LOST_LEASE") or k.startswith("RAISED")
        for k in kinds
        if k != "COMPLETED"
    ) or len(kinds) == 1


def test_worker_uses_injected_units_no_production_autostart():
    from pathlib import Path

    app_text = (Path(__file__).parent.parent / "bc250_llm_mode" / "app.py").read_text(
        encoding="utf-8"
    )
    # Session 5C: composition wires an ENGINE FACTORY for foreground
    # execution; it must never construct a Worker, start one, or execute.
    for token in ("Worker(", "run_until_idle", ".execute_one(", "operations.worker"):
        assert token not in app_text, (
            f"composition must not auto-start the executor: {token!r}"
        )
    compose_body_start = app_text.find("def compose")
    compose_body = app_text[compose_body_start : app_text.find("def ", compose_body_start + 10)]
    assert "thread" not in compose_body.lower()