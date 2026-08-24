"""Session 6A policy gates (U1.1): red before any production change.

Freezes the request-version vocabulary, the model-storage resource, the
quarantine terminal, and the closed TerminalDecision contract before any
migration, workflow, or adapter lands.
"""

from __future__ import annotations

import pytest

from bc250_llm_mode.operations.model import (
    OperationState,
    OperationType,
    OperationValidationError,
)


def test_model_import_joins_frozen_request_versions():
    from bc250_llm_mode.operations.validation import KNOWN_REQUEST_VERSIONS

    assert KNOWN_REQUEST_VERSIONS[OperationType.MODEL_ACQUIRE] == 1
    assert KNOWN_REQUEST_VERSIONS[OperationType.MODEL_IMPORT] == 1


def test_import_operation_type_exists_and_is_closed():
    assert OperationType.MODEL_IMPORT.value == "MODEL_IMPORT"
    assert OperationType("MODEL_IMPORT") is OperationType.MODEL_IMPORT


def test_model_storage_resource_key_is_stable():
    from bc250_llm_mode.operations.acquisition import ACQUISITION_RESOURCE

    assert ACQUISITION_RESOURCE == "model-storage"


def test_quarantine_terminal_codes_are_frozen():
    from bc250_llm_mode.operations.acquisition import (
        CODE_ARTIFACT_QUARANTINED,
        CODE_MODEL_INSTALLED,
        CODE_MODEL_REUSED,
    )

    assert CODE_MODEL_INSTALLED == "MODEL_INSTALLED"
    assert CODE_MODEL_REUSED == "MODEL_REUSED"
    assert CODE_ARTIFACT_QUARANTINED == "ARTIFACT_QUARANTINED"


def test_terminal_decision_states_are_closed_to_success_and_safe():
    """Workflow callbacks can pick only SUCCEEDED / FAILED_SAFE; rollback,
    recovery-required, and cancellation remain engine decisions."""
    from bc250_llm_mode.operations.acquisition import (
        AcquisitionTerminalDecision,
        decide_acquisition_terminal,
    )

    installed = decide_acquisition_terminal(
        {"validate_candidate": {"verdict": "ok"}},
        quarantined=False,
    )
    assert isinstance(installed, AcquisitionTerminalDecision)
    assert installed.state is OperationState.SUCCEEDED

    quarantined = decide_acquisition_terminal({}, quarantined=True)
    assert quarantined.state is OperationState.FAILED_SAFE
    assert quarantined.result_code == "ARTIFACT_QUARANTINED"

    with pytest.raises(OperationValidationError):
        decide_acquisition_terminal({}, quarantined=None)  # type: ignore[arg-type]
