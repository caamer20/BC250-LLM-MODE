"""P2/U1.5: Activity Center contract — headless, over the real control plane.

The §8.2 state/action matrix is exercised through the PURE presentation
functions; the widget layer is constructed under stubbed tkinter against
a REAL composed application, and every action must route through
``application.operation_commands``. A static guard forbids GUI-side
persistence or host-infrastructure imports forever.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR / "operations"))

from _gui_stubs import install  # noqa: E402

install()

from fakes import CrashInjector, SequenceIds, SimulatedProcessDeath  # noqa: E402
from helpers import Harness  # noqa: E402

from bc250_llm_mode.db import initialize_and_close  # noqa: E402
from bc250_llm_mode.operations.command_service import OperationCommandService  # noqa: E402
from bc250_llm_mode.operations.engine import ExecutionEngine  # noqa: E402
from bc250_llm_mode.operations.model import OperationState  # noqa: E402
from bc250_llm_mode.operations.query_service import OperationQueryService  # noqa: E402
from bc250_llm_mode.operations.repositories import OperationRepository  # noqa: E402
from bc250_llm_mode.operations.workflow import EnqueueService  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402
from bc250_llm_mode.gui.activity import (  # noqa: E402
    ActivityCenterFrame,
    action_plan,
    headline,
    message_copy,
    progress_text,
    severity_of,
    severity_rank,
    support_text,
)


class World:
    """Harness + U1.4 control plane sharing one database and clock."""

    def __init__(self, tmp_path: Path) -> None:
        self.harness = Harness(tmp_path)
        self.units = self.harness.units
        self.clock = self.harness.clock
        self.effect_ids = SequenceIds("eff")
        self.injector = CrashInjector()
        self.enqueue = EnqueueService(
            self.units,
            self.harness.registry,
            clock=self.clock.now,
            uuid_factory=SequenceIds("op"),
        )
        self.commands = OperationCommandService(
            self.units,
            engine_factory=self.make_engine,
            enqueue=self.enqueue,
        )
        self.query = OperationQueryService(self.units, clock=self.clock.now)

    def make_engine(self, worker_id="activity-worker"):
        return ExecutionEngine(
            self.units,
            self.harness.registry,
            clock=self.clock.now,
            uuid_factory=self.effect_ids,
            worker_id=worker_id,
            lease_ttl_seconds=60,
            crash_hook=lambda s, p: self.injector.check(s, p),
        )

    def seed(self, *, desired="v1") -> str:
        self.harness.set_desired(desired)
        return self.enqueue.enqueue(
            operation_type="MODEL_ACTIVATE",
            payload={"desired_value": desired},
            surface="activity-test",
        ).id

    def cas(self, operation_id, *targets, result_code=None) -> None:
        with self.units.begin() as conn:
            ops = OperationRepository(conn)
            last = len(targets) - 1
            for index, target in enumerate(targets):
                record = ops.require(operation_id)
                ops.compare_and_transition(
                    operation_id,
                    expected_state=record.state,
                    expected_revision=record.state_revision,
                    target_state=target,
                    event_code=f"SEED_{target.value}",
                    event_summary="activity center test",
                    result_code=(
                        result_code if index == last and result_code else None
                    ),
                )


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


# -- pure §8.2 state matrix -------------------------------------------------------


def test_every_durable_state_renders_plain_language_with_severity(world):
    seen = {}
    states = [
        ("QUEUED", None),
        ("RUNNING", "crash"),
        ("PAUSED", None),
        ("SUCCEEDED", "drive"),
        ("FAILED_SAFE", "poison"),
        ("RECOVERY_REQUIRED", "barrier"),
    ]
    for index, (state, mode) in enumerate(states):
        if index:
            # Free resource leases held by earlier seeded operations.
            world.clock.advance(120)
        op = world.seed()
        if mode == "crash":
            world.injector.arm("apply_effect", "before_step_checkpoint")
            try:
                world.make_engine().execute_one(op)
            except SimulatedProcessDeath:
                pass
            finally:
                world.injector = CrashInjector()
        elif mode == "barrier":
            world.cas(op, OperationState.PREPARING, OperationState.RUNNING,
                      OperationState.RECOVERY_REQUIRED)
        elif state == "FAILED_SAFE" and mode == "poison":
            world.cas(op, OperationState.PREPARING, OperationState.RUNNING,
                      OperationState.FAILED_SAFE,
                      result_code="STEP_FAILED_SAFE")
        elif mode == "drive":
            world.make_engine().execute_one(op)
        elif state == "PAUSED":
            world.cas(op, OperationState.PAUSED)
        summary = world.query.show(op).summary
        assert summary.state == state, (op, summary.state)
        text = headline(summary)
        copy = message_copy(summary)
        assert text and copy and summary.recovery_recommendation
        seen[state] = (text, severity_of(state))

    assert "Waiting to start" in seen["QUEUED"][0]
    assert "Working" in seen["RUNNING"][0]
    assert "Paused safely" in seen["PAUSED"][0]
    assert "Completed" in seen["SUCCEEDED"][0]
    assert "ATTENTION REQUIRED" in seen["RECOVERY_REQUIRED"][0]
    # Recovery-required outranks everything; failures outrank new work.
    assert severity_rank("RECOVERY_REQUIRED") < severity_rank("SUCCEEDED")
    assert severity_rank("FAILED_SAFE") < severity_rank("QUEUED")
    assert severity_rank("PAUSED") < severity_rank("QUEUED")


def test_progress_never_fabricates_step_percentage_before_terminal():
    def make(state, current, total):
        return __import__(
            "bc250_llm_mode.operations.views", fromlist=["OperationSummary"]
        ).OperationSummary(
            operation_id="x", kind="MODEL_ACTIVATE", kind_version=1,
            title="t", state=state, phase=None, progress_current=current,
            progress_total=total, progress_unit="steps",
            created_at="", updated_at="", surface="s",
            cancellable=False, resumable=False, retryable=False,
            recoverable=False, dismissable=False,
            failure_code=None, recovery_recommendation="wait",
        )

    working = make("RUNNING", 3, 3)
    assert "%" not in progress_text(working)
    assert "100%" not in progress_text(working)
    done = make("SUCCEEDED", 3, 3)
    assert progress_text(done) == ""
    assert "%" not in progress_text(make("VERIFYING", 1, 4))


def test_action_availability_comes_from_summary_flags_only(world):
    queued = world.seed()
    keys = [a.key for a in action_plan(_sum(world, queued), world.commands)]
    assert keys == ["cancel"]
    world.cas(queued, OperationState.PAUSED)
    paused_keys = {a.key for a in action_plan(_sum(world, queued), world.commands)}
    # A paused operation can be resumed OR cancelled safely: both are
    # legal transitions of the durable state machine.
    assert paused_keys == {"cancel", "resume"}


def _sum(world, operation_id):
    return world.query.show(operation_id).summary


def test_message_copy_answers_what_next(world):
    copy = message_copy(_sum(world, world.seed()))
    assert "waiting" in copy.lower()
    assert "What you can do now" in copy


def test_support_text_identifies_without_leaking_paths(world):
    operation_id = world.seed()
    text = support_text(world.query.show(operation_id))
    assert operation_id in text and "MODEL_ACTIVATE" in text
    assert "/Users/" not in text


# -- widget layer under stubbed tkinter -------------------------------------------


class RecordingCommands:
    """Routes to the REAL commands while recording what the GUI invoked."""

    def __init__(self, real):
        self.real = real
        self.invoked = []

    def __getattr__(self, name):
        target = getattr(self.real, name)

        def call(*a, **k):
            self.invoked.append((name, a, k))
            return target(*a, **k)

        return call


def test_frame_constructs_selects_and_routes_actions_through_commands(
    tmp_path,
):
    paths = AppPaths.temporary(tmp_path / "profile")
    initialize_and_close(paths.database_path)
    application = _compose(paths)
    operation_id = _seed_via(application, tmp_path)

    frame = ActivityCenterFrame(master=None, application=application)
    page = application.operation_query.list(page_size=10)
    assert page.total >= 1
    frame._selected_id = operation_id
    frame.refresh()
    assert frame.rendered_summary is not None
    assert frame.rendered_summary.operation_id == operation_id

    recorded = RecordingCommands(application.operation_commands)
    application.operation_commands = recorded
    stop = [
        a for a in action_plan(frame.rendered_summary, recorded)
        if a.key == "cancel"
    ]
    assert len(stop) == 1
    stop[0].command()
    assert any(name == "cancel" for name, _a, _k in recorded.invoked)
    with _units(paths) as conn:
        state = OperationRepository(conn).require(operation_id).state
    assert state is OperationState.CANCEL_REQUESTED
    # GUI refresh reflects the new truth without owning persistence.
    frame.refresh()
    assert frame.rendered_summary.state == "CANCEL_REQUESTED"


def _compose(paths):
    from bc250_llm_mode.app import Application

    return Application.compose(paths)


def _seed_via(application, tmp_path) -> str:
    """Seed one QUEUED production operation through the composed graph."""
    import struct
    import uuid as _uuid

    from bc250_llm_mode.acquisition_adapter import AcquisitionHostAdapter
    from bc250_llm_mode.legacy_import import utcnow
    from bc250_llm_mode.operations.acquisition import build_import_workflow
    from bc250_llm_mode.operations.workflow import WorkflowRegistry
    from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

    def s(v):
        return struct.pack("<Q", len(v)) + v

    gguf = (
        b"GGUF" + struct.pack("<I", 3)
        + struct.pack("<Q", 1) + struct.pack("<Q", 1)
        + s(b"general.architecture") + struct.pack("<I", 8) + s(b"llama")
    )
    source = application.paths.app_dir / "incoming.gguf"
    source.write_bytes(gguf)
    units = UnitOfWorkFactory(application.paths.database_path)
    host = AcquisitionHostAdapter(
        application.paths, units, hub_client=None, clock=utcnow
    )
    registry = WorkflowRegistry()
    registry.register(build_import_workflow(host))
    svc = EnqueueService(
        units, registry.freeze(), clock=utcnow,
        uuid_factory=lambda: str(_uuid.uuid4()),
    )
    return svc.enqueue(
        operation_type="MODEL_IMPORT",
        payload={"source_path": str(source), "alias": "activity-model"},
        surface="test",
    ).id


def _units(paths):
    from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

    return UnitOfWorkFactory(paths.database_path).begin()


def test_gui_module_imports_no_persistence_or_host_infrastructure():
    source = (
        Path(__file__).parent.parent / "bc250_llm_mode" / "gui" /
        "activity.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_modules = {"sqlite3", "subprocess"}
    forbidden_names = {
        "repositories", "engine", "worker_service", "runtime_process",
        "unit_of_work", "db",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops = {a.name.split(".")[0] for a in node.names}
            assert not tops & forbidden_modules, tops
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            names = {top} if top else set()
            bad = names & (forbidden_modules | forbidden_names)
            assert not bad, f"Activity Center imports {bad}"
