"""Session 5A: secret rejection and payload bounds for operation persistence.

Secret-like keys are REJECTED (not stripped) so a redaction bug fails loudly
instead of leaking; size budgets are enforced before anything reaches
SQLite. Canaries prove nothing secret ever lands in the database.
"""

from __future__ import annotations

import sqlite3

import pytest

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.model import (
    OperationValidationError,
)
from bc250_llm_mode.operations.repositories import EventRepository, OperationRepository
from bc250_llm_mode.operations.validation import (
    REQUEST_MAX_BYTES,
    SUMMARY_MAX_CHARS,
    sanitize_payload,
    sanitize_request,
    sanitize_summary,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


class FakeClock:
    def __init__(self) -> None:
        import datetime

        self._datetime = datetime
        self._moment = datetime.datetime(2026, 8, 23, 12, 0, 0)

    def __call__(self) -> str:
        self._moment += self._datetime.timedelta(seconds=1)
        return self._moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def units(tmp_path):
    database = tmp_path / "ops.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


def _operations_count(database_path) -> int:
    conn = sqlite3.connect(str(database_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"hf_token": "secret-value"},
        {"model_id": "m", "api_key": "sk-123"},
        {"nested": {"Authorization": "Bearer abc"}},
        {"deep": [{"cookie": "session=1"}]},
        {"PASSWORD": "hunter2"},
        {"download_dir": "/models", "private_key_pem": "-----BEGIN"},
    ],
)
def test_secret_canary_keys_are_rejected_and_never_reach_sqlite(units, payload):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        with pytest.raises(OperationValidationError, match="secret-like"):
            ops.create(operation_type="MODEL_ACTIVATE", request=payload)
    assert _operations_count(units.database_path) == 0


def test_secret_canaries_rejected_in_event_details_too(units):
    created = None
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={})
        events = EventRepository(conn, clock=FakeClock())
        with pytest.raises(OperationValidationError, match="secret-like"):
            events.append(
                created.id,
                code="PROBE",
                summary="attempting",
                detail={"headers": {"authorization": "Bearer xyz"}},
            )
    assert _operations_count(units.database_path) >= 1
    # And the event was never written.
    conn = sqlite3.connect(str(units.database_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM operation_events").fetchone()[0]
    finally:
        conn.close()
    assert count == 1  # only the OPERATION_QUEUED event from create()


def test_request_size_bound_enforced(units):
    # Single long strings are truncated by design; the encoded-size budget is
    # the backstop that rejects genuinely oversized payloads.
    huge = {"chunks": ["x" * 4000] * 20}  # ~80 KiB canonical
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        with pytest.raises(OperationValidationError, match="maximum"):
            ops.create(operation_type="MODEL_ACQUIRE", request=huge)


def test_event_detail_size_bound_enforced(units):
    with units.begin() as conn:
        ops = OperationRepository(conn, clock=FakeClock())
        created = ops.create(operation_type="MODEL_ACTIVATE", request={})
        events = EventRepository(conn, clock=FakeClock())
        with pytest.raises(OperationValidationError, match="maximum"):
            events.append(
                created.id,
                code="BIG",
                summary="too much detail",
                detail={"chunks": ["y" * 4000] * 5},  # ~20 KiB canonical
            )


def test_long_strings_are_truncated_not_rejected():
    sanitized = sanitize_payload({"blob": "x" * (REQUEST_MAX_BYTES + 1)})
    assert len(sanitized.encode("utf-8")) <= REQUEST_MAX_BYTES


def test_unknown_request_versions_and_types_rejected():
    from bc250_llm_mode.operations.model import OperationType

    with pytest.raises(OperationValidationError, match="unknown request_version"):
        sanitize_request("MODEL_ACTIVATE", 99, {})
    with pytest.raises(OperationValidationError, match="unknown operation type"):
        sanitize_request("TELEPORT_MODEL", 1, {})
    # Known version accepted and canonically encoded.
    encoded = sanitize_request(OperationType.MODEL_ACTIVATE, 1, {"b": 2, "a": 1})
    assert encoded == '{"a":1,"b":2}'


def test_summaries_bounded_and_strings_truncated():
    assert sanitize_summary("x" * 10_000) == "x" * SUMMARY_MAX_CHARS
    sanitized = sanitize_payload({"long": "z" * 10_000})
    assert len(sanitized) < 8 * 1024
    assert "truncated" in sanitized


def test_unsupported_payload_types_rejected():
    with pytest.raises(OperationValidationError, match="unsupported payload type"):
        sanitize_payload({"bytes": b"\x00\x01"})
