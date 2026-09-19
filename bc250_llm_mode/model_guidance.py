"""Read-only comparisons of exact local model/profile calibration evidence."""
from __future__ import annotations

import datetime
import json
import math
from dataclasses import asdict, dataclass

from .model_recommendation import evidence_is_fresh

MAX_MEASUREMENT_ROWS = 96
GUIDED_PROFILES = ("builtin-interactive", "builtin-long-context", "builtin-shared", "builtin-cool")


def _number(value, *, low=0, high=1_000_000):
    return float(value) if type(value) in {int, float} and math.isfinite(value) and low <= value <= high else None


@dataclass(frozen=True)
class ModelMeasurement:
    status: str
    recorded_at: str | None = None
    first_response_ms: float | None = None
    generation_per_second: float | None = None
    generation_unit: str | None = None
    peak_temperature_c: float | None = None
    tested_context: int | None = None
    tested_slots: int | None = None
    operation_id: str | None = None
    model_content_digest: str | None = None
    runtime_identity: str | None = None
    profile_fingerprint: str | None = None

    def to_dict(self):
        return asdict(self)

    @property
    def summary(self):
        if self.status == "NOT_MEASURED":
            return "Not measured for this exact model, runtime and profile."
        prefix = "Measured locally" if self.status == "CURRENT" else "Historical measurement — recalibrate"
        first = "unknown" if self.first_response_ms is None else f"{self.first_response_ms / 1000:.2f} s"
        speed = "unknown" if self.generation_per_second is None else f"{self.generation_per_second:.1f} {self.generation_unit}"
        temperature = "unknown" if self.peak_temperature_c is None else f"{self.peak_temperature_c:.1f} °C"
        return (f"{prefix}: first response {first} · generation {speed} · peak {temperature} · "
                f"tested context {self.tested_context:,} × {self.tested_slots} slot(s).")


def measurement_for_preview(preview, records, *, now=None):
    expected = preview.get("expected_evidence_fingerprint")
    if not expected:
        return ModelMeasurement("NOT_MEASURED")
    matched = []
    for record in records[:MAX_MEASUREMENT_ROWS]:
        plan, sample = record.get("plan"), record.get("sample")
        if not isinstance(plan, dict) or not isinstance(sample, dict):
            continue
        candidates = plan.get("candidates")
        index = sample.get("candidate_index")
        if (not isinstance(candidates, list) or len(candidates) > 3 or type(index) is not int
                or not 0 <= index < len(candidates) or not isinstance(candidates[index], dict)):
            continue
        candidate = candidates[index]
        if (sample.get("status") != "COMPLETE" or sample.get("evidence_fingerprint") != expected
                or candidate.get("evidence_fingerprint") != expected
                or sample.get("candidate_fingerprint") != candidate.get("candidate_fingerprint")
                or sample.get("profile_resolution_fingerprint") != preview.get("profile_fingerprint")
                or candidate.get("profile_resolution_fingerprint") != preview.get("profile_fingerprint")
                or plan.get("profile_id") != preview.get("profile_id")
                or plan.get("profile_revision") != preview.get("profile_revision")
                or plan.get("model_alias") != preview.get("model_alias")
                or plan.get("runtime_component_identity") != preview.get("runtime_component_identity")
                or candidate.get("context_per_slot") != preview.get("context_per_slot")
                or candidate.get("slots") != preview.get("slots")):
            continue
        # Older plans omit the explicit digest; the resolution fingerprint
        # still binds it. If supplied, it must agree as well.
        if plan.get("model_content_digest") is not None and plan["model_content_digest"] != preview.get("model_content_digest"):
            continue
        recorded = sample.get("completed_at")
        if not isinstance(recorded, str) or len(recorded) > 40:
            continue
        try:
            stamp = datetime.datetime.fromisoformat(recorded.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue
        except ValueError:
            continue
        speed = _number(sample.get("generation_per_second"), low=0.000001)
        first = _number(sample.get("time_to_first_unit_ms"), high=600_000)
        if speed is None or first is None:
            continue
        unit = sample.get("generation_unit", "units/s")
        if unit not in {"tokens/s", "chunks/s", "units/s"}:
            continue
        matched.append((stamp, ModelMeasurement(
            "CURRENT" if evidence_is_fresh(recorded, now=now) else "STALE", recorded,
            first, speed, unit, _number(sample.get("peak_temperature_c"), high=150),
            int(candidate["context_per_slot"]), int(candidate["slots"]), str(record.get("operation_id", ""))[:128],
            preview.get("model_content_digest"), plan.get("runtime_component_identity"), preview.get("profile_fingerprint"))))
    return max(matched, key=lambda item: item[0])[1] if matched else ModelMeasurement("NOT_MEASURED")


class ModelGuidanceService:
    def __init__(self, units, profiles):
        self._units, self._profiles = units, profiles

    def records(self):
        # Bound both row count and JSON bytes before crossing the query boundary.
        with self._units.read() as conn:
            rows = conn.execute(
                "SELECT o.id, p.output_json AS plan, s.output_json AS sample FROM operations o "
                "JOIN operation_steps p ON p.operation_id=o.id AND p.step_key='resolve_plan' "
                "JOIN operation_steps s ON s.operation_id=o.id AND s.step_key IN "
                "('measure_candidate_1','measure_candidate_2','measure_candidate_3') "
                "WHERE o.operation_type='PROFILE_CALIBRATE' AND o.state='SUCCEEDED' "
                "AND p.state='VERIFIED' AND s.state='VERIFIED' "
                "AND length(p.output_json)<=16384 AND length(s.output_json)<=16384 "
                "ORDER BY o.updated_at DESC, o.id, s.step_key LIMIT ?", (MAX_MEASUREMENT_ROWS,)).fetchall()
        records = []
        for row in rows:
            try:
                records.append({"operation_id": row["id"], "plan": json.loads(row["plan"]), "sample": json.loads(row["sample"])})
            except (ValueError, TypeError, RecursionError):
                continue
        return tuple(records)

    def for_preview(self, preview):
        return measurement_for_preview(preview, self.records())

    def compare(self, model_alias, profile_ids=GUIDED_PROFILES):
        if not 1 <= len(profile_ids) <= 5 or len(set(profile_ids)) != len(profile_ids):
            raise ValueError("Compare one to five distinct profiles.")
        records = self.records()
        result = []
        for identifier in profile_ids:
            preview = self._profiles.preview(identifier, model_alias=model_alias)
            measurement = measurement_for_preview(preview, records)
            result.append({"preview": preview, "measurement": measurement.to_dict(), "summary": measurement.summary})
        return tuple(result)
