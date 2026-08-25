"""Durable ``RUNTIME_UPDATE v1`` / ``RUNTIME_ROLLBACK v1`` workflows (U1.2).

This module owns the versioned runtime-lifecycle REQUESTS, the closed
EVIDENCE vocabulary and result codes, and the TYPED RUNTIME HOST PORT
consumed by the workflow steps (ADR 004). It contains NO I/O of any kind:
the port is implemented by exactly one production adapter
(``runtime_lifecycle_adapter``, wired in composition) and by the test fake
world. Import rules (plan §6) forbid subprocess, sqlite3, tkinter, urllib,
httpx, pathlib.Path, env/server/GUI/chat/repository modules, shell source,
service names, host paths, URLs, or credentials here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

OPERATION_TYPE = OperationType.RUNTIME_UPDATE
ROLLBACK_OPERATION_TYPE = OperationType.RUNTIME_ROLLBACK
REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1

RUNTIME_ACTIVE_RESOURCE = "runtime-active"
RUNTIME_INSTALLATION_RESOURCE = "runtime-installation"

REQUESTED_BY_VALUES = ("cli", "gui", "setup", "repair")

# The shipped known-good pin is the default requested ref; resolution to a
# full source commit happens BEFORE any fetch/build mutation (D2).
DEFAULT_REQUESTED_REF = "b7598"
_REF_PATTERN = __import__("re").compile(r"^[A-Za-z0-9._/-]{1,128}$")
_COMMIT_LENGTH = 40

# -- Stable result/failure codes (plan §9.4) -----------------------------------

CODE_RUNTIME_ALREADY_ACTIVE = "RUNTIME_ALREADY_ACTIVE"
CODE_SOURCE_REF_RESOLVED = "SOURCE_REF_RESOLVED"
CODE_SOURCE_REF_MOVED = "SOURCE_REF_MOVED"
CODE_SOURCE_COMMIT_UNAVAILABLE = "SOURCE_COMMIT_UNAVAILABLE"
CODE_BUILD_ENVIRONMENT_UNPROVEN = "BUILD_ENVIRONMENT_UNPROVEN"
CODE_BUILD_DISK_INSUFFICIENT = "BUILD_DISK_INSUFFICIENT"
CODE_ACTIVE_RUNTIME_UNPROVEN = "ACTIVE_RUNTIME_UNPROVEN"
CODE_KNOWN_GOOD_RUNTIME_MISSING = "KNOWN_GOOD_RUNTIME_MISSING"
CODE_THERMAL_LATCH_STOPPED = "THERMAL_LATCH_STOPPED"
CODE_FETCH_TIMEOUT = "FETCH_TIMEOUT"
CODE_BUILD_TIMEOUT = "BUILD_TIMEOUT"
CODE_BUILD_CANCELLED_SAFE = "BUILD_CANCELLED_SAFE"
CODE_CANDIDATE_BUILD_FAILED = "CANDIDATE_BUILD_FAILED"
CODE_CANDIDATE_SMOKE_FAILED = "CANDIDATE_SMOKE_FAILED"
CODE_CANDIDATE_IDENTITY_MISMATCH = "CANDIDATE_IDENTITY_MISMATCH"
CODE_ATOMIC_EXCHANGE_UNSUPPORTED = "ATOMIC_EXCHANGE_UNSUPPORTED"
CODE_ACTIVE_TREE_CHANGED = "ACTIVE_TREE_CHANGED"
CODE_TREE_EXCHANGE_COMPLETED = "TREE_EXCHANGE_COMPLETED"
CODE_TREE_EXCHANGE_UNCERTAIN = "TREE_EXCHANGE_UNCERTAIN"
CODE_HANDOFF_COMPONENT_PUBLISHED = "HANDOFF_COMPONENT_PUBLISHED"
CODE_SERVICE_RESTART_FAILED = "SERVICE_RESTART_FAILED"
CODE_SERVICE_INVOCATION_UNPROVEN = "SERVICE_INVOCATION_UNPROVEN"
CODE_RUNTIME_COMPONENT_MISMATCH = "RUNTIME_COMPONENT_MISMATCH"
CODE_RUNTIME_MODEL_MISMATCH = "RUNTIME_MODEL_MISMATCH"
CODE_RUNTIME_INFERENCE_FAILED = "RUNTIME_INFERENCE_FAILED"
CODE_RUNTIME_PROMOTED = "RUNTIME_PROMOTED"
CODE_ROLLBACK_TARGET_MISSING = "RUNTIME_ROLLBACK_TARGET_MISSING"
CODE_RUNTIME_RESTORED = "RUNTIME_RESTORED"
CODE_RESTORATION_UNCERTAIN = "RUNTIME_RESTORATION_UNCERTAIN"
CODE_CLEANUP_DEFERRED = "RUNTIME_CLEANUP_DEFERRED"

# Closed prior-service states for the captured boundary snapshot.
PRIOR_ACTIVE_VERIFIED = "ACTIVE_VERIFIED"
PRIOR_STOPPED = "STOPPED"
PRIOR_ABSENT = "ABSENT"


# -- Versioned requests ---------------------------------------------------------


@dataclass(frozen=True)
class RuntimeUpdateRequestV1:
    """Closed update intent (plan §9.1).

    Deliberately NO repository URL, container/service name, path, build
    flag/job/command/environment variable, credential, cleanup policy, or
    force/skip switch. ``requested_ref`` is display/resolution input only —
    the durable identity is the resolved commit + content manifest.
    """

    requested_by: str = "cli"
    requested_ref: str | None = None
    expected_active_build_id: str | None = None


_UPDATE_FIELDS = frozenset(
    {"requested_ref", "expected_active_build_id", "requested_by"}
)


def decode_runtime_update_request(payload: dict[str, Any]) -> RuntimeUpdateRequestV1:
    if not isinstance(payload, dict):
        raise OperationValidationError("runtime update request must be an object")
    unknown = set(payload) - _UPDATE_FIELDS
    if unknown:
        raise OperationValidationError(f"unknown request fields: {sorted(unknown)}")
    requested_by = payload.get("requested_by", "cli")
    if requested_by not in REQUESTED_BY_VALUES:
        raise OperationValidationError(
            f"requested_by must be one of {REQUESTED_BY_VALUES}"
        )
    ref = payload.get("requested_ref")
    if ref is not None:
        if not isinstance(ref, str) or not _REF_PATTERN.fullmatch(ref):
            raise OperationValidationError(
                "requested_ref must be a short bounded ref string"
            )
    expected = payload.get("expected_active_build_id")
    if expected is not None:
        from ..runtime_builds import valid_build_id

        if not isinstance(expected, str) or not valid_build_id(expected):
            raise OperationValidationError(
                "expected_active_build_id must be a valid build id"
            )
    return RuntimeUpdateRequestV1(
        requested_by=requested_by,
        requested_ref=ref,
        expected_active_build_id=expected,
    )


@dataclass(frozen=True)
class RuntimeRollbackRequestV1:
    """Closed rollback intent (plan §9.2).

    Both IDs are REQUIRED and are selected by the command service from the
    repository — never free-form frontend paths.
    """

    expected_active_build_id: str
    target_build_id: str
    requested_by: str = "cli"


_ROLLBACK_FIELDS = frozenset(
    {"expected_active_build_id", "target_build_id", "requested_by"}
)


def decode_runtime_rollback_request(payload: dict[str, Any]) -> RuntimeRollbackRequestV1:
    if not isinstance(payload, dict):
        raise OperationValidationError("runtime rollback request must be an object")
    unknown = set(payload) - _ROLLBACK_FIELDS
    if unknown:
        raise OperationValidationError(f"unknown request fields: {sorted(unknown)}")
    from ..runtime_builds import valid_build_id

    requested_by = payload.get("requested_by", "cli")
    if requested_by not in REQUESTED_BY_VALUES:
        raise OperationValidationError(
            f"requested_by must be one of {REQUESTED_BY_VALUES}"
        )
    expected = payload.get("expected_active_build_id")
    target = payload.get("target_build_id")
    for name, value in (
        ("expected_active_build_id", expected),
        ("target_build_id", target),
    ):
        if not isinstance(value, str) or not valid_build_id(value):
            raise OperationValidationError(f"{name} is required and must be a "
                                           "valid build id")
    return RuntimeRollbackRequestV1(
        expected_active_build_id=expected,
        target_build_id=target,
        requested_by=requested_by,
    )


# -- Closed evidence vocabulary (plan §9.3) -------------------------------------


@dataclass(frozen=True)
class ResolvedRuntimeSourceV1:
    requested_ref: str
    source_commit: str
    resolution: str  # PIN | COMMIT
    already_active: bool = False


@dataclass(frozen=True)
class BuildPreflightEvidenceV1:
    thermal_ok: bool
    disk_ok: bool
    disk_required_bytes: int
    disk_available_bytes: int
    filesystem_same_volume: bool
    atomic_exchange_supported: bool
    active_runtime_proven: bool
    legacy_adoption_used: bool = False


@dataclass(frozen=True)
class FetchEvidenceV1:
    source_commit: str
    checkout_locator: str
    fetch_state: str  # CREATED | RESUMED | EXISTING
    verified: bool


@dataclass(frozen=True)
class BuildEnvironmentEvidenceV1:
    recipe_version: int
    recipe_digest: str
    cmake_generator: str
    cmake_options: list[str] = field(default_factory=list)
    cmake_targets: list[str] = field(default_factory=list)
    parallelism_policy: str = ""
    container_image_id: str = ""
    container_image_digest: str = ""
    toolchain: dict[str, str] = field(default_factory=dict)
    target_arch: str = ""
    build_dir_locator: str = ""


@dataclass(frozen=True)
class CandidateBuildEvidenceV1:
    build_dir_locator: str
    binaries: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SmokeEvidenceV1:
    build_id: str
    manifest_digest: str
    smoke_contract_version: int
    binaries_ok: bool
    tree_id: str
    locator: str


@dataclass(frozen=True)
class PriorRuntimeSnapshotV1:
    """Complete restoration target captured BEFORE any mutation (D5/D6)."""

    service_state: str  # ACTIVE_VERIFIED | STOPPED | ABSENT
    active_build_id: str | None = None
    active_tree_id: str | None = None
    promoted_build_id: str | None = None
    rollback_build_id: str | None = None
    generation: int | None = None
    known_good_component_identity: str | None = None
    handoff_fingerprint: str | None = None
    handoff_payload: dict[str, Any] | None = None
    invocation_count: int | None = None
    invocation_marker: str | None = None
    observed_model_alias: str | None = None
    observed_context_total: int | None = None
    observed_slots: int | None = None
    inference_verified: bool = False


@dataclass(frozen=True)
class TreeLocationEvidenceV1:
    tree_id: str
    locator: str
    role: str
    manifest_digest: str
    server_binary_digest: str


@dataclass(frozen=True)
class TreeExchangeEvidenceV1:
    classification: str  # EXCHANGED | PUBLISHED_INITIAL | SKIPPED_ALREADY_ACTIVE
    exchanged_now: bool
    skipped: bool = False
    active_build_id_after: str = ""
    prior_tree: dict[str, Any] | None = None
    target_tree: dict[str, Any] | None = None


@dataclass(frozen=True)
class HandoffComponentEvidenceV1:
    fingerprint: str
    schema_version: int
    component_id: str
    server_sha256: str
    manifest_digest: str
    operation_id: str = ""


@dataclass(frozen=True)
class ServiceRestartEvidenceV1:
    restarted_now: bool
    was_already_active: bool
    invocation_nonce: str = ""
    receipt_present: bool = False


@dataclass(frozen=True)
class RuntimeIdentityEvidenceV1:
    component_ok: bool
    binary_digest_ok: bool
    model_alias_ok: bool
    context_ok: bool
    slots_ok: bool
    health_ok: bool
    observed_model_alias: str = ""


@dataclass(frozen=True)
class RuntimeInferenceEvidenceV1:
    success: bool
    generated_count: int
    latency_bucket: str  # sub_second | seconds | slow


@dataclass(frozen=True)
class RuntimePromotionEvidenceV1:
    generation_after: int
    promoted_build_id: str
    rollback_build_id: str | None
    promoted_tree_id: str | None = None
    rollback_tree_id: str | None = None
    noop: bool = False


@dataclass(frozen=True)
class RuntimeRestorationEvidenceV1:
    restored: bool
    stage_codes: list[str] = field(default_factory=list)
    service_state: str = PRIOR_STOPPED


@dataclass(frozen=True)
class RuntimeCleanupEvidenceV1:
    retained_locators: list[str] = field(default_factory=list)
    removed_locators: list[str] = field(default_factory=list)
    deferred_locators: list[str] = field(default_factory=list)
    noop: bool = False


@dataclass(frozen=True)
class RollbackTargetEvidenceV1:
    target_build_id: str
    current_promoted_build_id: str
    generation: int
    target_tree_id: str
    target_locator: str
    manifest_digest: str
    server_binary_digest: str


def evidence_dict(evidence: Any) -> dict[str, Any]:
    return asdict(evidence)


def _typed(cls: Any, output: Any):
    if isinstance(output, cls):
        return output
    if isinstance(output, dict):
        return cls(**output)
    raise TypeError(f"expected {cls.__name__}")


# -- Typed runtime host port ------------------------------------------------------


class RuntimeLifecycleHost(Protocol):
    """Typed, idempotent port grouped by postcondition (plan §13).

    Every mutator has a read-only probe that classifies reality WITHOUT
    mutating it. Implementations live outside ``operations/``.
    """

    # -- resolution / preflight -------------------------------------------------
    def resolve_source(self, request: Any) -> ResolvedRuntimeSourceV1: ...
    def observe_source_resolution(
        self, request: Any, evidence: ResolvedRuntimeSourceV1
    ) -> ProbeResult: ...
    def observe_noop(self, request: Any) -> bool: ...
    def preflight_build(self, request: Any) -> BuildPreflightEvidenceV1: ...
    def observe_preflight(
        self, request: Any, evidence: BuildPreflightEvidenceV1
    ) -> ProbeResult: ...

    # -- rollback-specific seams (D7) ---------------------------------------------
    def resolve_rollback_target(
        self, request: Any
    ) -> RollbackTargetEvidenceV1: ...
    def observe_rollback_target(
        self, request: Any, evidence: RollbackTargetEvidenceV1
    ) -> ProbeResult: ...
    def preflight_rollback(
        self, request: Any, target: RollbackTargetEvidenceV1
    ) -> BuildPreflightEvidenceV1: ...
    def observe_preflight_rollback(
        self, request: Any, target: RollbackTargetEvidenceV1,
        evidence: BuildPreflightEvidenceV1,
    ) -> ProbeResult: ...
    def smoke_rollback_target(
        self, target: RollbackTargetEvidenceV1
    ) -> SmokeEvidenceV1: ...
    def observe_rollback_manifest(self, target: RollbackTargetEvidenceV1) -> ProbeResult: ...

    # -- candidate build ----------------------------------------------------------
    def fetch_exact_commit(
        self, request: Any, source_commit: str, pulse: Any
    ) -> FetchEvidenceV1: ...
    def probe_checkout(self, source_commit: str) -> ProbeResult: ...
    def configure_build(
        self, request: Any, source_commit: str, pulse: Any
    ) -> BuildEnvironmentEvidenceV1: ...
    def probe_build_environment(
        self, evidence: BuildEnvironmentEvidenceV1
    ) -> ProbeResult: ...
    def compile_candidate(
        self, environment: BuildEnvironmentEvidenceV1, pulse: Any
    ) -> CandidateBuildEvidenceV1: ...
    def probe_compilation(
        self, environment: BuildEnvironmentEvidenceV1
    ) -> ProbeResult: ...
    def smoke_and_register_candidate(
        self,
        request: Any,
        source_commit: str,
        environment: BuildEnvironmentEvidenceV1,
        candidate: CandidateBuildEvidenceV1,
        operation_id: str,
    ) -> SmokeEvidenceV1: ...
    def observe_candidate_manifest(
        self, smoke: SmokeEvidenceV1
    ) -> ProbeResult: ...

    # -- activation boundary --------------------------------------------------------
    def capture_activation_boundary(
        self, request: Any, target_build_id: str | None
    ) -> PriorRuntimeSnapshotV1: ...
    def verify_activation_boundary(
        self, request: Any, snapshot: PriorRuntimeSnapshotV1, target_build_id: str | None
    ) -> None: ...

    # -- atomic exchange --------------------------------------------------------------
    def exchange_active_tree(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        smoke: SmokeEvidenceV1 | None,
        external_effect_id: str,
        *,
        mode: str,
    ) -> TreeExchangeEvidenceV1: ...
    def probe_exchange(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        *,
        mode: str,
    ) -> ProbeResult: ...
    def verify_exchange(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        *,
        mode: str,
    ) -> None: ...

    # -- handoff / restart / verification -----------------------------------------------
    def publish_handoff_v2(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        operation_id: str,
        *,
        mode: str,
    ) -> HandoffComponentEvidenceV1: ...
    def observe_handoff_v2(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        *,
        mode: str,
    ) -> ProbeResult: ...
    def restart_for_runtime_change(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        operation_id: str,
        *,
        mode: str,
    ) -> ServiceRestartEvidenceV1: ...
    def observe_invocation(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        *,
        mode: str,
    ) -> ProbeResult: ...
    def verify_runtime_identity(
        self, snapshot: PriorRuntimeSnapshotV1, target_build_id: str
    ) -> RuntimeIdentityEvidenceV1: ...
    def verify_runtime_inference(
        self, target_build_id: str
    ) -> RuntimeInferenceEvidenceV1: ...

    # -- promotion / restoration / finalization --------------------------------------------
    def promote_verified_runtime(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        smoke: SmokeEvidenceV1 | None,
        operation_id: str,
        *,
        mode: str,
    ) -> RuntimePromotionEvidenceV1: ...
    def observe_promotion(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        *,
        mode: str,
    ) -> ProbeResult: ...
    def restore_prior_runtime(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        restoration_id: str,
        *,
        mode: str,
    ) -> RuntimeRestorationEvidenceV1: ...
    def observe_restoration(
        self, snapshot: PriorRuntimeSnapshotV1, *, mode: str
    ) -> ProbeResult: ...
    def finalize_trees(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        promotion: RuntimePromotionEvidenceV1 | None,
        exchange: TreeExchangeEvidenceV1 | None,
        *,
        mode: str,
    ) -> RuntimeCleanupEvidenceV1: ...
    def observe_finalization(
        self,
        snapshot: PriorRuntimeSnapshotV1,
        target_build_id: str,
        *,
        mode: str,
    ) -> ProbeResult: ...


# -- shared helpers -----------------------------------------------------------------


def _require_complete(result: ProbeResult, code: str) -> None:
    if result.classification is not RecoveryClass.COMPLETE:
        from .workflow import StepFailure

        raise StepFailure(code, f"postcondition not proven ({result.reason_code})")


def _resolve_output(ctx: EffectContext, key: str, cls: Any):
    return _typed(cls, ctx.prior_outputs.get(key) or {})


def _already_active(ctx: EffectContext) -> bool:
    resolved = ctx.prior_outputs.get("resolve_source") or {}
    return bool(resolved.get("already_active"))


def _noop_output(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {"skipped": True}
    base.update(extra or {})
    return base


def _is_skipped(output: Any) -> bool:
    return isinstance(output, dict) and bool(output.get("skipped"))


def _smoke_or_none(ctx: EffectContext, key: str = "smoke_candidate"):
    """Parsed smoke evidence, or None when the no-op branch skipped it."""
    raw = ctx.prior_outputs.get(key)
    if _is_skipped(raw) or not raw:
        return None
    return _typed(SmokeEvidenceV1, raw)


def _target_build_id(ctx: EffectContext) -> str:
    """The intended live build: the candidate, or (no-op branch) the
    already-promoted active build whose verification must still re-run."""
    smoke = _smoke_or_none(ctx)
    if smoke is not None:
        return smoke.build_id
    snapshot = _resolve_output(
        ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
    )
    if snapshot.active_build_id:
        return snapshot.active_build_id
    from .workflow import StepFailure

    raise StepFailure(
        CODE_ACTIVE_RUNTIME_UNPROVEN, "no target or active build to verify"
    )


def _input(**extra: Any):
    def derive(*, request, prior):
        return dict(extra)

    return derive


# -- step callback builders -------------------------------------------------------


def _update_step_callbacks(host: RuntimeLifecycleHost):
    """Callbacks for the 13-step RUNTIME_UPDATE v1 sequence."""

    # 1. resolve_source
    def resolve_execute(ctx: EffectContext) -> dict[str, Any]:
        if host.observe_noop(ctx.request):
            resolved = host.resolve_source(ctx.request)
            return evidence_dict(
                ResolvedRuntimeSourceV1(
                    requested_ref=resolved.requested_ref,
                    source_commit=resolved.source_commit,
                    resolution=resolved.resolution,
                    already_active=True,
                )
            )
        return evidence_dict(host.resolve_source(ctx.request))

    def resolve_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("resolve_source")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_RESOLUTION_EVIDENCE")
        return host.observe_source_resolution(
            ctx.request, _typed(ResolvedRuntimeSourceV1, output)
        )

    def resolve_verify(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_source_resolution(
            ctx.request, _resolve_output(ctx, "resolve_source", ResolvedRuntimeSourceV1)
        )
        _require_complete(result, CODE_SOURCE_REF_MOVED)
        return {}

    # 2. preflight_build
    def preflight_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return _noop_output()
        return evidence_dict(host.preflight_build(ctx.request))

    def preflight_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("preflight_build")
        if _is_skipped(output):
            return ProbeResult(RecoveryClass.COMPLETE, "SKIPPED_NOOP_BRANCH")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_PREFLIGHT_EVIDENCE")
        return host.observe_preflight(
            ctx.request, _typed(BuildPreflightEvidenceV1, output)
        )

    def preflight_verify(ctx: EffectContext) -> dict[str, Any]:
        output = ctx.prior_outputs.get("preflight_build") or {}
        if _is_skipped(output):
            return {}
        result = host.observe_preflight(
            ctx.request, _resolve_output(ctx, "preflight_build", BuildPreflightEvidenceV1)
        )
        _require_complete(result, CODE_BUILD_ENVIRONMENT_UNPROVEN)
        return {}

    # 3. fetch_source
    def fetch_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return _noop_output()
        resolved = _resolve_output(ctx, "resolve_source", ResolvedRuntimeSourceV1)
        return evidence_dict(
            host.fetch_exact_commit(
                ctx.request, resolved.source_commit, ctx.pulse
            )
        )

    def fetch_probe(ctx: EffectContext) -> ProbeResult:
        if _is_skipped(ctx.prior_outputs.get("fetch_source")):
            return ProbeResult(RecoveryClass.COMPLETE, "SKIPPED_NOOP_BRANCH")
        resolved = _resolve_output(ctx, "resolve_source", ResolvedRuntimeSourceV1)
        return host.probe_checkout(resolved.source_commit)

    def fetch_verify(ctx: EffectContext) -> dict[str, Any]:
        if _is_skipped(ctx.prior_outputs.get("fetch_source")):
            return {}
        result = host.probe_checkout(
            _resolve_output(ctx, "resolve_source", ResolvedRuntimeSourceV1).source_commit
        )
        _require_complete(result, CODE_FETCH_TIMEOUT)
        return {}

    # 4. configure_build
    def configure_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return _noop_output()
        resolved = _resolve_output(ctx, "resolve_source", ResolvedRuntimeSourceV1)
        return evidence_dict(
            host.configure_build(ctx.request, resolved.source_commit, ctx.pulse)
        )

    def configure_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("configure_build")
        if _is_skipped(output):
            return ProbeResult(RecoveryClass.COMPLETE, "SKIPPED_NOOP_BRANCH")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_BUILD_ENVIRONMENT")
        return host.probe_build_environment(
            _typed(BuildEnvironmentEvidenceV1, output)
        )

    def configure_verify(ctx: EffectContext) -> dict[str, Any]:
        output = ctx.prior_outputs.get("configure_build") or {}
        if _is_skipped(output):
            return {}
        result = host.probe_build_environment(
            _resolve_output(ctx, "configure_build", BuildEnvironmentEvidenceV1)
        )
        _require_complete(result, CODE_BUILD_ENVIRONMENT_UNPROVEN)
        return {}

    # 5. compile_candidate
    def compile_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return _noop_output()
        environment = _resolve_output(
            ctx, "configure_build", BuildEnvironmentEvidenceV1
        )
        return evidence_dict(host.compile_candidate(environment, ctx.pulse))

    def compile_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("compile_candidate")
        if _is_skipped(output):
            return ProbeResult(RecoveryClass.COMPLETE, "SKIPPED_NOOP_BRANCH")
        return host.probe_compilation(
            _resolve_output(ctx, "configure_build", BuildEnvironmentEvidenceV1)
        )

    def compile_verify(ctx: EffectContext) -> dict[str, Any]:
        if _is_skipped(ctx.prior_outputs.get("compile_candidate")):
            return {}
        result = host.probe_compilation(
            _resolve_output(ctx, "configure_build", BuildEnvironmentEvidenceV1)
        )
        _require_complete(result, CODE_CANDIDATE_BUILD_FAILED)
        return {}

    # 6. smoke_candidate
    def smoke_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return _noop_output()
        resolved = _resolve_output(ctx, "resolve_source", ResolvedRuntimeSourceV1)
        environment = _resolve_output(
            ctx, "configure_build", BuildEnvironmentEvidenceV1
        )
        candidate = _resolve_output(ctx, "compile_candidate", CandidateBuildEvidenceV1)
        return evidence_dict(
            host.smoke_and_register_candidate(
                ctx.request,
                resolved.source_commit,
                environment,
                candidate,
                ctx.operation_id,
            )
        )

    def smoke_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("smoke_candidate")
        if _is_skipped(output):
            return ProbeResult(RecoveryClass.COMPLETE, "SKIPPED_NOOP_BRANCH")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_SMOKE_EVIDENCE")
        return host.observe_candidate_manifest(_typed(SmokeEvidenceV1, output))

    def smoke_verify(ctx: EffectContext) -> dict[str, Any]:
        output = ctx.prior_outputs.get("smoke_candidate") or {}
        if _is_skipped(output):
            return {}
        result = host.observe_candidate_manifest(
            _resolve_output(ctx, "smoke_candidate", SmokeEvidenceV1)
        )
        _require_complete(result, CODE_CANDIDATE_SMOKE_FAILED)
        return {}

    # 7. capture_activation_boundary
    def capture_execute(ctx: EffectContext) -> dict[str, Any]:
        smoke = _smoke_or_none(ctx)
        target_id = None if smoke is None else smoke.build_id
        return evidence_dict(
            host.capture_activation_boundary(ctx.request, target_id)
        )

    def capture_verify(ctx: EffectContext) -> dict[str, Any]:
        smoke = _smoke_or_none(ctx)
        host.verify_activation_boundary(
            ctx.request,
            _resolve_output(ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1),
            None if smoke is None else smoke.build_id,
        )
        return {}

    def capture_input(request, prior):
        return {}

    # 8. exchange_active_tree (critical)
    def exchange_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return evidence_dict(
                TreeExchangeEvidenceV1(
                    classification="SKIPPED_ALREADY_ACTIVE",
                    exchanged_now=False,
                    skipped=True,
                )
            )
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        smoke = _resolve_output(ctx, "smoke_candidate", SmokeEvidenceV1)
        return evidence_dict(
            host.exchange_active_tree(
                snapshot, smoke, ctx.external_effect_id, mode="update"
            )
        )

    def exchange_probe(ctx: EffectContext) -> ProbeResult:
        if _is_skipped(ctx.prior_outputs.get("exchange_active_tree")):
            return ProbeResult(
                RecoveryClass.COMPLETE, "SKIPPED_ALREADY_ACTIVE"
            )
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        smoke = _resolve_output(ctx, "smoke_candidate", SmokeEvidenceV1)
        return host.probe_exchange(
            snapshot, smoke.build_id, mode="update"
        )

    def exchange_verify(ctx: EffectContext) -> dict[str, Any]:
        if _is_skipped(ctx.prior_outputs.get("exchange_active_tree")):
            return {}
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        smoke = _resolve_output(ctx, "smoke_candidate", SmokeEvidenceV1)
        host.verify_exchange(snapshot, smoke.build_id, mode="update")
        return {}

    def compensate_via_restoration(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        return evidence_dict(
            host.restore_prior_runtime(
                snapshot, ctx.external_effect_id, mode="update"
            )
        )

    def verify_restored(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        result = host.observe_restoration(snapshot, mode="update")
        _require_complete(result, CODE_RESTORATION_UNCERTAIN)
        return {}

    def probe_restored(ctx: EffectContext) -> ProbeResult:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        return host.observe_restoration(snapshot, mode="update")

    # 9. publish_component_handoff (critical)
    def publish_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return _noop_output()
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        return evidence_dict(
            host.publish_handoff_v2(
                snapshot,
                _target_build_id(ctx),
                ctx.operation_id,
                mode="update",
            )
        )

    def publish_probe(ctx: EffectContext) -> ProbeResult:
        if _already_active(ctx):
            return ProbeResult(RecoveryClass.COMPLETE, "SKIPPED_NOOP_BRANCH")
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        return host.observe_handoff_v2(
            snapshot, _target_build_id(ctx), mode="update"
        )

    def publish_verify(ctx: EffectContext) -> dict[str, Any]:
        result = publish_probe(ctx)
        if result.reason_code != "SKIPPED_NOOP_BRANCH":
            _require_complete(result, CODE_HANDOFF_COMPONENT_PUBLISHED)
        return {}

    # 10. restart_runtime (critical)
    def restart_execute(ctx: EffectContext) -> dict[str, Any]:
        if _already_active(ctx):
            return _noop_output()
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        return evidence_dict(
            host.restart_for_runtime_change(
                snapshot, _target_build_id(ctx), ctx.operation_id, mode="update"
            )
        )

    def restart_probe(ctx: EffectContext) -> ProbeResult:
        if _already_active(ctx):
            return ProbeResult(RecoveryClass.COMPLETE, "SKIPPED_NOOP_BRANCH")
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        return host.observe_invocation(
            snapshot, _target_build_id(ctx), mode="update"
        )

    def restart_verify(ctx: EffectContext) -> dict[str, Any]:
        result = restart_probe(ctx)
        if result.reason_code != "SKIPPED_NOOP_BRANCH":
            _require_complete(result, CODE_SERVICE_INVOCATION_UNPROVEN)
        return {}

    # 11. verify_runtime (critical; observation only)
    def identity_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        target = _target_build_id(ctx)
        identity = host.verify_runtime_identity(snapshot, target)
        inference = host.verify_runtime_inference(target)
        if not identity.component_ok or not identity.health_ok:
            from .workflow import StepFailure

            raise StepFailure(
                CODE_RUNTIME_COMPONENT_MISMATCH,
                "live component identity mismatch",
                mutation_possible=True,
            )
        if not identity.model_alias_ok or not identity.context_ok or not identity.slots_ok:
            from .workflow import StepFailure

            raise StepFailure(
                CODE_RUNTIME_MODEL_MISMATCH,
                "live model/config mismatch",
                mutation_possible=True,
            )
        if not inference.success:
            from .workflow import StepFailure

            raise StepFailure(
                CODE_RUNTIME_INFERENCE_FAILED,
                "bounded inference failed",
                mutation_possible=True,
            )
        return {
            "identity": evidence_dict(identity),
            "inference": evidence_dict(inference),
        }

    def identity_probe(ctx: EffectContext) -> ProbeResult:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        target = _target_build_id(ctx)
        identity = host.verify_runtime_identity(snapshot, target)
        inference = host.verify_runtime_inference(target)
        ok = (
            identity.component_ok
            and identity.binary_digest_ok
            and identity.model_alias_ok
            and identity.context_ok
            and identity.slots_ok
            and identity.health_ok
            and inference.success
        )
        if ok:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                CODE_RUNTIME_COMPONENT_MISMATCH,
                output={
                    "identity": evidence_dict(identity),
                    "inference": evidence_dict(inference),
                },
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "LIVE_IDENTITY_MISMATCH")

    # 12. promote_runtime (critical)
    def promote_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        if _already_active(ctx):
            return evidence_dict(RuntimePromotionEvidenceV1(
                generation_after=int(snapshot.generation or 0),
                promoted_build_id=_target_build_id(ctx),
                rollback_build_id=snapshot.rollback_build_id,
                noop=True,
            ))
        return evidence_dict(
            host.promote_verified_runtime(
                snapshot,
                _target_build_id(ctx),
                _smoke_or_none(ctx),
                ctx.operation_id,
                mode="update",
            )
        )

    def promote_verify(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        result = host.observe_promotion(
            snapshot, _target_build_id(ctx), mode="update"
        )
        _require_complete(result, CODE_ACTIVE_TREE_CHANGED)
        return {}

    # 13. finalize_trees
    def finalize_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        promotion_raw = ctx.prior_outputs.get("promote_runtime")
        exchange_raw = ctx.prior_outputs.get("exchange_active_tree")
        promotion = (
            _typed(RuntimePromotionEvidenceV1, promotion_raw)
            if promotion_raw
            else None
        )
        exchange = (
            _typed(TreeExchangeEvidenceV1, exchange_raw) if exchange_raw else None
        )
        return evidence_dict(
            host.finalize_trees(
                snapshot,
                _target_build_id(ctx),
                promotion,
                exchange,
                mode="update",
            )
        )

    def finalize_verify(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_activation_boundary", PriorRuntimeSnapshotV1
        )
        result = host.observe_finalization(
            snapshot, _target_build_id(ctx), mode="update"
        )
        _require_complete(result, CODE_CLEANUP_DEFERRED)
        return {}

    return {
        "resolve_execute": resolve_execute,
        "resolve_probe": resolve_probe,
        "resolve_verify": resolve_verify,
        "preflight_execute": preflight_execute,
        "preflight_probe": preflight_probe,
        "preflight_verify": preflight_verify,
        "fetch_execute": fetch_execute,
        "fetch_probe": fetch_probe,
        "fetch_verify": fetch_verify,
        "configure_execute": configure_execute,
        "configure_probe": configure_probe,
        "configure_verify": configure_verify,
        "compile_execute": compile_execute,
        "compile_probe": compile_probe,
        "compile_verify": compile_verify,
        "smoke_execute": smoke_execute,
        "smoke_probe": smoke_probe,
        "smoke_verify": smoke_verify,
        "capture_execute": capture_execute,
        "capture_verify": capture_verify,
        "exchange_execute": exchange_execute,
        "exchange_probe": exchange_probe,
        "exchange_verify": exchange_verify,
        "compensate_via_restoration": compensate_via_restoration,
        "verify_restored": verify_restored,
        "probe_restored": probe_restored,
        "publish_execute": publish_execute,
        "publish_probe": publish_probe,
        "publish_verify": publish_verify,
        "restart_execute": restart_execute,
        "restart_probe": restart_probe,
        "restart_verify": restart_verify,
        "identity_execute": identity_execute,
        "identity_probe": identity_probe,
        "promote_execute": promote_execute,
        "promote_verify": promote_verify,
        "finalize_execute": finalize_execute,
        "finalize_verify": finalize_verify,
        "capture_input": capture_input,
    }


def build_runtime_update_workflow(host: RuntimeLifecycleHost) -> WorkflowDefinition:
    """Frozen RUNTIME_UPDATE v1 definition bound to one host implementation."""
    cb = _update_step_callbacks(host)

    def _step(key, phase, seq, resources, *, probe, execute, verify,
              visible=False, critical=False, cancel_safe=True,
              disposition="REVERSIBLE", compensate=None,
              probe_restoration=None, verify_restoration=None, unit=None,
              derive=None):
        return StepDefinition(
            step_key=key,
            phase=phase,
            sequence=seq,
            unit=unit or phase,
            resources=resources,
            externally_visible=visible,
            critical=critical,
            cancel_safe_before=cancel_safe,
            effect_disposition=disposition,
            compensate=compensate,
            probe_restoration=probe_restoration,
            verify_restoration=verify_restoration,
            safe_if_uncertain=False,
            derive_input=derive or _input(),
            probe=probe,
            execute=execute,
            verify=verify,
        )

    both = (RUNTIME_ACTIVE_RESOURCE, RUNTIME_INSTALLATION_RESOURCE)
    install = (RUNTIME_INSTALLATION_RESOURCE,)
    steps = (
        _step("resolve_source", "resolve", 1, install,
              probe=cb["resolve_probe"], execute=cb["resolve_execute"],
              verify=cb["resolve_verify"]),
        _step("preflight_build", "preflight", 2, install,
              probe=cb["preflight_probe"], execute=cb["preflight_execute"],
              verify=cb["preflight_verify"]),
        _step("fetch_source", "fetch", 3, install,
              probe=cb["fetch_probe"], execute=cb["fetch_execute"],
              verify=cb["fetch_verify"]),
        _step("configure_build", "configure", 4, install,
              probe=cb["configure_probe"], execute=cb["configure_execute"],
              verify=cb["configure_verify"]),
        _step("compile_candidate", "build", 5, install,
              probe=cb["compile_probe"], execute=cb["compile_execute"],
              verify=cb["compile_verify"]),
        _step("smoke_candidate", "smoke", 6, install,
              probe=cb["smoke_probe"], execute=cb["smoke_execute"],
              verify=cb["smoke_verify"]),
        _step("capture_activation_boundary", "activation-boundary", 7, both,
              probe=lambda ctx: ProbeResult(
                  RecoveryClass.ABSENT, "NO_BOUNDARY_SNAPSHOT"),
              execute=cb["capture_execute"],
              verify=cb["capture_verify"],
              derive=cb["capture_input"]),
        _step("exchange_active_tree", "exchange", 8, both,
              visible=True, critical=True, cancel_safe=False,
              probe=cb["exchange_probe"], execute=cb["exchange_execute"],
              verify=cb["exchange_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("publish_component_handoff", "handoff", 9, both,
              visible=True, critical=True, cancel_safe=False,
              probe=cb["publish_probe"], execute=cb["publish_execute"],
              verify=cb["publish_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("restart_runtime", "restart", 10, both,
              visible=True, critical=True, cancel_safe=False,
              probe=cb["restart_probe"], execute=cb["restart_execute"],
              verify=cb["restart_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("verify_runtime", "verify", 11, both,
              critical=True, cancel_safe=False,
              probe=cb["identity_probe"], execute=cb["identity_execute"],
              verify=lambda ctx: {}),
        _step("promote_runtime", "promote", 12, both,
              visible=True, critical=True, cancel_safe=False,
              probe=lambda ctx: ProbeResult(
                  RecoveryClass.ABSENT, "PROMOTION_NOT_EVIDENCED"),
              execute=cb["promote_execute"],
              verify=cb["promote_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("finalize_trees", "finalize", 13, both,
              probe=lambda ctx: ProbeResult(
                  RecoveryClass.ABSENT, "NO_FINALIZATION_EVIDENCE"),
              execute=cb["finalize_execute"],
              verify=cb["finalize_verify"]),
    )

    def terminal_decision(request, outputs) -> TerminalDecision:
        already_active = bool((outputs.get("resolve_source") or {}).get(
            "already_active"
        ))
        if already_active:
            return TerminalDecision(
                OperationState.SUCCEEDED,
                CODE_RUNTIME_ALREADY_ACTIVE,
                {"detail": "requested build already promoted and live"},
                "requested runtime build is already promoted and live",
            )
        return TerminalDecision(
            OperationState.SUCCEEDED,
            CODE_RUNTIME_PROMOTED,
            {},
            "candidate runtime atomically activated, verified, and promoted",
        )

    return WorkflowDefinition(
        operation_type=OPERATION_TYPE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_runtime_update_request,
        steps=steps,
        summary=lambda request: (
            f"update llama.cpp runtime to "
            f"{getattr(request, 'requested_ref', None) or DEFAULT_REQUESTED_REF!r} "
            f"(durable immutable build)"
        ),
        preflight=lambda request: None,
        phase_scoped_resources=True,
        recovery_barrier_resources=both,
        terminal_decision=terminal_decision,
    )


# -- RUNTIME_ROLLBACK v1 (plan §10.2) ---------------------------------------------


def _rollback_step_callbacks(host: RuntimeLifecycleHost):
    """Callbacks for the 10-step RUNTIME_ROLLBACK v1 sequence."""

    def resolve_execute(ctx: EffectContext) -> dict[str, Any]:
        return evidence_dict(host.resolve_rollback_target(ctx.request))

    def resolve_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("resolve_rollback_target")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_TARGET_EVIDENCE")
        return host.observe_rollback_target(
            ctx.request, _typed(RollbackTargetEvidenceV1, output)
        )

    def resolve_verify(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_rollback_target(
            ctx.request,
            _resolve_output(ctx, "resolve_rollback_target", RollbackTargetEvidenceV1),
        )
        _require_complete(result, CODE_ROLLBACK_TARGET_MISSING)
        return {}

    def preflight_execute(ctx: EffectContext) -> dict[str, Any]:
        target = _resolve_output(ctx, "resolve_rollback_target", RollbackTargetEvidenceV1)
        return evidence_dict(host.preflight_rollback(ctx.request, target))

    def preflight_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("preflight_rollback")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_PREFLIGHT_EVIDENCE")
        target = _resolve_output(ctx, "resolve_rollback_target", RollbackTargetEvidenceV1)
        return host.observe_preflight_rollback(
            ctx.request,
            target,
            _typed(BuildPreflightEvidenceV1, output),
        )

    def preflight_verify(ctx: EffectContext) -> dict[str, Any]:
        result = preflight_probe(ctx)
        _require_complete(result, CODE_BUILD_ENVIRONMENT_UNPROVEN)
        return {}

    def smoke_execute(ctx: EffectContext) -> dict[str, Any]:
        target = _resolve_output(ctx, "resolve_rollback_target", RollbackTargetEvidenceV1)
        return evidence_dict(host.smoke_rollback_target(target))

    def smoke_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("smoke_rollback_target")
        if not output:
            return ProbeResult(RecoveryClass.ABSENT, "NO_SMOKE_EVIDENCE")
        target = _resolve_output(ctx, "resolve_rollback_target", RollbackTargetEvidenceV1)
        return host.observe_rollback_manifest(target)

    def smoke_verify(ctx: EffectContext) -> dict[str, Any]:
        result = smoke_probe(ctx)
        _require_complete(result, CODE_CANDIDATE_SMOKE_FAILED)
        return {}

    def capture_execute(ctx: EffectContext) -> dict[str, Any]:
        # The snapshot's restoration target is the CURRENT promoted build.
        return evidence_dict(
            host.capture_activation_boundary(ctx.request, None)
        )

    def capture_verify(ctx: EffectContext) -> dict[str, Any]:
        host.verify_activation_boundary(ctx.request, _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1), None)
        return {}

    def compensate_via_restoration(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        return evidence_dict(
            host.restore_prior_runtime(
                snapshot, ctx.external_effect_id, mode="rollback"
            )
        )

    def verify_restored(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        result = host.observe_restoration(snapshot, mode="rollback")
        _require_complete(result, CODE_RESTORATION_UNCERTAIN)
        return {}

    def probe_restored(ctx: EffectContext) -> ProbeResult:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        return host.observe_restoration(snapshot, mode="rollback")

    def _target_id(ctx: EffectContext) -> str:
        return getattr(ctx.request, "target_build_id")

    # 5. exchange_active_tree
    def exchange_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        smoke = _resolve_output(ctx, "smoke_rollback_target", SmokeEvidenceV1)
        return evidence_dict(
            host.exchange_active_tree(
                snapshot, smoke, ctx.external_effect_id, mode="rollback"
            )
        )

    def exchange_probe(ctx: EffectContext) -> ProbeResult:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        return host.probe_exchange(snapshot, _target_id(ctx), mode="rollback")

    def exchange_verify(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        host.verify_exchange(snapshot, _target_id(ctx), mode="rollback")
        return {}

    # 6. publish_component_handoff
    def publish_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        return evidence_dict(
            host.publish_handoff_v2(
                snapshot, _target_id(ctx), ctx.operation_id, mode="rollback"
            )
        )

    def publish_probe(ctx: EffectContext) -> ProbeResult:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        return host.observe_handoff_v2(
            snapshot, _target_id(ctx), mode="rollback"
        )

    def publish_verify(ctx: EffectContext) -> dict[str, Any]:
        _require_complete(publish_probe(ctx), CODE_HANDOFF_COMPONENT_PUBLISHED)
        return {}

    # 7. restart_runtime
    def restart_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        return evidence_dict(
            host.restart_for_runtime_change(
                snapshot, _target_id(ctx), ctx.operation_id, mode="rollback"
            )
        )

    def restart_probe(ctx: EffectContext) -> ProbeResult:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        return host.observe_invocation(
            snapshot, _target_id(ctx), mode="rollback"
        )

    def restart_verify(ctx: EffectContext) -> dict[str, Any]:
        _require_complete(restart_probe(ctx), CODE_SERVICE_INVOCATION_UNPROVEN)
        return {}

    # 8. verify_runtime
    def identity_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        identity = host.verify_runtime_identity(snapshot, _target_id(ctx))
        inference = host.verify_runtime_inference(_target_id(ctx))
        from .workflow import StepFailure

        if not identity.component_ok or not identity.health_ok:
            raise StepFailure(
                CODE_RUNTIME_COMPONENT_MISMATCH,
                "live component identity mismatch",
                mutation_possible=True,
            )
        if not identity.model_alias_ok or not identity.context_ok or not identity.slots_ok:
            raise StepFailure(
                CODE_RUNTIME_MODEL_MISMATCH,
                "live model/config mismatch",
                mutation_possible=True,
            )
        if not inference.success:
            raise StepFailure(
                CODE_RUNTIME_INFERENCE_FAILED,
                "bounded inference failed on the restored build",
                mutation_possible=True,
            )
        return {"identity": evidence_dict(identity), "inference": evidence_dict(inference)}

    def identity_probe(ctx: EffectContext) -> ProbeResult:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        identity = host.verify_runtime_identity(snapshot, _target_id(ctx))
        inference = host.verify_runtime_inference(_target_id(ctx))
        ok = (
            identity.component_ok
            and identity.binary_digest_ok
            and identity.model_alias_ok
            and identity.context_ok
            and identity.slots_ok
            and identity.health_ok
            and inference.success
        )
        if ok:
            return ProbeResult(
                RecoveryClass.COMPLETE,
                "RESTORED_IDENTITY_VERIFIED",
                output={
                    "identity": evidence_dict(identity),
                    "inference": evidence_dict(inference),
                },
            )
        return ProbeResult(RecoveryClass.REVERTIBLE, "RESTORED_IDENTITY_MISMATCH")

    # 9. promote_rollback
    def promote_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        smoke = _resolve_output(ctx, "smoke_rollback_target", SmokeEvidenceV1)
        return evidence_dict(
            host.promote_verified_runtime(
                snapshot,
                _target_id(ctx),
                smoke,
                ctx.operation_id,
                mode="rollback",
            )
        )

    def promote_verify(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        result = host.observe_promotion(
            snapshot, _target_id(ctx), mode="rollback"
        )
        _require_complete(result, CODE_ACTIVE_TREE_CHANGED)
        return {}

    # 10. finalize_trees
    def finalize_execute(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        promotion_raw = ctx.prior_outputs.get("promote_rollback")
        exchange_raw = ctx.prior_outputs.get("exchange_active_tree")
        promotion = (
            _typed(RuntimePromotionEvidenceV1, promotion_raw)
            if promotion_raw
            else None
        )
        exchange = (
            _typed(TreeExchangeEvidenceV1, exchange_raw) if exchange_raw else None
        )
        return evidence_dict(
            host.finalize_trees(
                snapshot, _target_id(ctx), promotion, exchange, mode="rollback"
            )
        )

    def finalize_verify(ctx: EffectContext) -> dict[str, Any]:
        snapshot = _resolve_output(
            ctx, "capture_rollback_boundary", PriorRuntimeSnapshotV1
        )
        result = host.observe_finalization(
            snapshot, _target_id(ctx), mode="rollback"
        )
        _require_complete(result, CODE_CLEANUP_DEFERRED)
        return {}

    return {
        "resolve_execute": resolve_execute,
        "resolve_probe": resolve_probe,
        "resolve_verify": resolve_verify,
        "preflight_execute": preflight_execute,
        "preflight_probe": preflight_probe,
        "preflight_verify": preflight_verify,
        "smoke_execute": smoke_execute,
        "smoke_probe": smoke_probe,
        "smoke_verify": smoke_verify,
        "capture_execute": capture_execute,
        "capture_verify": capture_verify,
        "compensate_via_restoration": compensate_via_restoration,
        "verify_restored": verify_restored,
        "probe_restored": probe_restored,
        "exchange_execute": exchange_execute,
        "exchange_probe": exchange_probe,
        "exchange_verify": exchange_verify,
        "publish_execute": publish_execute,
        "publish_probe": publish_probe,
        "publish_verify": publish_verify,
        "restart_execute": restart_execute,
        "restart_probe": restart_probe,
        "restart_verify": restart_verify,
        "identity_execute": identity_execute,
        "identity_probe": identity_probe,
        "promote_execute": promote_execute,
        "promote_verify": promote_verify,
        "finalize_execute": finalize_execute,
        "finalize_verify": finalize_verify,
    }


def build_runtime_rollback_workflow(host: RuntimeLifecycleHost) -> WorkflowDefinition:
    """Frozen RUNTIME_ROLLBACK v1 definition bound to one host implementation."""
    cb = _rollback_step_callbacks(host)

    def _step(key, phase, seq, resources, *, probe, execute, verify,
              visible=False, critical=False, cancel_safe=True,
              compensate=None, probe_restoration=None,
              verify_restoration=None):
        return StepDefinition(
            step_key=key,
            phase=phase,
            sequence=seq,
            unit=phase,
            resources=resources,
            externally_visible=visible,
            critical=critical,
            cancel_safe_before=cancel_safe,
            safe_if_uncertain=False,
            derive_input=_input(),
            probe=probe,
            execute=execute,
            verify=verify,
            compensate=compensate,
            probe_restoration=probe_restoration,
            verify_restoration=verify_restoration,
        )

    both = (RUNTIME_ACTIVE_RESOURCE, RUNTIME_INSTALLATION_RESOURCE)
    steps = (
        _step("resolve_rollback_target", "resolve-target", 1, both,
              probe=cb["resolve_probe"], execute=cb["resolve_execute"],
              verify=cb["resolve_verify"]),
        _step("preflight_rollback", "preflight", 2, both,
              probe=cb["preflight_probe"], execute=cb["preflight_execute"],
              verify=cb["preflight_verify"]),
        _step("smoke_rollback_target", "smoke", 3, both,
              probe=cb["smoke_probe"], execute=cb["smoke_execute"],
              verify=cb["smoke_verify"]),
        _step("capture_rollback_boundary", "activation-boundary", 4, both,
              probe=lambda ctx: ProbeResult(
                  RecoveryClass.ABSENT, "NO_BOUNDARY_SNAPSHOT"),
              execute=cb["capture_execute"],
              verify=cb["capture_verify"]),
        _step("exchange_active_tree", "exchange", 5, both,
              visible=True, critical=True, cancel_safe=False,
              probe=cb["exchange_probe"], execute=cb["exchange_execute"],
              verify=cb["exchange_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("publish_component_handoff", "handoff", 6, both,
              visible=True, critical=True, cancel_safe=False,
              probe=cb["publish_probe"], execute=cb["publish_execute"],
              verify=cb["publish_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("restart_runtime", "restart", 7, both,
              visible=True, critical=True, cancel_safe=False,
              probe=cb["restart_probe"], execute=cb["restart_execute"],
              verify=cb["restart_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("verify_runtime", "verify", 8, both,
              critical=True, cancel_safe=False,
              probe=cb["identity_probe"], execute=cb["identity_execute"],
              verify=lambda ctx: {}),
        _step("promote_rollback", "promote", 9, both,
              visible=True, critical=True, cancel_safe=False,
              probe=lambda ctx: ProbeResult(
                  RecoveryClass.ABSENT, "PROMOTION_NOT_EVIDENCED"),
              execute=cb["promote_execute"],
              verify=cb["promote_verify"],
              compensate=cb["compensate_via_restoration"],
              probe_restoration=cb["probe_restored"],
              verify_restoration=cb["verify_restored"]),
        _step("finalize_trees", "finalize", 10, both,
              probe=lambda ctx: ProbeResult(
                  RecoveryClass.ABSENT, "NO_FINALIZATION_EVIDENCE"),
              execute=cb["finalize_execute"],
              verify=cb["finalize_verify"]),
    )

    def terminal_decision(request, outputs) -> TerminalDecision:
        return TerminalDecision(
            OperationState.SUCCEEDED,
            CODE_RUNTIME_RESTORED,
            {"restored_build": request.target_build_id},
            f"runtime restored to {request.target_build_id} and live-verified",
        )

    return WorkflowDefinition(
        operation_type=ROLLBACK_OPERATION_TYPE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_runtime_rollback_request,
        steps=steps,
        summary=lambda request: (
            f"roll llama.cpp runtime back to {request.target_build_id}"
        ),
        preflight=lambda request: None,
        phase_scoped_resources=True,
        recovery_barrier_resources=both,
        terminal_decision=terminal_decision,
    )
