"""Durable ``MODEL_ACQUIRE v1`` / ``MODEL_IMPORT v1`` workflows (U1.1).

Pure definitions only: requests, closed evidence types, stable codes, the
typed ``AcquisitionHost`` port, the eight-step workflow definitions, and
the closed terminal decision. No Path/subprocess/HTTP/SQL/tkinter imports;
the production implementation lives in ``acquisition_adapter.py``.
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

OPERATION_ACQUIRE = OperationType.MODEL_ACQUIRE
OPERATION_IMPORT = OperationType.MODEL_IMPORT
REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1

# ADR 002 §16: supersedes the provisional model:<artifact-id> key because
# the content digest is unknown until transfer completes.
ACQUISITION_RESOURCE = "model-storage"

CODE_MODEL_INSTALLED = "MODEL_INSTALLED"
CODE_MODEL_REUSED = "MODEL_REUSED"
CODE_ARTIFACT_QUARANTINED = "ARTIFACT_QUARANTINED"
CODE_CANCELLED_PARTIAL_RETAINED = "CANCELLED_PARTIAL_RETAINED"
CODE_SOURCE_CHANGED = "SOURCE_CHANGED"
CODE_LOCAL_SOURCE_CHANGED = "LOCAL_SOURCE_CHANGED"
CODE_GGUF_INVALID = "GGUF_INVALID"

MAX_ALIAS_CHARS = 128
MAX_FILENAME_CHARS = 256
MAX_SOURCE_FILES = 32


@dataclass(frozen=True)
class ModelAcquireRequestV1:
    model_id: str
    quantization: str
    catalog_entry_fingerprint: str = ""
    requested_by: str = "cli"


@dataclass(frozen=True)
class ModelImportRequestV1:
    source_path: str
    alias: str | None = None
    display_name: str | None = None
    quantization: str | None = None
    requested_by: str = "cli"


def _closed(payload: dict[str, Any], fields: frozenset) -> None:
    unknown = set(payload) - fields
    if unknown:
        raise OperationValidationError(f"unknown request fields: {sorted(unknown)}")


def decode_acquire_request(payload: dict[str, Any]) -> ModelAcquireRequestV1:
    _closed(
        payload,
        frozenset({"model_id", "quantization", "catalog_entry_fingerprint", "requested_by"}),
    )
    model_id = payload.get("model_id")
    quantization = payload.get("quantization")
    for name, value in (("model_id", model_id), ("quantization", quantization)):
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_ALIAS_CHARS:
            raise OperationValidationError(f"{name} must be a short string")
    return ModelAcquireRequestV1(
        model_id=model_id,  # type: ignore[arg-type]
        quantization=quantization,  # type: ignore[arg-type]
        catalog_entry_fingerprint=str(payload.get("catalog_entry_fingerprint") or ""),
        requested_by=str(payload.get("requested_by", "cli")),
    )


def decode_import_request(payload: dict[str, Any]) -> ModelImportRequestV1:
    _closed(
        payload,
        frozenset({"source_path", "alias", "display_name", "quantization", "requested_by"}),
    )
    source_path = payload.get("source_path")
    if not isinstance(source_path, str) or not source_path.strip():
        raise OperationValidationError("source_path must be a non-empty string")
    # U1.1 §3.1 (P1): local sources must be absolute user-selected paths;
    # NUL bytes and oversized strings are refused outright.
    import os as _os

    if "\x00" in source_path or len(source_path) > 1024:
        raise OperationValidationError("source_path is malformed")
    if not _os.path.isabs(source_path):
        raise OperationValidationError("source_path must be absolute")
    alias = payload.get("alias")
    if alias is not None and (
        not isinstance(alias, str)
        or not alias.strip()
        or len(alias) > MAX_ALIAS_CHARS
    ):
        raise OperationValidationError("alias must be a short non-empty string")
    display_name = payload.get("display_name")
    if display_name is not None and (
        not isinstance(display_name, str) or len(display_name) > MAX_ALIAS_CHARS
    ):
        raise OperationValidationError("display_name too long")
    quantization = payload.get("quantization")
    if quantization is not None and (
        not isinstance(quantization, str) or len(quantization) > MAX_ALIAS_CHARS
    ):
        raise OperationValidationError("quantization too long")
    return ModelImportRequestV1(
        source_path=source_path,
        alias=alias,
        display_name=display_name,
        quantization=quantization,
        requested_by=str(payload.get("requested_by", "cli"))[:64],
    )


# -- Closed evidence contracts ----------------------------------------------------


@dataclass(frozen=True)
class SourceIdentity:
    """Immutable source identity persisted before any transfer begins."""

    fingerprint: str
    files: tuple[dict[str, Any], ...] = ()
    total_bytes: int = 0
    revision: str | None = None


@dataclass(frozen=True)
class DiskPreflightEvidence:
    filesystem_identity: str
    required_bytes: int
    available_bytes: int
    reserved_bytes: int
    reclaimable_owned_bytes: int = 0
    credited_partial_bytes: int = 0


@dataclass(frozen=True)
class TransferEvidence:
    fingerprint: str
    bytes_complete: int
    total_bytes: int
    validator_digest: str
    receipt_version: int = 1


@dataclass(frozen=True)
class CandidateEvidence:
    staged_path_rel: str
    byte_size: int
    content_digest: str
    recipe_identity: str = ""


@dataclass(frozen=True)
class ValidationEvidence:
    verdict: str  # ok | invalid
    format: str = "GGUF"
    architecture: str | None = None
    quantization: str | None = None
    tensor_count: int | None = None
    layout_verdict: str = "standard"
    validator_version: int = 1
    reason_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicationEvidence:
    content_digest: str
    final_path_rel: str
    file_identity: str
    disposition: str  # published | reused | quarantined
    receipt_version: int = 1


@dataclass(frozen=True)
class RegistrationEvidence:
    artifact_id: str
    alias: str | None = None
    disposition: str = ""  # installed | reused | quarantined


@dataclass(frozen=True)
class CleanupEvidence:
    removed_paths: tuple[str, ...] = ()
    retained_paths: tuple[str, ...] = ()
    reservation_released: bool = False


# -- Typed acquisition port (§9.1) -------------------------------------------------


class AcquisitionHost(Protocol):
    def resolve_catalog_source(
        self, request: ModelAcquireRequestV1
    ) -> SourceIdentity: ...

    def observe_local_source(
        self, request: ModelImportRequestV1
    ) -> SourceIdentity: ...

    def preflight_storage(
        self, request: Any, source: SourceIdentity
    ) -> DiskPreflightEvidence: ...

    def reserve_storage(
        self,
        request: Any,
        source: SourceIdentity,
        preflight: DiskPreflightEvidence,
        external_effect_id: str,
    ) -> DiskPreflightEvidence: ...

    def observe_reservation(self, request: Any) -> ProbeResult: ...

    def probe_transfer(self, ctx: EffectContext) -> ProbeResult: ...

    def transfer_catalog(self, ctx: EffectContext) -> TransferEvidence: ...

    def copy_local(self, ctx: EffectContext) -> TransferEvidence: ...

    def probe_candidate(self, ctx: EffectContext) -> ProbeResult: ...

    def materialize_candidate(self, ctx: EffectContext) -> CandidateEvidence: ...

    def hash_and_validate_candidate(
        self, ctx: EffectContext
    ) -> ValidationEvidence: ...

    def probe_publication(self, ctx: EffectContext) -> ProbeResult: ...

    def publish_or_reuse(self, ctx: EffectContext) -> PublicationEvidence: ...

    def quarantine_candidate(self, ctx: EffectContext) -> PublicationEvidence: ...

    def probe_registration(self, ctx: EffectContext) -> ProbeResult: ...

    def register_installation(self, ctx: EffectContext) -> RegistrationEvidence: ...

    def probe_finalization(self, ctx: EffectContext) -> ProbeResult: ...

    def finalize_owned_staging(self, ctx: EffectContext) -> CleanupEvidence: ...

    def release_on_cancellation(self, request: Any) -> dict[str, Any]:
        """Release the logical reservation; report retained partial bytes."""

    def compensate_acquisition(self, ctx: EffectContext) -> dict[str, Any]: ...

    def observe_compensation(self, ctx: EffectContext) -> ProbeResult: ...


# -- Workflow definitions (§10) ---------------------------------------------------


def _evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return asdict(value)


def _require_complete(result: ProbeResult, code: str) -> None:
    from .workflow import StepFailure

    if result.classification is not RecoveryClass.COMPLETE:
        raise StepFailure(code, f"postcondition not proven ({result.reason_code})")


def _identity_matches(stored: dict[str, Any], fresh: SourceIdentity) -> bool:
    return bool(stored) and stored.get("fingerprint") == fresh.fingerprint


def _acquire_step_callbacks(host: AcquisitionHost) -> dict[str, Any]:
    def resolve_execute(ctx: EffectContext) -> dict[str, Any]:
        identity = host.resolve_catalog_source(ctx.request)
        return {"source_identity": _evidence(identity)}

    def resolve_probe(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("resolve_source") or {}
        identity = output.get("source_identity") or {}
        fresh = host.resolve_catalog_source(ctx.request)
        if identity and not _identity_matches(identity, fresh):
            return ProbeResult(RecoveryClass.REVERTIBLE, CODE_SOURCE_CHANGED)
        if identity:
            return ProbeResult(RecoveryClass.COMPLETE, "SOURCE_RESOLVED", output)
        return ProbeResult(
            RecoveryClass.ABSENT,
            "SOURCE_UNRESOLVED",
            {"source_identity": _evidence(fresh)},
        )

    def reserve_input(request, prior) -> dict[str, Any]:
        identity = (prior.get("resolve_source") or {}).get("source_identity")
        return {"source": identity or {}}

    def reserve_execute(ctx: EffectContext) -> dict[str, Any]:
        source = SourceIdentity(**(ctx.inputs.get("source") or {}))
        preflight = host.preflight_storage(ctx.request, source)
        reserved = host.reserve_storage(
            ctx.request, source, preflight, ctx.external_effect_id
        )
        return {
            "preflight": _evidence(preflight),
            "reservation": _evidence(reserved),
        }

    def transfer_execute(ctx: EffectContext) -> dict[str, Any]:
        evidence = host.transfer_catalog(ctx)
        return {"transfer": _evidence(evidence)}

    def transfer_verify(ctx: EffectContext) -> dict[str, Any]:
        from .workflow import StepFailure

        transfer = (ctx.prior_outputs.get("transfer_source") or {}).get(
            "transfer"
        ) or {}
        if int(transfer.get("bytes_complete", -1)) != int(
            transfer.get("total_bytes", -2)
        ):
            raise StepFailure("PARTIAL_NOT_RESUMABLE", "transfer bytes incomplete")
        return {}

    def materialize_execute(ctx: EffectContext) -> dict[str, Any]:
        candidate = host.materialize_candidate(ctx)
        return {"candidate": _evidence(candidate)}

    def validate_execute(ctx: EffectContext) -> dict[str, Any]:
        validation = host.hash_and_validate_candidate(ctx)
        return {"validation": _evidence(validation)}

    def publish_execute(ctx: EffectContext) -> dict[str, Any]:
        validation = ValidationEvidence(
            **(((ctx.prior_outputs.get("validate_candidate") or {}).get(
                "validation"
            )) or {})
        )
        if validation.verdict == "ok":
            publication = host.publish_or_reuse(ctx)
        else:
            publication = host.quarantine_candidate(ctx)
        return {"publication": _evidence(publication)}

    def register_execute(ctx: EffectContext) -> dict[str, Any]:
        registration = host.register_installation(ctx)
        return {"registration": _evidence(registration)}

    def finalize_execute(ctx: EffectContext) -> dict[str, Any]:
        cleanup = host.finalize_owned_staging(ctx)
        return {"cleanup": _evidence(cleanup)}

    def compensate_via_host(ctx: EffectContext) -> dict[str, Any]:
        return host.compensate_acquisition(ctx)

    def verify_compensated(ctx: EffectContext) -> dict[str, Any]:
        result = host.observe_compensation(ctx)
        _require_complete(result, "RECOVERY_PUBLICATION_MISMATCH")
        return {}

    return {
        "resolve_probe": resolve_probe,
        "reserve_input": reserve_input,
        "reserve_execute": reserve_execute,
        "transfer_execute": transfer_execute,
        "transfer_verify": transfer_verify,
        "materialize_execute": materialize_execute,
        "validate_execute": validate_execute,
        "publish_execute": publish_execute,
        "register_execute": register_execute,
        "finalize_execute": finalize_execute,
        "compensate_via_host": compensate_via_host,
        "verify_compensated": verify_compensated,
    }


def decide_acquisition_terminal(
    verified_outputs: dict[str, Any], *, quarantined: bool | None
) -> TerminalDecision:
    """Closed terminal resolver (U1.1 §8.1): success vs safe quarantine."""
    if quarantined is None:
        raise OperationValidationError(
            "terminal decision requires an explicit quarantine flag"
        )
    if quarantined:
        return TerminalDecision(
            OperationState.FAILED_SAFE,
            CODE_ARTIFACT_QUARANTINED,
            {},
            "candidate failed validation and was quarantined",
        )
    registration = (
        verified_outputs.get("register_installation") or {}
    ).get("registration") or {}
    publication = (
        (verified_outputs.get("publish_artifact") or {}).get("publication")
        or {}
    )
    quarantined_by_publication = publication.get("disposition") == "quarantined"
    disposition = registration.get("disposition") or (
        "quarantined" if quarantined_by_publication else ""
    )
    code = CODE_MODEL_REUSED if disposition == "reused" else CODE_MODEL_INSTALLED
    return TerminalDecision(
        OperationState.SUCCEEDED,
        code,
        {
            "artifact_id": registration.get("artifact_id"),
            "alias": registration.get("alias"),
        },
        f"model artifact {code.lower()}",
    )


_STEP_LAYOUT = (
    ("resolve_source", "preflight", 1, {}),
    ("reserve_storage", "preflight", 2, {}),
    ("transfer_source", "transfer", 3, {"unit": "bytes"}),
    ("materialize_candidate", "prepare", 4, {}),
    ("validate_candidate", "verify", 5, {}),
    ("publish_artifact", "publish", 6, {"critical": True}),
    ("register_installation", "register", 7, {"critical": True}),
    ("finalize_staging", "cleanup", 8, {"critical": True}),
)


def _build_steps(
    host: AcquisitionHost,
    callbacks: dict[str, Any],
    *,
    resolve_execute: Any,
    resolve_probe: Any,
    resolve_verify: Any,
    transfer_execute: Any,
) -> tuple[StepDefinition, ...]:
    def step_for(key: str, phase: str, seq: int, extra: dict) -> StepDefinition:
        probe_map = {
            "resolve_source": resolve_probe,
            "reserve_storage": lambda ctx, h=host: h.observe_reservation(
                ctx.request
            ),
            "transfer_source": lambda ctx, h=host: h.probe_transfer(ctx),
            "materialize_candidate": lambda ctx, h=host: h.probe_candidate(ctx),
            "validate_candidate": lambda ctx: (
                ProbeResult(RecoveryClass.COMPLETE, "VALIDATED")
                if ctx.prior_outputs.get("validate_candidate")
                else ProbeResult(RecoveryClass.ABSENT, "UNVALIDATED")
            ),
            "publish_artifact": lambda ctx, h=host: h.probe_publication(ctx),
            "register_installation": lambda ctx, h=host: (
                h.probe_registration(ctx)
            ),
            "finalize_staging": lambda ctx, h=host: h.probe_finalization(ctx),
        }
        execute_map = {
            "resolve_source": resolve_execute,
            "reserve_storage": callbacks["reserve_execute"],
            "transfer_source": transfer_execute,
            "materialize_candidate": callbacks["materialize_execute"],
            "validate_candidate": callbacks["validate_execute"],
            "publish_artifact": callbacks["publish_execute"],
            "register_installation": callbacks["register_execute"],
            "finalize_staging": callbacks["finalize_execute"],
        }
        verify_map = {
            "resolve_source": resolve_verify,
            "reserve_storage": lambda ctx, h=host: _require_complete(
                h.observe_reservation(ctx.request),
                "STAGING_OWNERSHIP_INVALID",
            ),
            "transfer_source": callbacks["transfer_verify"],
            "materialize_candidate": lambda ctx, h=host: _require_complete(
                h.probe_candidate(ctx), "CONVERSION_FAILED"
            ),
            "validate_candidate": lambda ctx: {},
            "publish_artifact": lambda ctx, h=host: _require_complete(
                h.probe_publication(ctx), "PUBLICATION_IDENTITY_UNCERTAIN"
            ),
            "register_installation": lambda ctx, h=host: _require_complete(
                h.probe_registration(ctx), "RECOVERY_REGISTRATION_MISMATCH"
            ),
            "finalize_staging": lambda ctx, h=host: _require_complete(
                h.probe_finalization(ctx), "CLEANUP_INCOMPLETE"
            ),
        }
        compensating = extra.get("critical") and key != "finalize_staging"
        if key in ("publish_artifact", "register_installation"):
            disposition = "FORWARD_ONLY"
        elif key != "resolve_source" and key != "reserve_storage":
            disposition = "HIDDEN_DURABLE"
        else:
            disposition = "REVERSIBLE"
        return StepDefinition(
            step_key=key,
            phase=phase,
            sequence=seq,
            unit=extra.get("unit"),
            effect_disposition=disposition,
            critical=bool(extra.get("critical")),
            compensate=(
                callbacks["compensate_via_host"] if compensating else None
            ),
            verify_restoration=(
                callbacks["verify_compensated"] if compensating else None
            ),
            probe_restoration=(
                (lambda ctx: host.observe_compensation(ctx))
                if compensating
                else None
            ),
            derive_input=callbacks["reserve_input"],
            probe=probe_map[key],
            execute=execute_map[key],
            verify=verify_map[key],
            resources=(ACQUISITION_RESOURCE,),
        )

    return tuple(step_for(*layout) for layout in _STEP_LAYOUT)


def build_acquire_workflow(host: AcquisitionHost) -> WorkflowDefinition:
    """Frozen ``MODEL_ACQUIRE v1`` bound to one host implementation."""
    cb = _acquire_step_callbacks(host)

    def resolve_catalog_execute(ctx: EffectContext) -> dict[str, Any]:
        identity = host.resolve_catalog_source(ctx.request)
        return {"source_identity": _evidence(identity)}

    def resolve_verify(ctx: EffectContext) -> dict[str, Any]:
        result = cb["resolve_probe"](ctx)
        _require_complete(result, CODE_SOURCE_CHANGED)
        return {}

    def terminal(request: Any, verified_outputs: dict) -> TerminalDecision:
        registration = verified_outputs.get("register_installation") or {}
        publication = (verified_outputs.get("publish_artifact") or {}).get(
            "publication"
        ) or {}
        quarantined = (
            registration.get("disposition") == "quarantined"
            or publication.get("disposition") == "quarantined"
        )
        return decide_acquisition_terminal(
            verified_outputs, quarantined=bool(quarantined)
        )

    return WorkflowDefinition(
        operation_type=OPERATION_ACQUIRE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_acquire_request,
        steps=_build_steps(
            host,
            cb,
            resolve_execute=resolve_catalog_execute,
            resolve_probe=cb["resolve_probe"],
            resolve_verify=resolve_verify,
            transfer_execute=cb["transfer_execute"],
        ),
        summary=lambda request: f"acquire catalog model {request.model_id}",
        terminal_decision=terminal,
        cancel_finalizer=lambda request, h=host: h.release_on_cancellation(request),
        preflight=lambda request: None,
    )


def build_import_workflow(host: AcquisitionHost) -> WorkflowDefinition:
    """Frozen ``MODEL_IMPORT v1``: same eight keys, local-descriptor source."""
    cb = _acquire_step_callbacks(host)

    def resolve_local_execute(ctx: EffectContext) -> dict[str, Any]:
        identity = host.observe_local_source(ctx.request)
        return {"source_identity": _evidence(identity)}

    def probe_local(ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("resolve_source") or {}
        identity = output.get("source_identity") or {}
        fresh = host.observe_local_source(ctx.request)
        if identity and not _identity_matches(identity, fresh):
            return ProbeResult(RecoveryClass.REVERTIBLE, CODE_LOCAL_SOURCE_CHANGED)
        if identity:
            return ProbeResult(RecoveryClass.COMPLETE, "SOURCE_OBSERVED", output)
        return ProbeResult(
            RecoveryClass.ABSENT,
            "SOURCE_UNOBSERVED",
            {"source_identity": _evidence(fresh)},
        )

    def resolve_verify(ctx: EffectContext) -> dict[str, Any]:
        _require_complete(probe_local(ctx), CODE_LOCAL_SOURCE_CHANGED)
        return {}

    def copy_execute(ctx: EffectContext) -> dict[str, Any]:
        evidence = host.copy_local(ctx)
        return {"transfer": _evidence(evidence)}

    def terminal(request: Any, verified_outputs: dict) -> TerminalDecision:
        registration = verified_outputs.get("register_installation") or {}
        publication = (verified_outputs.get("publish_artifact") or {}).get(
            "publication"
        ) or {}
        quarantined = (
            registration.get("disposition") == "quarantined"
            or publication.get("disposition") == "quarantined"
        )
        return decide_acquisition_terminal(
            verified_outputs, quarantined=bool(quarantined)
        )

    return WorkflowDefinition(
        operation_type=OPERATION_IMPORT,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_import_request,
        steps=_build_steps(
            host,
            cb,
            resolve_execute=resolve_local_execute,
            resolve_probe=probe_local,
            resolve_verify=resolve_verify,
            transfer_execute=copy_execute,
        ),
        # Redacted label only: the source basename never enters durable text
        # (U1.1 §3.1 P1).
        summary=lambda request: "import local GGUF into managed storage",
        terminal_decision=terminal,
        cancel_finalizer=lambda request, h=host: h.release_on_cancellation(request),
        preflight=lambda request: None,
    )



