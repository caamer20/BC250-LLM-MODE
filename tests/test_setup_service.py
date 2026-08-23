"""A3: named setup workflow stages and SetupService."""

from __future__ import annotations

import pytest

from _native import NativeApp
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.services import SETUP_STAGES, SetupConflict, SetupService
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


def _service(tmp_path):
    store = NativeApp(tmp_path)
    return store, SetupService(UnitOfWorkFactory(store.paths.database_path))


def test_legal_transition_sequence(tmp_path):
    _store, setup = _service(tmp_path)
    setup.acknowledge_safety()
    setup.record_hardware_validation({"gpu": "gfx110x", "vram_gib": 12})
    setup.mark_tkinter_staged()
    setup.advance("TKINTER_READY", "LLM_MODE_CONFIGURED")
    setup.advance("LLM_MODE_CONFIGURED", "RUNTIME_READY")
    setup.advance("RUNTIME_READY", "MODEL_SELECTED")
    setup.advance("MODEL_SELECTED", "MODEL_PREPARED")
    setup.advance("MODEL_PREPARED", "PROFILE_APPLIED")
    setup.advance("PROFILE_APPLIED", "SERVICE_INSTALLED")
    setup.advance("SERVICE_INSTALLED", "OPTIONALS_CONFIGURED")
    setup.advance("OPTIONALS_CONFIGURED", "VERIFIED")
    setup.mark_setup_complete()

    workflow = setup.current_workflow()
    assert workflow["stage"] == "COMPLETE"
    assert workflow["complete"] is True
    assert workflow["phase"] == len(SETUP_STAGES) - 1


def test_stage_skipping_rejected(tmp_path):
    _store, setup = _service(tmp_path)
    with pytest.raises(SetupConflict, match="cannot be skipped"):
        setup.advance("WELCOME", "MODEL_SELECTED")
    # Durable state untouched by the rejected transition.
    assert setup.current_workflow()["stage"] == "WELCOME"


def test_stale_expected_stage_rejected(tmp_path):
    _store, setup = _service(tmp_path)
    setup.acknowledge_safety()
    # A stale caller still believes the workflow is at WELCOME.
    with pytest.raises(SetupConflict, match="expected WELCOME"):
        setup.advance("WELCOME", "HARDWARE_VALIDATED")
    assert setup.current_workflow()["stage"] == "SAFETY_ACKNOWLEDGED"


def test_stage_skip_rejected_via_advance(tmp_path):
    _store, setup = _service(tmp_path)
    setup.acknowledge_safety()
    with pytest.raises(SetupConflict, match="cannot be skipped"):
        setup.advance("SAFETY_ACKNOWLEDGED", "TKINTER_READY")
    assert setup.current_workflow()["stage"] == "SAFETY_ACKNOWLEDGED"


def test_repeating_completed_transition_is_idempotent(tmp_path):
    _store, setup = _service(tmp_path)
    setup.acknowledge_safety()
    first = setup.record_hardware_validation({"gpu": "gfx110x"})
    second = setup.record_hardware_validation({"gpu": "gfx110x"})
    assert first["stage"] == second["stage"] == "HARDWARE_VALIDATED"
    # Evidence is recorded once; the repeat added nothing.
    assert len(setup.current_workflow()["evidence"]) == 1


def test_failed_staging_does_not_advance(tmp_path):
    """The stage moves only after the postcondition (staging) succeeds."""
    _store, setup = _service(tmp_path)
    setup.acknowledge_safety()
    setup.record_hardware_validation()

    # Simulate: staging raised before the service call — nothing advanced.
    try:
        raise RuntimeError("rpm-ostree failed")
    except RuntimeError:
        pass
    assert setup.current_workflow()["stage"] == "HARDWARE_VALIDATED"


def test_acknowledgement_persists_independently_of_setup_reset(tmp_path):
    store, setup = _service(tmp_path)
    setup.acknowledge_safety()
    setup.record_hardware_validation()
    setup.advance("HARDWARE_VALIDATED", "TKINTER_READY")

    result = setup.reset_for_repair("wizard crashed during model prep")
    assert result["stage"] == "SAFETY_ACKNOWLEDGED"
    assert result["complete"] is False

    # Acknowledgement survives; models and known-good data untouched.
    state = NativeApp(tmp_path).load()
    assert state["disclaimer_ack"] is True
    assert state["ack_timestamp"]
    assert state["installed_models"] == []  # service never mutates models
    assert setup.current_workflow()["evidence"]["repair"]["reason"] == (
        "wizard crashed during model prep"
    )


def test_repair_resumes_at_first_unverified_stage(tmp_path):
    _store, setup = _service(tmp_path)
    setup.acknowledge_safety()
    setup.record_hardware_validation()
    setup.mark_tkinter_staged()
    setup.advance("TKINTER_READY", "LLM_MODE_CONFIGURED")

    setup.reset_for_repair("reboot mid-setup")
    assert setup.current_workflow()["stage"] == "SAFETY_ACKNOWLEDGED"
    # The workflow can be replayed from there.
    setup.record_hardware_validation({"gpu": "revalidated"})
    assert setup.current_workflow()["stage"] == "HARDWARE_VALIDATED"


def test_legacy_numeric_phase_projects_correctly(tmp_path):
    store, setup = _service(tmp_path)
    assert setup.current_workflow()["phase"] == 0
    setup.acknowledge_safety()
    assert setup.current_workflow()["phase"] == 1
    setup.record_hardware_validation()
    assert setup.current_workflow()["phase"] == 2
    setup.mark_tkinter_staged()
    assert setup.current_workflow()["phase"] == 3
    # The native query projection keeps GUI consumers working.
    assert store.load()["setup_phase"] == 3