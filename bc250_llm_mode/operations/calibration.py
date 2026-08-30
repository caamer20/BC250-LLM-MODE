"""Durable ``PROFILE_CALIBRATE v1`` workflow.

The workflow owns orchestration and recovery only.  Model/runtime inspection,
bounded fixed-prompt measurement, receipt publication, and exact restoration
live behind :class:`CalibrationHost`; this module remains free of filesystem,
HTTP, subprocess, SQLite, GUI, and frontend imports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .model import OperationState, OperationType, OperationValidationError
from .recovery import RecoveryClass
from .workflow import (
    EffectContext,
    ProbeResult,
    StepDefinition,
    TerminalDecision,
    WorkflowDefinition,
)


OPERATION_TYPE = OperationType.PROFILE_CALIBRATE
REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1
RUNTIME_RESOURCE = "runtime-active"
BENCHMARK_RESOURCE = "runtime-benchmark"
MAX_CANDIDATES = 3
CANDIDATE_POLICIES = frozenset({"balanced-v1", "conservative-v1"})


@dataclass(frozen=True)
class ProfileCalibrateRequestV1:
    profile_id: str
    expected_profile_revision: int
    model_alias: str
    candidate_policy: str = "balanced-v1"
    accept_tight: bool = False
    requested_by: str = "cli"


@dataclass(frozen=True)
class CalibrationPlanV1:
    profile_id: str
    profile_revision: int
    profile_fingerprint: str
    expected_evidence_fingerprint: str
    model_alias: str
    runtime_component_identity: str
    candidates: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CalibrationBaselineV1:
    config: dict[str, Any]
    known_good: dict[str, Any] | None
    service_active: bool
    service_verified: bool


class CalibrationHost(Protocol):
    def preflight(self, request: ProfileCalibrateRequestV1) -> None: ...

    def resolve_plan(self, request: ProfileCalibrateRequestV1) -> CalibrationPlanV1: ...

    def capture_baseline(
        self, request: ProfileCalibrateRequestV1, plan: CalibrationPlanV1
    ) -> CalibrationBaselineV1: ...

    def probe_candidate(self, ctx: EffectContext, index: int) -> ProbeResult: ...

    def run_candidate(self, ctx: EffectContext, index: int) -> dict[str, Any]: ...

    def verify_candidate(self, ctx: EffectContext, index: int) -> dict[str, Any]: ...

    def restore_baseline(self, ctx: EffectContext) -> dict[str, Any]: ...

    def probe_restoration(self, ctx: EffectContext) -> ProbeResult: ...

    def verify_restoration(self, ctx: EffectContext) -> dict[str, Any]: ...

    def probe_recorded_winner(self, ctx: EffectContext) -> ProbeResult: ...

    def record_winner(self, ctx: EffectContext) -> dict[str, Any]: ...


def _closed(payload: dict[str, Any], fields: frozenset[str]) -> None:
    unknown = set(payload) - fields
    if unknown:
        raise OperationValidationError(f"unknown request fields: {sorted(unknown)}")


def _short(value: object, *, name: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise OperationValidationError(f"{name} must be a short non-empty string")
    return value.strip()


def decode_calibration_request(payload: dict[str, Any]) -> ProfileCalibrateRequestV1:
    _closed(
        payload,
        frozenset(
            {
                "profile_id",
                "expected_profile_revision",
                "model_alias",
                "candidate_policy",
                "accept_tight",
                "requested_by",
            }
        ),
    )
    profile_id = _short(payload.get("profile_id"), name="profile_id", maximum=64)
    revision = payload.get("expected_profile_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise OperationValidationError("expected_profile_revision must be positive")
    model_alias = _short(payload.get("model_alias"), name="model_alias")
    policy = payload.get("candidate_policy", "balanced-v1")
    if policy not in CANDIDATE_POLICIES:
        raise OperationValidationError("unknown calibration candidate policy")
    accept_tight = payload.get("accept_tight", False)
    if not isinstance(accept_tight, bool):
        raise OperationValidationError("accept_tight must be boolean")
    requested_by = _short(
        payload.get("requested_by", "cli"), name="requested_by", maximum=64
    )
    return ProfileCalibrateRequestV1(
        profile_id=profile_id,
        expected_profile_revision=revision,
        model_alias=model_alias,
        candidate_policy=policy,
        accept_tight=accept_tight,
        requested_by=requested_by,
    )


def _plan(ctx: EffectContext) -> CalibrationPlanV1:
    value = ctx.prior_outputs.get("resolve_plan")
    if not isinstance(value, dict):
        raise OperationValidationError("calibration plan checkpoint is missing")
    return CalibrationPlanV1(
        profile_id=str(value["profile_id"]),
        profile_revision=int(value["profile_revision"]),
        profile_fingerprint=str(value["profile_fingerprint"]),
        expected_evidence_fingerprint=str(value["expected_evidence_fingerprint"]),
        model_alias=str(value["model_alias"]),
        runtime_component_identity=str(value["runtime_component_identity"]),
        candidates=tuple(dict(item) for item in value.get("candidates", ())),
    )


def _baseline(ctx: EffectContext) -> CalibrationBaselineV1:
    value = ctx.prior_outputs.get("capture_baseline")
    if not isinstance(value, dict):
        raise OperationValidationError("calibration baseline checkpoint is missing")
    known_good = value.get("known_good")
    return CalibrationBaselineV1(
        config=dict(value.get("config") or {}),
        known_good=dict(known_good) if isinstance(known_good, dict) else None,
        service_active=bool(value.get("service_active")),
        service_verified=bool(value.get("service_verified")),
    )


def _candidate_input(index: int):
    def derive(*, request, prior) -> dict[str, Any]:
        plan = prior.get("resolve_plan") or {}
        candidates = plan.get("candidates") or ()
        return {
            "candidate_index": index,
            "candidate_fingerprint": (
                str(candidates[index].get("candidate_fingerprint"))
                if index < len(candidates)
                else "SKIPPED"
            ),
        }

    return derive


def _winner(outputs: dict[str, Any]) -> dict[str, Any] | None:
    completed = []
    for index in range(MAX_CANDIDATES):
        value = outputs.get(f"measure_candidate_{index + 1}")
        if not isinstance(value, dict) or value.get("status") != "COMPLETE":
            continue
        generation = value.get("generation_per_second")
        if not isinstance(generation, (int, float)):
            continue
        completed.append(value)
    if not completed:
        return None
    return max(
        completed,
        key=lambda item: (
            float(item["generation_per_second"]),
            -int(item.get("candidate_index") or 0),
        ),
    )


def build_calibration_workflow(host: CalibrationHost) -> WorkflowDefinition:
    def plan_execute(ctx: EffectContext) -> dict[str, Any]:
        return asdict(host.resolve_plan(ctx.request))

    def plan_probe(ctx: EffectContext) -> ProbeResult:
        # Resolution is read-only and deterministic; interrupted resolution is
        # safe to recompute under the request's revision fence.
        return ProbeResult(RecoveryClass.ABSENT, "PLAN_NOT_CHECKPOINTED")

    def plan_verify(ctx: EffectContext) -> dict[str, Any]:
        plan = _plan(ctx)
        if not 1 <= len(plan.candidates) <= MAX_CANDIDATES:
            raise OperationValidationError("calibration plan candidate bound violated")
        return {}

    def baseline_execute(ctx: EffectContext) -> dict[str, Any]:
        return asdict(host.capture_baseline(ctx.request, _plan(ctx)))

    def baseline_probe(ctx: EffectContext) -> ProbeResult:
        return ProbeResult(RecoveryClass.ABSENT, "BASELINE_NOT_CHECKPOINTED")

    def baseline_verify(ctx: EffectContext) -> dict[str, Any]:
        _baseline(ctx)
        return {}

    steps: list[StepDefinition] = [
        StepDefinition(
            "resolve_plan",
            "preparing",
            1,
            derive_input=lambda **_: {},
            probe=plan_probe,
            execute=plan_execute,
            verify=plan_verify,
            resources=(BENCHMARK_RESOURCE,),
            effect_disposition="NONE",
            safe_if_uncertain=True,
        ),
        StepDefinition(
            "capture_baseline",
            "preparing",
            2,
            derive_input=lambda **_: {},
            probe=baseline_probe,
            execute=baseline_execute,
            verify=baseline_verify,
            resources=(BENCHMARK_RESOURCE, RUNTIME_RESOURCE),
            effect_disposition="NONE",
            safe_if_uncertain=True,
        ),
    ]

    for index in range(MAX_CANDIDATES):
        key = f"measure_candidate_{index + 1}"

        def execute(ctx: EffectContext, candidate_index=index) -> dict[str, Any]:
            _plan(ctx)
            _baseline(ctx)
            return host.run_candidate(ctx, candidate_index)

        def probe(ctx: EffectContext, candidate_index=index) -> ProbeResult:
            _plan(ctx)
            _baseline(ctx)
            return host.probe_candidate(ctx, candidate_index)

        def verify(ctx: EffectContext, candidate_index=index) -> dict[str, Any]:
            return host.verify_candidate(ctx, candidate_index)

        steps.append(
            StepDefinition(
                key,
                "running",
                index + 3,
                derive_input=_candidate_input(index),
                probe=probe,
                execute=execute,
                verify=verify,
                resources=(BENCHMARK_RESOURCE, RUNTIME_RESOURCE),
                externally_visible=True,
                critical=True,
                cancel_safe_before=True,
                effect_disposition="REVERSIBLE",
                compensate=host.restore_baseline,
                probe_restoration=host.probe_restoration,
                verify_restoration=host.verify_restoration,
            )
        )

    def record_execute(ctx: EffectContext) -> dict[str, Any]:
        return host.record_winner(ctx)

    steps.append(
        StepDefinition(
            "record_winner",
            "committing",
            len(steps) + 1,
            derive_input=lambda *, request, prior: {
                "winner_fingerprint": (
                    (_winner(prior) or {}).get("candidate_fingerprint")
                )
            },
            probe=host.probe_recorded_winner,
            execute=record_execute,
            verify=lambda ctx: {},
            resources=(BENCHMARK_RESOURCE,),
            externally_visible=True,
            critical=True,
            effect_disposition="HIDDEN_DURABLE",
            safe_if_uncertain=True,
        )
    )

    def terminal(request, outputs) -> TerminalDecision:
        winner = _winner(outputs)
        if winner is None:
            return TerminalDecision(
                OperationState.FAILED_SAFE,
                "CALIBRATION_NO_COMPLETE_CANDIDATE",
                {"completed_candidates": 0, "winner": None},
                "calibration completed without a usable candidate",
            )
        return TerminalDecision(
            OperationState.SUCCEEDED,
            "CALIBRATION_WINNER_PROPOSED",
            {
                "completed_candidates": sum(
                    1
                    for index in range(MAX_CANDIDATES)
                    if (outputs.get(f"measure_candidate_{index + 1}") or {}).get("status")
                    == "COMPLETE"
                ),
                "winner": {
                    key: winner.get(key)
                    for key in (
                        "candidate_index",
                        "candidate_fingerprint",
                        "evidence_fingerprint",
                        "generation_per_second",
                        "prompt_per_second",
                        "time_to_first_unit_ms",
                        "peak_temperature_c",
                        "throttling_class",
                    )
                },
                "applied": False,
            },
            "calibration finished; winner is a proposal and was not applied",
        )

    return WorkflowDefinition(
        operation_type=OPERATION_TYPE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_calibration_request,
        steps=tuple(steps),
        summary=lambda request: f"Calibrate profile {request.profile_id}",
        terminal_decision=terminal,
        preflight=host.preflight,
        recovery_barrier_resources=(BENCHMARK_RESOURCE, RUNTIME_RESOURCE),
    )


__all__ = [
    "BENCHMARK_RESOURCE",
    "CANDIDATE_POLICIES",
    "CalibrationBaselineV1",
    "CalibrationHost",
    "CalibrationPlanV1",
    "MAX_CANDIDATES",
    "OPERATION_TYPE",
    "ProfileCalibrateRequestV1",
    "RUNTIME_RESOURCE",
    "build_calibration_workflow",
    "decode_calibration_request",
]
