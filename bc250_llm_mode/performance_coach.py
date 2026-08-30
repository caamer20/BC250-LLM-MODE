"""Bounded, query-only performance coaching for workload profiles."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


MAX_SUGGESTIONS = 3
SUGGESTION_CODES = (
    "FIT_HEADROOM_LOW",
    "CONTEXT_UNUSED_OR_CLIPPED",
    "BASELINE_UNATTRIBUTED",
    "THERMAL_MARGIN_LOW",
    "REPEATED_LOAD_FAILURE",
    "IDLE_POLICY_MISMATCH",
    "SMALLER_MODEL_MEETS_GOAL",
)


@dataclass(frozen=True)
class CoachSuggestion:
    code: str
    benefit: str
    tradeoff: str
    confidence: str
    evidence_recorded_at: str | None
    evidence_fingerprint: str | None
    preview: dict[str, Any]
    rollback_available: bool
    apply_action: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PerformanceCoachService:
    """Stable safety-first suggestions; performs no mutation or host I/O."""

    def __init__(self, units, *, profiles) -> None:
        self._units = units
        self._profiles = profiles

    @staticmethod
    def _action(preview: dict[str, Any]) -> dict[str, Any]:
        return {
            "command": "profiles apply",
            "profile_id": preview["profile_id"],
            "model_alias": preview["model_alias"],
            "expected_profile_revision": preview["profile_revision"],
            "preview_fingerprint": preview["profile_fingerprint"],
            "accept_tight_required": preview["tight_confirmation_required"],
            "automatic": False,
        }

    @staticmethod
    def _suggestion(
        code: str,
        preview: dict[str, Any],
        *,
        benefit: str,
        tradeoff: str,
        confidence: str | None = None,
    ) -> CoachSuggestion:
        return CoachSuggestion(
            code=code,
            benefit=benefit[:240],
            tradeoff=tradeoff[:240],
            confidence=confidence or str(preview.get("evidence_class") or "ESTIMATED"),
            evidence_recorded_at=preview.get("evidence_recorded_at"),
            evidence_fingerprint=preview.get("evidence_fingerprint"),
            preview=preview,
            rollback_available=bool(preview.get("rollback_available")),
            apply_action=PerformanceCoachService._action(preview),
        )

    def _observations(
        self,
        profile_fingerprint: str | None,
        runtime_component_identity: str | None,
    ) -> dict[str, Any]:
        from .repositories import (
            BenchHistoryRepository,
            RuntimeConfigRepository,
            ThermalStateRepository,
        )
        from .workload_profiles import WorkloadProfileRepository

        with self._units.read() as conn:
            history = BenchHistoryRepository(conn).list()[-20:]
            thermal = ThermalStateRepository(conn).get()
            runtime = RuntimeConfigRepository(conn).get()
            applied_idle_policy = None
            applied_profile_id = runtime.get("profile_id")
            if applied_profile_id:
                applied = WorkloadProfileRepository(conn).get(
                    str(applied_profile_id), include_deleted=True
                )
                if applied is not None:
                    applied_idle_policy = applied.idle_policy
            failures = 0
            if profile_fingerprint and runtime_component_identity:
                rows = conn.execute(
                    "SELECT o.state, o.error_code, s.output_json "
                    "FROM operations AS o JOIN operation_steps AS s "
                    "ON s.operation_id = o.id AND s.step_key = 'resolve_candidate' "
                    "WHERE o.operation_type = 'MODEL_ACTIVATE' "
                    "ORDER BY o.updated_at DESC LIMIT 20"
                ).fetchall()
                for row in rows:
                    if row["state"] not in {
                        "FAILED_SAFE", "FAILED_ROLLED_BACK", "RECOVERY_REQUIRED"
                    }:
                        continue
                    try:
                        candidate = json.loads(row["output_json"] or "{}")
                    except (TypeError, ValueError):
                        continue
                    if (
                        candidate.get("profile_fingerprint") == profile_fingerprint
                        and candidate.get("component_identity")
                        == runtime_component_identity
                    ):
                        failures += 1
        return {
            "history": history,
            "thermal": thermal,
            "matching_failures": failures,
            "applied_idle_policy": applied_idle_policy,
        }

    def suggestions(
        self,
        *,
        profile_id: str = "builtin-interactive",
        model_alias: str | None = None,
        requested_context: int | None = None,
        requested_users: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        from .workload_profiles import WorkloadProfileCommandError

        preview = self._profiles.preview(profile_id, model_alias=model_alias)
        observed = self._observations(
            preview.get("profile_fingerprint"),
            preview.get("runtime_component_identity"),
        )
        suggestions: list[CoachSuggestion] = []

        def add(item: CoachSuggestion) -> None:
            if len(suggestions) < MAX_SUGGESTIONS and item.code not in {
                existing.code for existing in suggestions
            }:
                suggestions.append(item)

        if preview["fit_verdict"] == "TIGHT" or float(preview["headroom_gib"]) < 1.5:
            alternative = self._profiles.preview(
                "builtin-cool", model_alias=preview["model_alias"]
            )
            add(self._suggestion(
                "FIT_HEADROOM_LOW",
                alternative,
                benefit="Leaves more fast-VRAM headroom for stable sustained use.",
                tradeoff="Uses a more conservative context or runtime shape.",
                confidence="ESTIMATED",
            ))

        if requested_context is not None and requested_context > int(
            preview["context_per_slot"]
        ):
            alternative = self._profiles.preview(
                "builtin-long-context", model_alias=preview["model_alias"]
            )
            add(self._suggestion(
                "CONTEXT_UNUSED_OR_CLIPPED",
                alternative,
                benefit="Resolves the largest model-valid context that fits the selected model.",
                tradeoff="Longer context consumes more KV memory and can reduce concurrency.",
                confidence="ESTIMATED",
            ))

        if str(preview.get("thermal_readiness")) != "READY":
            alternative = self._profiles.preview(
                "builtin-cool", model_alias=preview["model_alias"]
            )
            add(self._suggestion(
                "THERMAL_MARGIN_LOW",
                alternative,
                benefit="Prioritizes thermal margin before throughput.",
                tradeoff="May reduce sustained generation speed.",
                confidence="ESTIMATED",
            ))

        if int(observed["matching_failures"]) >= 2:
            alternative = self._profiles.preview(
                "builtin-cool", model_alias=preview["model_alias"]
            )
            add(self._suggestion(
                "REPEATED_LOAD_FAILURE",
                alternative,
                benefit="Uses a conservative verified-artifact profile after repeated matching failures.",
                tradeoff="Does not diagnose unrelated failures and never applies automatically.",
                confidence="MEASURED_LOCAL",
            ))

        if observed["history"] and preview.get("evidence_class") == "ESTIMATED":
            add(self._suggestion(
                "BASELINE_UNATTRIBUTED",
                preview,
                benefit="Calibration can produce an exact model/runtime/profile-attributed baseline.",
                tradeoff="Calibration takes time, heats the GPU, and still requires separate apply.",
                confidence="ESTIMATED",
            ))

        selected_idle = str(preview["profile"].get("idle_policy") or "KEEP_LOADED")
        applied_idle = observed.get("applied_idle_policy")
        if applied_idle is not None and applied_idle != selected_idle:
            add(self._suggestion(
                "IDLE_POLICY_MISMATCH",
                preview,
                benefit=(
                    f"Makes the selected {selected_idle} behavior explicit for the "
                    "current workload goal."
                ),
                tradeoff=(
                    f"The currently applied profile uses {applied_idle}; changing idle "
                    "behavior still requires a separate profile apply."
                ),
            ))

        if (
            requested_users is not None
            and requested_users > int(preview["slots"])
            and len(suggestions) < MAX_SUGGESTIONS
        ):
            with self._units.read() as conn:
                from .repositories import ModelInstallationsRepository

                aliases = tuple(
                    item["id"] for item in ModelInstallationsRepository(conn).list()[:32]
                    if item.get("artifact_trust_state") == "VERIFIED"
                )
            candidates = []
            for alias in aliases:
                try:
                    candidate = self._profiles.preview(
                        "builtin-shared", model_alias=alias
                    )
                except (
                    KeyError,
                    ValueError,
                    WorkloadProfileCommandError,
                ):
                    continue
                if (
                    candidate["fit_verdict"] == "FITS"
                    and int(candidate["slots"]) >= min(8, requested_users)
                ):
                    candidates.append(candidate)
            if candidates:
                alternative = min(
                    candidates,
                    key=lambda item: (float(item["required_gib"]), str(item["model_alias"])),
                )
                add(self._suggestion(
                    "SMALLER_MODEL_MEETS_GOAL",
                    alternative,
                    benefit="Meets the requested user count with comfortable projected memory use.",
                    tradeoff="This compares fit only and makes no claim about model intelligence.",
                    confidence="ESTIMATED",
                ))

        return tuple(item.to_dict() for item in suggestions[:MAX_SUGGESTIONS])


__all__ = [
    "CoachSuggestion",
    "MAX_SUGGESTIONS",
    "PerformanceCoachService",
    "SUGGESTION_CODES",
]
