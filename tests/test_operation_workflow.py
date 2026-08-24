"""Session 5B: workflow identity, exact-version registry, atomic enqueue."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
ops_support = sys_path / "operations"
for _path in (sys_path, ops_support):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from bc250_llm_mode.operations.model import (
    OperationValidationError,
)
from bc250_llm_mode.operations.repositories import (
    EventRepository,
    StepRepository,
)
from bc250_llm_mode.operations.workflow import (
    WorkflowRegistry,
    WorkflowRegistryError,
    WorkflowVersionUnavailable,
)

from fakes import FakeClock
from helpers import (
    Harness,
    build_fake_workflow,
    decode_fake_request,
)


@pytest.fixture()
def harness(tmp_path):
    return Harness(tmp_path)


def test_exact_version_resolves(harness):
    definition = harness.registry.lookup("MODEL_ACTIVATE", 1, 1)
    assert definition is harness.workflow


def test_unknown_versions_are_never_guessed():
    registry = WorkflowRegistry()
    # Empty registry: nothing resolves for any version.
    with pytest.raises(WorkflowVersionUnavailable):
        registry.lookup("MODEL_ACTIVATE", 1, 1)
    with pytest.raises(WorkflowVersionUnavailable):
        registry.lookup("MODEL_ACTIVATE", 2, 1)
    with pytest.raises(WorkflowVersionUnavailable):
        registry.lookup("MODEL_ACQUIRE", 1, 1)


def test_duplicate_registration_rejected(harness):
    fresh = WorkflowRegistry()
    fresh.register(harness.workflow)
    with pytest.raises(WorkflowRegistryError, match="already registered"):
        fresh.register(harness.workflow)


def test_frozen_registry_refuses_registration(harness):
    with pytest.raises(WorkflowRegistryError, match="frozen"):
        harness.registry.register(harness.workflow)


def test_typed_decoder_rejects_invalid_and_mutable_payloads():
    with pytest.raises(OperationValidationError):
        decode_fake_request({})
    with pytest.raises(OperationValidationError):
        decode_fake_request({"desired_value": "   "})
    with pytest.raises(OperationValidationError):
        decode_fake_request({"desired_value": "v", "hf_token": "canary"})
    decoded = decode_fake_request({"desired_value": "v"})
    with pytest.raises(Exception):
        decoded.desired_value = "mutated"  # frozen dataclass must refuse


def test_enqueue_atomically_creates_operation_steps_and_event(tmp_path):
    harness = Harness(tmp_path)
    harness.set_desired("v7")
    record = harness.enqueue(desired_value="v7", operation_id="op-001")

    assert record.state.value == "QUEUED"
    with harness.units.begin() as conn:
        steps = StepRepository(conn, clock=FakeClock())
        events = EventRepository(conn, clock=FakeClock())
        step_rows = steps.list(record.id)
        assert [s.step_key for s in step_rows] == [
            "capture_prior",
            "apply_effect",
            "verify_effect",
            "publish",
            "verify_publication",
        ]
        assert [s.sequence for s in step_rows] == [1, 2, 3, 4, 5]
        assert all(s.state.value == "PENDING" for s in step_rows)
        assert len(events.list_after(record.id)) >= 1


def test_enqueue_decoder_failure_leaves_no_rows(tmp_path):
    harness = Harness(tmp_path)
    with pytest.raises(OperationValidationError):
        harness.enqueue(desired_value="")

    with harness.units.begin() as conn:
        count = conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        steps_count = conn.execute(
            "SELECT COUNT(*) FROM operation_steps"
        ).fetchone()[0]
        events_count = conn.execute(
            "SELECT COUNT(*) FROM operation_events"
        ).fetchone()[0]
    assert (count, steps_count, events_count) == (0, 0, 0)


def test_step_keys_sequences_and_versions_stable(harness):
    workflow = harness.workflow
    keys = [step.step_key for step in workflow.steps]
    assert len(keys) == len(set(keys)), "step keys must be unique"
    sequences = [step.sequence for step in workflow.steps]
    assert sequences == list(range(1, len(keys) + 1))
    versions = {step.implementation_version for step in workflow.steps}
    assert versions == {1}
    # Re-declaring yields identical rows.
    rebuilt = build_fake_workflow(
        harness.world, harness.recorder, harness.injector
    )
    assert [s.step_key for s in rebuilt.steps] == keys


def test_operations_package_has_no_forbidden_imports():
    import re

    package = Path(__file__).parent.parent / "bc250_llm_mode" / "operations"
    banned = (
        "tkinter", "gui", "chat", "__main__",
        "sqlite3", "systemd", "podman",
    )
    import_line = re.compile(r"^\s*(?:import|from)\s+([.\w]+)", re.MULTILINE)
    for py in sorted(package.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for match in import_line.finditer(text):
            module = match.group(1)
            for token in banned:
                assert token not in module.lower(), (
                    f"{py.name}: forbidden import of {module!r}"
                )


def test_engine_modules_do_not_import_frontend_layers():
    operations_dir = Path(__file__).parent.parent / "bc250_llm_mode" / "operations"
    import re

    import_line = re.compile(r"^\s*(?:import|from)\s+([.\w]+)", re.MULTILINE)
    frontend_markers = ("tkinter", "gui", "chat", "__main__")
    for py in sorted(operations_dir.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for match in import_line.finditer(text):
            module = match.group(1).lower()
            for marker in frontend_markers:
                assert marker not in module, (
                    f"{py.name}: imports {module!r}"
                )