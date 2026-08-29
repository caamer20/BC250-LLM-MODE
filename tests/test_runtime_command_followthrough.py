"""U1.2 §15/§16.4 follow-through: Ctrl-C contract, button gating, and
settings isolation around the durable runtime lifecycle."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.engine import ExecutionOutcome
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.repositories import (
    OperationRepository,
)
from bc250_llm_mode.operations.runtime_lifecycle import (
    build_runtime_rollback_workflow,
    build_runtime_update_workflow,
)
from bc250_llm_mode.operations.workflow import WorkflowRegistry
from bc250_llm_mode.runtime_lifecycle_command import (
    RuntimeLifecycleCommandService,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

from _gui_stubs import install as _install_gui_stubs

_install_gui_stubs()

from fakes import FakeClock, SequenceIds  # noqa: E402


class InterruptingEngine:
    """First execute_one raises KeyboardInterrupt; then it succeeds."""

    def __init__(self, units) -> None:
        self.units = units
        self.calls = 0

    def execute_one(self, operation_id):
        self.calls += 1
        if self.calls == 1:
            raise KeyboardInterrupt()
        return ExecutionOutcome("COMPLETED", operation_id,
                                reason_code="CANCELLED")


class RaisingEngine:
    def __init__(self) -> None:
        self.calls = 0

    def execute_one(self, _operation_id):
        self.calls += 1
        raise KeyboardInterrupt()


@pytest.fixture()
def env(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    clock = FakeClock()
    class Env:
        pass

    e = Env()
    e.units = units
    e.clock = clock
    e.service = RuntimeLifecycleCommandService(
        units=units,
        enqueue=None,
        engine_factory=lambda: InterruptingEngine(units),
    )
    e.op_id = "op-ctrl-1"
    with units.begin() as conn:
        OperationRepository(conn).create(
            operation_type="RUNTIME_UPDATE",
            request={"requested_by": "cli"},
            surface="test",
            operation_id=e.op_id,
        )
    return e


def test_first_ctrl_c_requests_durable_cancel_and_keeps_driving(env):
    outcome = env.service._drive(env.op_id)

    assert outcome.reason_code == "CANCELLED"
    with env.units.read() as conn:
        row = OperationRepository(conn).require(env.op_id)
        assert row.cancel_requested_at is not None


def test_second_ctrl_c_reraises_leaving_operation_resumable(env):
    service = RuntimeLifecycleCommandService(
        units=env.units,
        enqueue=None,
        engine_factory=lambda: RaisingEngine(),
    )
    with pytest.raises(KeyboardInterrupt):
        service._drive(env.op_id)
    with pytest.raises(KeyboardInterrupt):
        service._drive(env.op_id)  # second interrupt re-raises immediately
    with env.units.read() as conn:
        row = OperationRepository(conn).require(env.op_id)
    # The first interrupt already requested durable cancellation.
    assert row.cancel_requested_at is not None


def test_recovery_outcome_surfaces_persisted_remediation(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    op_id = "op-recov-1"
    with units.begin() as conn:
        ops = OperationRepository(conn)
        ops.create(
            operation_type="RUNTIME_UPDATE",
            request={"requested_by": "cli"},
            surface="test",
            operation_id=op_id,
        )
        ops.compare_and_transition(
            op_id, expected_state=OperationState.QUEUED,
            expected_revision=1, target_state=OperationState.PREPARING,
        )
        record = ops.require(op_id)
        ops.compare_and_transition(
            op_id, expected_state=OperationState.PREPARING,
            expected_revision=record.state_revision,
            target_state=OperationState.RUNNING,
        )
        record = ops.require(op_id)
        import json as _json

        detail = _json.dumps({
            "step": "exchange_active_tree",
            "classification": "UNCERTAIN_MANUAL",
            "probe": "NO_ACTIVE_IDENTITY",
        })
        ops.record_terminal_result(
            op_id,
            terminal_state=OperationState.RECOVERY_REQUIRED,
            error_code="TREE_EXCHANGE_UNCERTAIN",
            error_detail=detail,
        )

    service = RuntimeLifecycleCommandService(
        units=units, enqueue=None, engine_factory=lambda: None,
    )
    outcome = service.resume(op_id)

    assert outcome.status == "RECOVERY_REQUIRED"
    assert outcome.detail["remediation"] == {
        "step": "exchange_active_tree",
        "classification": "UNCERTAIN_MANUAL",
        "probe": "NO_ACTIVE_IDENTITY",
    }


# -- §15.4 pure gating helpers ---------------------------------------------------


def test_button_gating_rules():
    from bc250_llm_mode.gui.system_page import llamacpp_button_states

    healthy = {"rollback_available": True}
    assert llamacpp_button_states(healthy) == {
        "update": "normal", "rollback": "normal"}

    barrier = {"rollback_available": True,
               "recovery_barrier": {"operation_id": "op-1"}}
    assert llamacpp_button_states(barrier) == {
        "update": "disabled", "rollback": "disabled"}

    active = {"rollback_available": True,
              "active_operation": {"operation_id": "op-2"}}
    assert llamacpp_button_states(active) == {
        "update": "disabled", "rollback": "disabled"}

    no_target = {"rollback_available": False}
    assert llamacpp_button_states(no_target) == {
        "update": "normal", "rollback": "disabled"}


def test_card_text_reports_barrier_active_and_promotion():
    from bc250_llm_mode.gui.system_page import llamacpp_card_text

    text = llamacpp_card_text({
        "promoted": {"short": "abc123def456"},
        "rollback_available": True,
    })
    assert "promoted build abc123def456" in text
    assert "foreground only" not in text

    busy = llamacpp_card_text({
        "active_operation": {"type": "RUNTIME_UPDATE",
                             "state": "RUNNING", "phase": "build",
                             "current": 2, "total": 3},
    })
    assert "RUNTIME_UPDATE RUNNING" in busy and "2/3" in busy
    assert "(foreground only)" in busy

    barrier = llamacpp_card_text({
        "recovery_barrier": {"operation_id": "op-abcdef1234567890"}})
    assert barrier.startswith("llama.cpp: RECOVERY REQUIRED")


# -- §16.4 settings isolation ------------------------------------------------------


def test_settings_write_cannot_clobber_runtime_lineage(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)

    from bc250_llm_mode.repositories import SettingsRepository
    from bc250_llm_mode.runtime_builds import (
        RuntimeComponentRepository, RuntimeTreeRepository,
    )

    with units.begin() as conn:
        components = RuntimeComponentRepository(conn)
        components.initialize()
        before = components.current()

        trees = RuntimeTreeRepository(conn)
        tree_before = [
            dict(r) for r in conn.execute(
                "SELECT * FROM runtime_trees ORDER BY tree_id"
            ).fetchall()
        ]

        settings = SettingsRepository(conn)
        settings.set_many({"disclaimer_ack": True})
        settings.set_revision(settings.revision() + 1)

    with units.read() as conn:
        after = RuntimeComponentRepository(conn).current()
        tree_after = [
            dict(r) for r in conn.execute(
                "SELECT * FROM runtime_trees ORDER BY tree_id"
            ).fetchall()
        ]
    assert before == after
    assert tree_before == tree_after
