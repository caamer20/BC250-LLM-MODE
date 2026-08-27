"""Durable ``MODEL_REMOVE v1`` workflow (P6 §12.2).

Forward-only removal: an alias is detached in a transaction, and any bytes
that become unreferenced are MOVED to an operation-owned quarantine area —
never deleted outright — so a bounded undo exists while the retention window
holds. Active / known-good / referenced artifacts are never destructively
removed: the blocker gate refuses before any mutation and is re-verified
durably by the resolve step.

The workflow is registered in the ONE frozen registry and driven by the same
engine factory as activation/acquisition/runtime, so cleanup and undo survive
process death and lease takeover.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .model import OperationType
from .recovery import RecoveryClass
from .workflow import (
    EffectContext,
    ProbeResult,
    StepDefinition,
    TerminalDecision,
    WorkflowDefinition,
)
from .validation import OperationValidationError

REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1

# Removal contends with acquisition/import over the same model storage.
REMOVE_RESOURCE = "model-storage"

CODE_MODEL_REMOVED = "MODEL_REMOVED"
CODE_REMOVAL_BLOCKED = "REMOVAL_BLOCKED"
CODE_ALIAS_UNKNOWN = "ALIAS_UNKNOWN"
CODE_QUARANTINE_RETAINED = "QUARANTINE_RETAINED"

MAX_ALIAS_CHARS = 128


@dataclass(frozen=True)
class ModelRemoveRequestV1:
    alias: str
    requested_by: str = "cli"


def _closed(payload: dict[str, Any], fields: frozenset) -> None:
    unknown = set(payload) - fields
    if unknown:
        raise OperationValidationError(
            f"unknown request fields: {sorted(unknown)}"
        )


def decode_remove_request(payload: dict[str, Any]) -> ModelRemoveRequestV1:
    _closed(payload, frozenset({"alias", "requested_by"}))
    alias = payload.get("alias")
    if (
        not isinstance(alias, str)
        or not alias.strip()
        or len(alias) > MAX_ALIAS_CHARS
    ):
        raise OperationValidationError("alias must be a short non-empty string")
    return ModelRemoveRequestV1(
        alias=alias, requested_by=str(payload.get("requested_by", "cli"))
    )


def _evidence(value: Any) -> dict[str, Any]:
    return {"evidence": value}


def _require_complete(result: ProbeResult, code: str) -> None:
    if result.classification is not RecoveryClass.COMPLETE:
        raise OperationValidationError(
            f"removal precondition not met: {code} ({result.reason_code})"
        )


class ModelRemoveHost:
    """Typed port the production adapter satisfies (P6 §12.2)."""

    def resolve_identity(self, request: ModelRemoveRequestV1) -> dict[str, Any]:
        raise NotImplementedError

    def probe_identity(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def detach_alias(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_detachment(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def quarantine_bytes(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_quarantine(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def record_removal(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_removal_record(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError


def _terminal(request: Any, verified_outputs: dict) -> TerminalDecision:
    from .model import OperationState

    removal = (verified_outputs.get("record_removal") or {}).get(
        "removal"
    ) or {}
    evidence = removal.get("evidence") or {}
    disposition = evidence.get("disposition") or CODE_MODEL_REMOVED
    return TerminalDecision(
        OperationState.SUCCEEDED,
        disposition,
        {"alias": request.alias},
        f"removed alias {request.alias!r}; unreferenced bytes quarantined",
    )


def build_remove_workflow(host: ModelRemoveHost) -> WorkflowDefinition:
    """Frozen ``MODEL_REMOVE v1``: four forward-only steps."""

    def resolve_execute(ctx: EffectContext) -> dict[str, Any]:
        identity = host.resolve_identity(ctx.request)
        return {"identity": _evidence(identity)}

    def resolve_verify(ctx: EffectContext) -> dict[str, Any]:
        _require_complete(host.probe_identity(ctx), CODE_ALIAS_UNKNOWN)
        return {}

    def detach_execute(ctx: EffectContext) -> dict[str, Any]:
        return {"detachment": _evidence(host.detach_alias(ctx))}

    def quarantine_execute(ctx: EffectContext) -> dict[str, Any]:
        return {"quarantine": _evidence(host.quarantine_bytes(ctx))}

    def record_execute(ctx: EffectContext) -> dict[str, Any]:
        return {"removal": _evidence(host.record_removal(ctx))}

    steps = (
        StepDefinition(
            step_key="resolve_identity",
            phase="resolve",
            sequence=1,
            derive_input=lambda request, prior: {"alias": request.alias},
            probe=host.probe_identity,
            execute=resolve_execute,
            verify=resolve_verify,
            effect_disposition="REVERSIBLE",
            resources=(REMOVE_RESOURCE,),
        ),
        StepDefinition(
            step_key="detach_alias",
            phase="mutate",
            sequence=2,
            derive_input=lambda request, prior: {"alias": request.alias},
            probe=host.probe_detachment,
            execute=detach_execute,
            verify=lambda ctx: _require_complete(
                host.probe_detachment(ctx), "DETACHMENT_INCOMPLETE"
            ),
            effect_disposition="HIDDEN_DURABLE",
            critical=True,
            resources=(REMOVE_RESOURCE,),
        ),
        StepDefinition(
            step_key="quarantine_bytes",
            phase="mutate",
            sequence=3,
            derive_input=lambda request, prior: {"alias": request.alias},
            probe=host.probe_quarantine,
            execute=quarantine_execute,
            verify=lambda ctx: _require_complete(
                host.probe_quarantine(ctx), "QUARANTINE_INCOMPLETE"
            ),
            effect_disposition="FORWARD_ONLY",
            critical=True,
            resources=(REMOVE_RESOURCE,),
        ),
        StepDefinition(
            step_key="record_removal",
            phase="finalize",
            sequence=4,
            derive_input=lambda request, prior: {"alias": request.alias},
            probe=host.probe_removal_record,
            execute=record_execute,
            verify=lambda ctx: _require_complete(
                host.probe_removal_record(ctx), "REMOVAL_RECORD_MISSING"
            ),
            effect_disposition="HIDDEN_DURABLE",
            resources=(REMOVE_RESOURCE,),
        ),
    )

    return WorkflowDefinition(
        operation_type=OperationType.MODEL_REMOVE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_remove_request,
        steps=steps,
        summary=lambda request: f"Remove model alias {request.alias!r}",
        terminal_decision=_terminal,
    )
