"""P6 §12.4: bounded conversion gate — honestly unavailable in this build.

Pins the P6 exit gate: conversion "either passes the bounded operation gate or
remains visibly unavailable with an honest reason." This build ships no pinned,
verified converter, so every request must be refused BEFORE any external effect
with the single honest reason, and the request contract stays a known versioned
type.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.model import OperationType
from bc250_llm_mode.operations.model_convert import (
    CONVERTER_UNAVAILABLE_REASON,
    ModelConvertRequestV1,
    REQUEST_VERSION,
    availability,
    converter_available,
    decode_convert_request,
)
from bc250_llm_mode.operations.validation import (
    KNOWN_REQUEST_VERSIONS,
    OperationValidationError,
)
from bc250_llm_mode.paths import AppPaths


@pytest.fixture
def paths(tmp_path):
    p = AppPaths.temporary(tmp_path / "profile")
    initialize_and_close(p.database_path)
    return p


def test_convert_is_a_known_versioned_operation_type():
    assert OperationType.MODEL_CONVERT.value == "MODEL_CONVERT"
    assert KNOWN_REQUEST_VERSIONS[OperationType.MODEL_CONVERT] == 1
    assert REQUEST_VERSION == 1


def test_decode_convert_request_validates():
    req = decode_convert_request(
        {"source_alias": "tiny", "target_quantization": "Q4_K_M"}
    )
    assert isinstance(req, ModelConvertRequestV1)
    assert req.source_alias == "tiny"
    assert req.target_quantization == "Q4_K_M"
    with pytest.raises(OperationValidationError):
        decode_convert_request({"source_alias": "", "target_quantization": "Q4"})
    with pytest.raises(OperationValidationError):
        decode_convert_request(
            {"source_alias": "tiny", "target_quantization": "Q4", "bogus": 1}
        )


def test_converter_is_honestly_unavailable():
    assert converter_available() is False
    report = availability()
    assert report["available"] is False
    assert report["operation_type"] == "MODEL_CONVERT"
    assert report["reason"] == CONVERTER_UNAVAILABLE_REASON


def test_convert_refuses_before_any_external_effect(paths):
    app = Application.compose(paths)
    outcome = app.model_convert.convert("tiny", "Q4_K_M")
    assert outcome.status == "UNAVAILABLE"
    assert outcome.ok is False
    assert outcome.operation_id is None
    assert outcome.detail["reason"] == CONVERTER_UNAVAILABLE_REASON
    # No operation was enqueued.
    from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

    units = UnitOfWorkFactory(paths.database_path)
    with units.read() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM operations").fetchone()
    assert row["n"] == 0


def test_convert_availability_through_composition(paths):
    app = Application.compose(paths)
    report = app.model_convert.availability()
    assert report["available"] is False
    assert report["reason"] == CONVERTER_UNAVAILABLE_REASON


def test_cli_convert_model_reports_honest_unavailability(
    paths, monkeypatch, capsys):
    from bc250_llm_mode import __main__ as entry

    app = Application.compose(paths)
    monkeypatch.setattr(
        Application, "compose", classmethod(lambda cls, *a, **k: app))
    rc = entry.cli(("convert-model", "tiny", "Q4_K_M"))
    assert rc == 1  # unavailable -> non-zero
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["status"] == "UNAVAILABLE"
    assert doc["reason"] == CONVERTER_UNAVAILABLE_REASON
