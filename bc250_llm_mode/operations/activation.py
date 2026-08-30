"""Durable ``MODEL_ACTIVATE v1`` workflow (Session 5C plan §5–§8).

This module owns the versioned activation REQUEST, the closed EVIDENCE
vocabulary, and the TYPED ACTIVATION PORT consumed by the eight workflow
steps. It contains no I/O: the port is implemented by exactly one
production adapter (``activation_adapter``, wired in composition) and by
the test fake world. Import direction rules (plan §4) forbid importing
``server``, tkinter, GUI, chat, subprocess, urllib, sqlite3, or global
state here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .model import OperationType, OperationValidationError
from .recovery import RecoveryClass
from .workflow import (
    EffectContext,
    ProbeResult,
    StepDefinition,
    WorkflowDefinition,
)

OPERATION_TYPE = OperationType.MODEL_ACTIVATE
REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1
ACTIVATION_RESOURCE = "runtime-active"

CONTEXT_RANGE = (512, 262144)
SLOT_RANGE = (1, 8)
REQUESTED_BY_VALUES = ("cli", "chat", "gui", "setup", "repair")

# Stable, bounded evidence codes (plan §5.4).
CODE_CANDIDATE_RESOLVED = "CANDIDATE_RESOLVED"
CODE_THERMAL_LATCH_STOPPED = "THERMAL_LATCH_STOPPED"
CODE_REVISION_CONFLICT = "RUNTIME_REVISION_CONFLICT"
CODE_ARTIFACT_CHANGED = "ARTIFACT_IDENTITY_CHANGED"
CODE_LAYOUT_REJECTED = "MODEL_LAYOUT_REJECTED"
CODE_FIT_NO_FIT = "FIT_NO_FIT"
CODE_PRIOR_ACTIVE_VERIFIED = "PRIOR_ACTIVE_VERIFIED"
CODE_PRIOR_STOPPED_CAPTURED = "PRIOR_STOPPED_CAPTURED"
CODE_CONFIG_COMMITTED = "CANDIDATE_CONFIG_COMMITTED"
CODE_HANDOFF_PUBLISHED = "CANDIDATE_HANDOFF_PUBLISHED"
CODE_RUNTIME_VERIFIED = "CANDIDATE_RUNTIME_VERIFIED"
CODE_INFERENCE_VERIFIED = "CANDIDATE_INFERENCE_VERIFIED"
CODE_KNOWN_GOOD_PROMOTED = "KNOWN_GOOD_PROMOTED"
CODE_PRIOR_RESTORED = "PRIOR_RUNTIME_RESTORED"
CODE_RESTORATION_UNCERTAIN = "RESTORATION_UNCERTAIN"


# -- Versioned request ---------------------------------------------------------


@dataclass(frozen=True)
class ModelActivateRequestV1:
    """Closed activation intent (plan §5.1).

    Deliberately NO path, service name, port, command, URL, token, or
    free-form optimization dictionary; no ``allow_preview`` flag.
    """

    model_alias: str
    context_per_slot: int | None = None
    parallel_slots: int | None = None
    profile_id: str | None = None
    expected_runtime_revision: int | None = None
    requested_by: str = "cli"


_FIELDS = frozenset(
    {
        "model_alias",
        "context_per_slot",
        "parallel_slots",
        "profile_id",
        "expected_runtime_revision",
        "requested_by",
    }
)


def decode_activation_request(payload: dict[str, Any]) -> ModelActivateRequestV1:
    if not isinstance(payload, dict):
        raise OperationValidationError("activation request must be an object")
    unknown = set(payload) - _FIELDS
    if unknown:
        raise OperationValidationError(
            f"unknown request fields: {sorted(unknown)}"
        )
    alias = payload.get("model_alias")
    if not isinstance(alias, str) or not alias.strip() or len(alias) > 256:
        raise OperationValidationError("model_alias must be a short string")
    context = payload.get("context_per_slot")
    if context is not None:
        if (
            not isinstance(context, int)
            or isinstance(context, bool)
            or not CONTEXT_RANGE[0] <= context <= CONTEXT_RANGE[1]
        ):
            raise OperationValidationError(
                f"context_per_slot must be {CONTEXT_RANGE[0]}..{CONTEXT_RANGE[1]}"
            )
    slots = payload.get("parallel_slots")
    if slots is not None:
        if (
            not isinstance(slots, int)
            or isinstance(slots, bool)
            or not SLOT_RANGE[0] <= slots <= SLOT_RANGE[1]
        ):
            raise OperationValidationError(
                f"parallel_slots must be {SLOT_RANGE[0]}..{SLOT_RANGE[1]}"
            )
    profile_id = payload.get("profile_id")
    if profile_id is not None and (
        not isinstance(profile_id, str) or len(profile_id) > 256
    ):
        raise OperationValidationError("profile_id must be a short string")
    revision = payload.get("expected_runtime_revision")
    if revision is not None:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise OperationValidationError(
                "expected_runtime_revision must be a positive integer"
            )
    requested_by = payload.get("requested_by", "cli")
    if requested_by not in REQUESTED_BY_VALUES:
        raise OperationValidationError(
            f"requested_by must be one of {REQUESTED_BY_VALUES}"
        )
    return ModelActivateRequestV1(
        model_alias=alias,
        context_per_slot=context,
        parallel_slots=slots,
        profile_id=profile_id,
        expected_runtime_revision=revision,
        requested_by=requested_by,
    )


# -- Closed evidence vocabulary ------------------------------------------------


@dataclass(frozen=True)
class CandidateRuntimeV1:
    """Resolved immutable artifact identity (plan §5.2)."""

    model_alias: str
    canonical_path: str
    quant: str
    display_alias: str
    validation_status: str
    provenance_class: str
    byte_size: int
    file_identity: str
    content_digest: str
    layout_verdict: str
    context_per_slot: int
    parallel_slots: int
    settings: dict[str, Any] = field(default_factory=dict)
    fit_verdict: str = ""
    fit_detail: str = ""
    runtime_fingerprint: str = ""
    component_identity: str = ""
    profile_id: str | None = None
    profile_revision: int | None = None
    profile_fingerprint: str | None = None


@dataclass(frozen=True)
class PriorRuntimeSnapshotV1:
    """Complete restoration target captured BEFORE any mutation (§5.3)."""

    service_state: str  # ACTIVE_VERIFIED | STOPPED
    revision: int
    config: dict[str, Any] = field(default_factory=dict)
    handoff_fingerprint: str | None = None  # None => ABSENT
    handoff_payload: dict[str, Any] | None = None
    known_good: dict[str, Any] | None = None  # None => NONE
    component_identity: str | None = None
    observed_model_alias: str | None = None
    observed_context_total: int | None = None
    observed_slots: int | None = None
    inference_verified: bool = False


@dataclass(frozen=True)
class ConfigEvidenceV1:
    revision: int
    model_alias: str
    context_per_slot: int
    parallel_slots: int
    profile_id: str | None = None
    profile_revision: int | None = None
    profile_fingerprint: str | None = None


@dataclass(frozen=True)
class HandoffEvidenceV1:
    fingerprint: str
    config_revision: int
    model_id: str
    schema_version: int


@dataclass(frozen=True)
class RestartEvidenceV1:
    restarted_now: bool
    was_already_active: bool


@dataclass(frozen=True)
class HealthEvidenceV1:
    healthy: bool
    model_alias: str
    context_total: int
    slots: int


@dataclass(frozen=True)
class InferenceEvidenceV1:
    success: bool
    generated_count: int
    elapsed_bucket: str  # sub_second | seconds | slow


@dataclass(frozen=True)
class KnownGoodEvidenceV1:
    model_alias: str
    context: int
    slots: int
    fingerprint: str | None
    component_identity: str | None
    profile_id: str | None = None
    profile_revision: int | None = None
    profile_fingerprint: str | None = None


@dataclass(frozen=True)
class RestorationEvidenceV1:
    restored: bool
    stage_codes: list[str] = field(default_factory=list)
    service_state: str = "STOPPED"


# -- Typed activation port (plan §6) -------------------------------------------


class ActivationHost(Protocol):
    """Typed, idempotent port grouped by postcondition.

    Every mutator has a read-only probe that classifies reality WITHOUT
    mutating it. Implementations live outside ``operations/``.
    """

    def resolve_candidate(
        self, request: ModelActivateRequestV1
    ) -> CandidateRuntimeV1: ...

    def observe_candidate(
        self, request: ModelActivateRequestV1, candidate: CandidateRuntimeV1
    ) -> ProbeResult: ...

    def capture_prior(
        self, request: ModelActivateRequestV1
    ) -> PriorRuntimeSnapshotV1: ...

    def commit_candidate(
        self,
        request: ModelActivateRequestV1,
        candidate: CandidateRuntimeV1,
        external_effect_id: str,
    ) -> ConfigEvidenceV1: ...

    def observe_config(
        self,
        request: ModelActivateRequestV1,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
    ) -> ProbeResult: ...

    def publish_candidate(
        self, candidate: CandidateRuntimeV1, external_effect_id: str
    ) -> HandoffEvidenceV1: ...

    def observe_handoff(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
    ) -> ProbeResult: ...

    def restart_candidate(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
        external_effect_id: str,
        pulse: Any = None,
    ) -> RestartEvidenceV1: ...

    def observe_restart(
        self, candidate: CandidateRuntimeV1, prior: PriorRuntimeSnapshotV1,
        pulse: Any = None,
    ) -> ProbeResult: ...

    def check_health(
        self, candidate: CandidateRuntimeV1, pulse: Any = None,
    ) -> HealthEvidenceV1: ...

    def check_inference(
        self, candidate: CandidateRuntimeV1
    ) -> InferenceEvidenceV1: ...

    def promote_known_good(
        self, candidate: CandidateRuntimeV1, external_effect_id: str
    ) -> KnownGoodEvidenceV1: ...

    def observe_known_good(
        self,
        candidate: CandidateRuntimeV1,
        prior: PriorRuntimeSnapshotV1,
    ) -> ProbeResult: ...

    def restore_prior(
        self,
        prior: PriorRuntimeSnapshotV1,
        candidate: CandidateRuntimeV1,
        restoration_id: str,
        pulse: Any = None,
    ) -> RestorationEvidenceV1: ...

    def observe_restoration(
        self, prior: PriorRuntimeSnapshotV1, candidate: CandidateRuntimeV1,
        pulse: Any = None,
    ) -> ProbeResult: ...


# -- Evidence (de)serialization helpers ----------------------------------------


def evidence_dict(evidence: Any) -> dict[str, Any]:
    return asdict(evidence)


def _typed(cls: Any, output: Any):
    if isinstance(output, cls):
        return output
    if isinstance(output, dict):
        return cls(**output)
    raise TypeError(f"expected {cls.__name__}")


def candidate_from_output(output: Any) -> CandidateRuntimeV1:
    return _typed(CandidateRuntimeV1, output or {})


def prior_from_output(output: Any) -> PriorRuntimeSnapshotV1:
    return _typed(PriorRuntimeSnapshotV1, output or {})


# -- The eight-step workflow (plan §7) -----------------------------------------


def _require_complete(result: ProbeResult, code: str) -> None:
    if result.classification is not RecoveryClass.COMPLETE:
        from .workflow import StepFailure

        raise StepFailure(
            code, f"postcondition not proven ({result.reason_code})"
        )


def build_activation_workflow(host: ActivationHost) -> WorkflowDefinition:
    """Frozen MODEL_ACTIVATE v1 definition bound to one host implementation."""

    def _candidate(ctx: EffectContext) -> CandidateRuntimeV1:
        return candidate_from_output(
            ctx.prior_outputs.get("resolve_candidate") or {}
        )

    def _prior(ctx: EffectContext) -> PriorRuntimeSnapshotV1:
        return prior_from_output(ctx.prior_outputs.get("capture_prior") or {})

    # Step 1 — resolve_candidate (prepare; no visible mutation)
    def resolve_execute(ctx: EffectContext) -> dict[str, Any]:
        return evidence_dict(host.resolve_candidate(ctx.request))

    def resolve_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("resolve_candidate")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_CANDIDATE_EVIDENCE")
        return host.observe_candidate(ctx.request, candidate_from_output(output))

    def resolve_verify(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_candidate(ctx.request, _candidate(ctx))
        _require_complete(result, CODE_ARTIFACT_CHANGED)
        return {}

    # Step 2 — capture_prior (prepare; read-only)
    def capture_execute(ctx: EffectContext) -> dict[str, Any]:
        return evidence_dict(host.capture_prior(ctx.request))

    def capture_verify(ctx: EffectContext) -> dict[str, Any]:
        from .workflow import StepFailure

        prior = _prior(ctx)
        if prior.service_state not in ("ACTIVE_VERIFIED", "STOPPED"):
            raise StepFailure(
                CODE_RESTORATION_UNCERTAIN, "prior service state unprovable"
            )
        if (
            prior.service_state == "ACTIVE_VERIFIED"
            and not prior.inference_verified
        ):
            # An active prior that cannot pass health + inference is NOT a
            # valid rollback target: fail safely before candidate mutation.
            raise StepFailure(
                CODE_PRIOR_ACTIVE_VERIFIED,
                "active prior failed bounded verification",
                mutation_possible=False,
            )
        return {}

    # Steps 3-5 and 8 share the aggregate restoration protocol (§9).
    def compensate_via_restoration(ctx: EffectContext) -> dict[str, Any]:
        restoration = host.restore_prior(
            _prior(ctx), _candidate(ctx), ctx.external_effect_id,
            pulse=ctx.pulse,
        )
        return evidence_dict(restoration)

    def verify_restored(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_restoration(
            _prior(ctx), _candidate(ctx), pulse=ctx.pulse
        )
        _require_complete(result, CODE_RESTORATION_UNCERTAIN)
        return {}

    def probe_restored(ctx: EffectContext) -> ProbeResult:
        return host.observe_restoration(
            _prior(ctx), _candidate(ctx), pulse=ctx.pulse
        )

    # Step 3 — commit_candidate_config (critical; no handoff side effect)
    def commit_execute(ctx: EffectContext) -> dict[str, Any]:
        return evidence_dict(
            host.commit_candidate(
                ctx.request, _candidate(ctx), ctx.external_effect_id
            )
        )

    def commit_verify(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_config(ctx.request, _candidate(ctx), _prior(ctx))
        _require_complete(result, CODE_REVISION_CONFLICT)
        return {}

    # Step 4 — publish_candidate_handoff (critical)
    def publish_execute(ctx: EffectContext) -> dict[str, Any]:
        return evidence_dict(
            host.publish_candidate(_candidate(ctx), ctx.external_effect_id)
        )

    def publish_probe(ctx: EffectContext) -> ProbeResult:
        """Composite §8 decision for an interrupted publication.

        The handoff artifact alone does not prove the step safe to skip:
        if the active runtime does not already serve the complete candidate,
        an interrupted publication is REVERTIBLE and rolls back.
        """
        candidate = _candidate(ctx)
        prior = _prior(ctx)
        handoff = host.observe_handoff(candidate, prior)
        if handoff.classification is not RecoveryClass.COMPLETE:
            return handoff
        runtime = host.observe_restart(candidate, prior, pulse=ctx.pulse)
        if (
            runtime.classification is RecoveryClass.COMPLETE
            and runtime.reason_code == "CANDIDATE_RUNTIME_VERIFIED"
        ):
            return ProbeResult(
                RecoveryClass.COMPLETE,
                CODE_HANDOFF_PUBLISHED,
                output=handoff.output,
            )
        if runtime.classification is RecoveryClass.REVERTIBLE:
            return ProbeResult(
                RecoveryClass.REVERTIBLE,
                "PUBLISHED_RUNTIME_NOT_CANDIDATE",
            )
        return ProbeResult(
            RecoveryClass.UNCERTAIN_MANUAL,
            runtime.reason_code,
        )

    def publish_verify(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_handoff(_candidate(ctx), _prior(ctx))
        _require_complete(result, CODE_HANDOFF_PUBLISHED)
        return {}

    # Step 5 — restart_candidate (critical)
    def restart_execute(ctx: EffectContext) -> dict[str, Any]:
        from .workflow import StepFailure

        candidate = _candidate(ctx)
        # Re-validate artifact identity immediately before restart so a file
        # replaced in place cannot run under stale evidence.
        identity = host.observe_candidate(ctx.request, candidate)
        if identity.classification is not RecoveryClass.COMPLETE:
            raise StepFailure(
                CODE_ARTIFACT_CHANGED,
                "artifact changed between resolution and restart",
                mutation_possible=False,
            )
        return evidence_dict(
            host.restart_candidate(
                candidate, _prior(ctx), ctx.external_effect_id,
                pulse=ctx.pulse,
            )
        )

    def restart_probe(ctx: EffectContext) -> ProbeResult:
        return host.observe_restart(
            _candidate(ctx), _prior(ctx), pulse=ctx.pulse
        )

    def restart_verify(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_restart(
            _candidate(ctx), _prior(ctx), pulse=ctx.pulse
        )
        _require_complete(result, CODE_RUNTIME_VERIFIED)
        return {}

    # Step 6 — verify_candidate_health (critical; non-mutating)
    def health_execute(ctx: EffectContext) -> dict[str, Any]:
        from .workflow import StepFailure

        health = host.check_health(_candidate(ctx), pulse=ctx.pulse)
        if not health.healthy:
            raise StepFailure(
                CODE_RUNTIME_VERIFIED,
                "health identity mismatch",
                mutation_possible=True,
            )
        return evidence_dict(health)

    def health_probe(ctx: EffectContext) -> ProbeResult:
        health = host.check_health(_candidate(ctx), pulse=ctx.pulse)
        if health.healthy:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                CODE_RUNTIME_VERIFIED,
                output=evidence_dict(health),
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "HEALTH_MISMATCH")

    # Step 7 — verify_candidate_inference (critical; non-mutating)
    def inference_execute(ctx: EffectContext) -> dict[str, Any]:
        from .workflow import StepFailure

        inference = host.check_inference(_candidate(ctx))
        if not inference.success:
            raise StepFailure(
                CODE_INFERENCE_VERIFIED,
                "bounded inference did not succeed",
                mutation_possible=True,
            )
        return evidence_dict(inference)

    def inference_probe(ctx: EffectContext) -> ProbeResult:
        inference = host.check_inference(_candidate(ctx))
        if inference.success:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                CODE_INFERENCE_VERIFIED,
                output=evidence_dict(inference),
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "INFERENCE_FAILED")

    # Step 8 — promote_known_good (critical)
    def promote_execute(ctx: EffectContext) -> dict[str, Any]:
        return evidence_dict(
            host.promote_known_good(_candidate(ctx), ctx.external_effect_id)
        )

    def promote_verify(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_known_good(_candidate(ctx), _prior(ctx))
        _require_complete(result, CODE_KNOWN_GOOD_PROMOTED)
        return {}

    def _input(**extra: Any):
        def derive(*, request, prior):
            base: dict[str, Any] = dict(extra)
            base["model_alias"] = request.model_alias
            return base

        return derive

    steps = (
        StepDefinition(
            step_key="resolve_candidate",
            phase="prepare",
            sequence=1,
            unit="identity",
            resources=(ACTIVATION_RESOURCE,),
            derive_input=_input(),
            probe=resolve_probe,
            execute=resolve_execute,
            verify=resolve_verify,
        ),
        StepDefinition(
            step_key="capture_prior",
            phase="prepare",
            sequence=2,
            unit="snapshot",
            resources=(ACTIVATION_RESOURCE,),
            derive_input=_input(),
            probe=lambda _ctx: ProbeResult(
                RecoveryClass.ABSENT, "NO_SNAPSHOT_EVIDENCE"
            ),
            execute=capture_execute,
            verify=capture_verify,
        ),
        StepDefinition(
            step_key="commit_candidate_config",
            phase="commit-config",
            sequence=3,
            unit="config",
            resources=(ACTIVATION_RESOURCE,),
            externally_visible=True,
            critical=True,
            cancel_safe_before=False,
            derive_input=_input(),
            probe=lambda ctx: host.observe_config(
                ctx.request, _candidate(ctx), _prior(ctx)
            ),
            execute=commit_execute,
            verify=commit_verify,
            compensate=compensate_via_restoration,
            verify_restoration=verify_restored,
            probe_restoration=probe_restored,
        ),
        StepDefinition(
            step_key="publish_candidate_handoff",
            phase="publish",
            sequence=4,
            unit="handoff",
            resources=(ACTIVATION_RESOURCE,),
            externally_visible=True,
            critical=True,
            cancel_safe_before=False,
            safe_if_uncertain=False,
            derive_input=_input(),
            probe=publish_probe,
            execute=publish_execute,
            verify=publish_verify,
            compensate=compensate_via_restoration,
            verify_restoration=verify_restored,
            probe_restoration=probe_restored,
        ),
        StepDefinition(
            step_key="restart_candidate",
            phase="restart",
            sequence=5,
            unit="service",
            resources=(ACTIVATION_RESOURCE,),
            externally_visible=True,
            critical=True,
            cancel_safe_before=False,
            safe_if_uncertain=False,
            derive_input=_input(),
            probe=restart_probe,
            execute=restart_execute,
            verify=restart_verify,
            compensate=compensate_via_restoration,
            verify_restoration=verify_restored,
            probe_restoration=probe_restored,
        ),
        StepDefinition(
            step_key="verify_candidate_health",
            phase="verify-health",
            sequence=6,
            unit="health",
            resources=(ACTIVATION_RESOURCE,),
            critical=True,
            derive_input=_input(),
            probe=health_probe,
            execute=health_execute,
            verify=lambda _ctx: {},
        ),
        StepDefinition(
            step_key="verify_candidate_inference",
            phase="verify-inference",
            sequence=7,
            unit="inference",
            resources=(ACTIVATION_RESOURCE,),
            critical=True,
            derive_input=_input(),
            probe=inference_probe,
            execute=inference_execute,
            verify=lambda _ctx: {},
        ),
        StepDefinition(
            step_key="promote_known_good",
            phase="promote",
            sequence=8,
            unit="known-good",
            resources=(ACTIVATION_RESOURCE,),
            externally_visible=True,
            critical=True,
            cancel_safe_before=False,
            derive_input=_input(),
            probe=lambda ctx: host.observe_known_good(
                _candidate(ctx), _prior(ctx)
            ),
            execute=promote_execute,
            verify=promote_verify,
            compensate=compensate_via_restoration,
            verify_restoration=verify_restored,
            probe_restoration=probe_restored,
        ),
    )

    return WorkflowDefinition(
        operation_type=OPERATION_TYPE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_activation_request,
        steps=steps,
        summary=lambda request: (
            f"activate {request.model_alias!r} "
            f"(ctx={request.context_per_slot or 'unchanged'}, "
            f"slots={request.parallel_slots or 'unchanged'})"
        ),
        preflight=lambda request: None,
    )
