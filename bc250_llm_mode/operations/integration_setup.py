"""Durable ``INTEGRATION_SETUP v1`` workflow for guided Connections.

The request contains only public identities and pre-effect observations. Secret
bytes remain in mode-0600 files owned by the credential service and are never
durable operation input/output/event data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .model import OperationState, OperationType
from .recovery import RecoveryClass
from .validation import OperationValidationError
from .workflow import (
    EffectContext,
    ProbeResult,
    StepDefinition,
    TerminalDecision,
    WorkflowDefinition,
)


REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1
STEP_VERSION = 1
OPERATION_TYPE = OperationType.INTEGRATION_SETUP
INTEGRATION_RESOURCES = (
    "connection-clients",
    "integration-gateway",
    "integration-openwebui",
    "integration-sharing",
    "runtime-active",
)
INTENTS = frozenset({
    "OPENWEBUI", "PHONE_TABLET", "DESKTOP_APP", "DEVELOPER",
})
CLIENT_KIND_FOR_INTENT = {
    "OPENWEBUI": "openwebui",
    "PHONE_TABLET": "pocketpal",
    "DESKTOP_APP": "openai",
    "DEVELOPER": "curl",
}
_CLIENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_ALIAS = re.compile(r"[A-Za-z0-9_.:/+-]{1,128}\Z")
_SURFACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


@dataclass(frozen=True)
class IntegrationSetupRequestV1:
    intent: str
    client_id: str
    client_label: str
    client_kind: str
    webui_client_id: str
    public_alias: str
    require_tailnet: bool
    baseline_client_ids: tuple[str, ...]
    baseline_access_enabled: bool
    baseline_model_active: bool
    baseline_gateway_consumers: tuple[str, ...]
    baseline_openwebui_running: bool
    baseline_sharing_enabled: bool
    requested_by: str = "gui"


def _short(value: Any, *, field: str, maximum: int = 80) -> str:
    if not isinstance(value, str):
        raise OperationValidationError(f"{field} must be text")
    text = " ".join(value.strip().split())
    if not (1 <= len(text) <= maximum) or any(ord(char) < 32 for char in text):
        raise OperationValidationError(f"{field} is invalid")
    return text


def decode_integration_setup_request(
    payload: dict[str, Any],
) -> IntegrationSetupRequestV1:
    allowed = {
        "intent", "client_id", "client_label", "client_kind",
        "webui_client_id", "public_alias", "require_tailnet",
        "baseline_client_ids", "baseline_access_enabled",
        "baseline_model_active", "baseline_gateway_consumers",
        "baseline_openwebui_running", "baseline_sharing_enabled",
        "requested_by",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise OperationValidationError(
            f"unknown integration fields: {sorted(unknown)}")
    intent = str(payload.get("intent") or "").upper()
    if intent not in INTENTS:
        raise OperationValidationError("connection intent is unknown")
    client_id = str(payload.get("client_id") or "")
    webui_client_id = str(payload.get("webui_client_id") or "")
    if not _CLIENT_ID.fullmatch(client_id) or not _CLIENT_ID.fullmatch(webui_client_id):
        raise OperationValidationError("client identities must be generated UUID hex")
    alias = str(payload.get("public_alias") or "")
    if not _ALIAS.fullmatch(alias):
        raise OperationValidationError("public model alias is invalid")
    required_bools = (
        "require_tailnet", "baseline_access_enabled", "baseline_model_active",
        "baseline_openwebui_running", "baseline_sharing_enabled",
    )
    if any(not isinstance(payload.get(name), bool) for name in required_bools):
        raise OperationValidationError("integration baseline flags must be booleans")
    consumers = payload.get("baseline_gateway_consumers")
    if not isinstance(consumers, list) or len(consumers) > 3:
        raise OperationValidationError("gateway consumer baseline is invalid")
    normalized_consumers = tuple(sorted(set(str(value) for value in consumers)))
    if set(normalized_consumers) - {"client", "openwebui", "sharing"}:
        raise OperationValidationError("gateway consumer baseline is invalid")
    client_ids = payload.get("baseline_client_ids")
    if not isinstance(client_ids, list) or len(client_ids) > 32:
        raise OperationValidationError("client baseline is invalid")
    normalized_client_ids = tuple(sorted(set(str(value) for value in client_ids)))
    if any(not _CLIENT_ID.fullmatch(value) and value != "legacy-install"
           for value in normalized_client_ids):
        raise OperationValidationError("client baseline is invalid")
    requested_by = str(payload.get("requested_by") or "gui")
    if not _SURFACE.fullmatch(requested_by):
        raise OperationValidationError("requested_by is invalid")
    client_kind = _short(
        payload.get("client_kind"), field="client_kind", maximum=24)
    if client_kind != CLIENT_KIND_FOR_INTENT[intent]:
        raise OperationValidationError(
            "client kind does not match the connection intent")
    if (intent == "OPENWEBUI") != (client_id == webui_client_id):
        raise OperationValidationError(
            "Open WebUI and external clients must use separate identities")
    return IntegrationSetupRequestV1(
        intent=intent,
        client_id=client_id,
        client_label=_short(payload.get("client_label"), field="client_label"),
        client_kind=client_kind,
        webui_client_id=webui_client_id,
        public_alias=alias,
        require_tailnet=payload["require_tailnet"],
        baseline_client_ids=normalized_client_ids,
        baseline_access_enabled=payload["baseline_access_enabled"],
        baseline_model_active=payload["baseline_model_active"],
        baseline_gateway_consumers=normalized_consumers,
        baseline_openwebui_running=payload["baseline_openwebui_running"],
        baseline_sharing_enabled=payload["baseline_sharing_enabled"],
        requested_by=requested_by,
    )


class IntegrationSetupHost:
    def ensure_clients(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_clients(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def remove_created_clients(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_clients_restored(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def start_model(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_model(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def restore_model(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_model_restored(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def start_gateway(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_gateway(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def restore_gateway(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_gateway_restored(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def start_openwebui(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_openwebui(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def restore_openwebui(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_openwebui_restored(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def publish_tailnet(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_tailnet(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def restore_tailnet(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_tailnet_restored(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError

    def verify_client(self, ctx: EffectContext) -> dict[str, Any]:
        raise NotImplementedError

    def probe_client_verification(self, ctx: EffectContext) -> ProbeResult:
        raise NotImplementedError


def _require_complete(value: ProbeResult, code: str) -> dict[str, Any]:
    if value.classification is not RecoveryClass.COMPLETE:
        raise OperationValidationError(f"{code}: {value.reason_code}")
    return value.output or {}


def build_integration_setup_workflow(
    host: IntegrationSetupHost,
) -> WorkflowDefinition:
    resources = INTEGRATION_RESOURCES
    steps = (
        StepDefinition(
            "ensure_clients", "client", 1,
            derive_input=lambda request, prior: {
                "client_id": request.client_id,
                "webui_client_id": request.webui_client_id,
                "intent": request.intent,
                "baseline_client_ids": list(request.baseline_client_ids),
                "baseline_access_enabled": request.baseline_access_enabled,
            },
            probe=host.probe_clients,
            execute=host.ensure_clients,
            verify=lambda ctx: _require_complete(
                host.probe_clients(ctx), "CLIENT_NOT_READY"),
            resources=resources,
            externally_visible=True,
            effect_disposition="REVERSIBLE",
            compensate=host.remove_created_clients,
            probe_restoration=host.probe_clients_restored,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_clients_restored(ctx), "CLIENT_RESTORE_FAILED"),
        ),
        StepDefinition(
            "start_model", "model", 2,
            derive_input=lambda request, prior: {
                "public_alias": request.public_alias,
                "baseline_active": request.baseline_model_active,
            },
            probe=host.probe_model,
            execute=host.start_model,
            verify=lambda ctx: _require_complete(
                host.probe_model(ctx), "MODEL_NOT_READY"),
            resources=resources,
            externally_visible=True,
            effect_disposition="REVERSIBLE",
            compensate=host.restore_model,
            probe_restoration=host.probe_model_restored,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_model_restored(ctx), "MODEL_RESTORE_FAILED"),
        ),
        StepDefinition(
            "start_gateway", "gateway", 3,
            derive_input=lambda request, prior: {
                "baseline_consumers": list(request.baseline_gateway_consumers),
            },
            probe=host.probe_gateway,
            execute=host.start_gateway,
            verify=lambda ctx: _require_complete(
                host.probe_gateway(ctx), "GATEWAY_NOT_READY"),
            resources=resources,
            externally_visible=True,
            effect_disposition="REVERSIBLE",
            compensate=host.restore_gateway,
            probe_restoration=host.probe_gateway_restored,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_gateway_restored(ctx), "GATEWAY_RESTORE_FAILED"),
        ),
        StepDefinition(
            "start_openwebui", "openwebui", 4,
            derive_input=lambda request, prior: {
                "baseline_running": request.baseline_openwebui_running,
                "public_alias": request.public_alias,
            },
            probe=host.probe_openwebui,
            execute=host.start_openwebui,
            verify=lambda ctx: _require_complete(
                host.probe_openwebui(ctx), "OPENWEBUI_NOT_READY"),
            resources=resources,
            externally_visible=True,
            effect_disposition="REVERSIBLE",
            compensate=host.restore_openwebui,
            probe_restoration=host.probe_openwebui_restored,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_openwebui_restored(ctx), "OPENWEBUI_RESTORE_FAILED"),
        ),
        StepDefinition(
            "publish_tailnet", "tailnet", 5,
            derive_input=lambda request, prior: {
                "required": request.require_tailnet,
                "baseline_enabled": request.baseline_sharing_enabled,
            },
            probe=host.probe_tailnet,
            execute=host.publish_tailnet,
            verify=lambda ctx: _require_complete(
                host.probe_tailnet(ctx), "TAILNET_NOT_READY"),
            resources=resources,
            externally_visible=True,
            effect_disposition="REVERSIBLE",
            compensate=host.restore_tailnet,
            probe_restoration=host.probe_tailnet_restored,
            verify_restoration=lambda ctx: _require_complete(
                host.probe_tailnet_restored(ctx), "TAILNET_RESTORE_FAILED"),
        ),
        StepDefinition(
            "verify_client", "verify", 6,
            derive_input=lambda request, prior: {
                "client_id": request.client_id,
                "public_alias": request.public_alias,
                "require_tailnet": request.require_tailnet,
            },
            probe=host.probe_client_verification,
            execute=host.verify_client,
            verify=lambda ctx: _require_complete(
                host.probe_client_verification(ctx), "CLIENT_TEST_FAILED"),
            resources=resources,
            effect_disposition="HIDDEN_DURABLE",
        ),
    )

    def terminal(
        request: IntegrationSetupRequestV1, outputs: dict[str, Any],
    ) -> TerminalDecision:
        verification = outputs.get("verify_client") or {}
        return TerminalDecision(
            OperationState.SUCCEEDED,
            "INTEGRATION_READY",
            {
                "intent": request.intent,
                "client_id": request.client_id,
                "public_alias": request.public_alias,
                "local_ready": bool(verification.get("local_ready")),
                "tailnet_ready": bool(verification.get("tailnet_ready")),
            },
            "named client integration verified",
        )

    return WorkflowDefinition(
        operation_type=OPERATION_TYPE,
        request_version=REQUEST_VERSION,
        recovery_policy_version=RECOVERY_POLICY_VERSION,
        decode_request=decode_integration_setup_request,
        steps=steps,
        summary=lambda request: (
            f"Connect {request.client_label} through the private gateway"),
        terminal_decision=terminal,
    )


__all__ = [
    "CLIENT_KIND_FOR_INTENT", "INTEGRATION_RESOURCES", "INTENTS", "IntegrationSetupHost",
    "IntegrationSetupRequestV1", "OPERATION_TYPE", "RECOVERY_POLICY_VERSION",
    "REQUEST_VERSION", "build_integration_setup_workflow",
    "decode_integration_setup_request",
]
