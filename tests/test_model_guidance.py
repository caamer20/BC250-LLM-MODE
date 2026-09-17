"""Measured guidance never inherits evidence across artifact/runtime profiles."""
import copy
import datetime

import pytest

from bc250_llm_mode.model_guidance import measurement_for_preview, ModelGuidanceService


NOW = datetime.datetime(2026, 9, 17, tzinfo=datetime.timezone.utc)


def fixture():
    preview = {"expected_evidence_fingerprint": "e" * 64, "profile_fingerprint": "f" * 64,
        "profile_id": "builtin-interactive", "profile_revision": 1,
        "model_alias": "tiny", "runtime_component_identity": "runtime-a",
        "model_content_digest": "a" * 64, "context_per_slot": 8192, "slots": 1}
    candidate = {"profile_resolution_fingerprint": "f" * 64, "evidence_fingerprint": "e" * 64,
        "candidate_fingerprint": "c" * 64, "context_per_slot": 8192, "slots": 1}
    plan = {**preview, "candidates": [candidate]}
    sample = {**candidate, "candidate_index": 0, "status": "COMPLETE",
        "completed_at": "2026-09-16T12:00:00Z", "time_to_first_unit_ms": 150,
        "generation_per_second": 31.2, "generation_unit": "tokens/s", "peak_temperature_c": 66.5}
    return preview, [{"operation_id": "measured-operation", "plan": plan, "sample": sample}]


def test_exact_fresh_measurement_exposes_human_comparison_and_identity():
    preview, records = fixture()
    result = measurement_for_preview(preview, records, now=NOW)
    assert result.status == "CURRENT"
    assert result.first_response_ms == 150 and result.generation_per_second == 31.2
    assert result.peak_temperature_c == 66.5 and result.tested_context == 8192
    assert result.model_content_digest == "a" * 64 and result.runtime_identity == "runtime-a"
    assert "31.2 tokens/s" in result.summary and "0.15 s" in result.summary


@pytest.mark.parametrize("field,value", [("profile_revision", 2), ("model_alias", "other"),
    ("model_content_digest", "b" * 64), ("runtime_component_identity", "runtime-b"),
    ("context_per_slot", 16384), ("slots", 2), ("profile_fingerprint", "x" * 64),
    ("expected_evidence_fingerprint", "d" * 64)])
def test_other_setup_never_inherits_measurement(field, value):
    preview, records = fixture()
    preview[field] = value
    assert measurement_for_preview(preview, records, now=NOW).status == "NOT_MEASURED"


def test_stale_missing_and_unknown_units_are_honest():
    preview, records = fixture()
    records[0]["sample"]["completed_at"] = "2026-01-01T00:00:00Z"
    assert measurement_for_preview(preview, records, now=NOW).status == "STALE"
    records[0]["sample"].pop("generation_unit")
    assert "units/s" in measurement_for_preview(preview, records, now=NOW).summary
    records[0]["sample"]["peak_temperature_c"] = None
    assert "peak unknown" in measurement_for_preview(preview, records, now=NOW).summary
    records[0]["sample"]["generation_per_second"] = float("nan")
    assert measurement_for_preview(preview, records, now=NOW).status == "NOT_MEASURED"


def test_query_returns_only_completed_checkpointed_calibration_metrics(tmp_path):
    # Exercise the actual bounded SQL through the existing durable engine.
    from test_performance_coach_calibration_exp3 import test_production_calibration_adapter_restores_and_records_exact_evidence
    test_production_calibration_adapter_restores_and_records_exact_evidence(tmp_path)
    from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
    from bc250_llm_mode.workload_profiles import WorkloadProfileQueryService
    databases = list(tmp_path.rglob("*.db"))
    assert len(databases) == 1
    units = UnitOfWorkFactory(databases[0])
    service = ModelGuidanceService(units, WorkloadProfileQueryService(units))
    records = service.records()
    assert records and len(records) <= 3
    result = service.for_preview(service._profiles.preview("builtin-interactive"))
    assert result.status in {"CURRENT", "STALE"}
    assert result.generation_unit == "units/s"  # fixture has no token counter
