"""One foreground command surface for the durable connection assistant."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .connection_setup import PROBE_OBSERVATION_PREFIX, instructions_for
from .operations.integration_setup import (
    CLIENT_KIND_FOR_INTENT,
    INTENTS,
    OPERATION_TYPE,
)
from .operations.model import OperationState
from .operations.repositories import OperationRepository, json_loads_or_none


@dataclass(frozen=True)
class IntegrationSetupOutcome:
    operation_id: str | None
    status: str
    client_id: str | None = None
    secret: str | None = field(default=None, repr=False)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "READY"

    def to_dict(self, *, reveal_secret: bool = False) -> dict[str, Any]:
        value = {
            "operation_id": self.operation_id,
            "status": self.status,
            "client_id": self.client_id,
            **self.detail,
        }
        if reveal_secret and self.secret is not None:
            value["api_key"] = self.secret
        else:
            value["secret_revealed"] = False
        return value


class IntegrationSetupCommandService:
    def __init__(
        self,
        *,
        application: Any,
        units: Any,
        enqueue: Any,
        engine_factory: Callable[[], Any],
        id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.app = application
        self.units = units
        self.enqueue = enqueue
        self.engine_factory = engine_factory
        self.id_provider = id_provider or (lambda: uuid.uuid4().hex)

    def _active_clients(self) -> list[dict[str, Any]]:
        return [
            item for item in self.app.connection_credentials.list_clients(
                include_revoked=False)
            if not item.get("revoked_at")
        ]

    def _webui_client_id(self, clients: list[dict[str, Any]]) -> str:
        matches = [
            item for item in clients
            if item.get("client_kind") == "openwebui"
        ]
        if len(matches) > 1:
            raise RuntimeError(
                "More than one active Open WebUI client exists; revoke the stale one before setup."
            )
        return str(matches[0]["client_id"]) if matches else self.id_provider()

    def _baseline(self, clients: list[dict[str, Any]]) -> dict[str, Any]:
        state = self.app.read_model()
        runner = self.app.runner()
        try:
            model = self.app.model_server.status(state, runner)
        except Exception:
            model = {}
        try:
            gateway = self.app.gateway_service.status(runner)
        except Exception:
            gateway = {}
        try:
            webui = self.app.openwebui.status(state, runner)
        except Exception:
            webui = {}
        try:
            sharing = self.app.sharing.status(state, runner)
        except Exception:
            sharing = {}
        return {
            "state": state,
            "client_ids": [item["client_id"] for item in clients],
            "access_enabled": bool(
                self.app.connection_credentials.access_state()["enabled"]),
            "model_active": bool(model.get("active")),
            "gateway_consumers": list(gateway.get("current_boot_consumers", ())),
            "openwebui_running": bool(webui.get("running")),
            "sharing_enabled": bool(sharing.get("enabled")),
        }

    def start(
        self,
        *,
        intent: str,
        label: str,
        client_id: str | None = None,
        require_tailnet: bool = True,
        requested_by: str = "gui",
    ) -> IntegrationSetupOutcome:
        intent = str(intent).strip().upper()
        if intent not in INTENTS:
            raise ValueError("unknown connection intent")
        active = self._active_clients()
        baseline = self._baseline(active)
        webui_id = self._webui_client_id(active)
        if intent == "OPENWEBUI":
            selected_id = webui_id
            label = "Open WebUI"
        elif client_id:
            selected = next(
                (item for item in active if item["client_id"] == client_id), None)
            if selected is None:
                raise ValueError("selected client is not active")
            if selected.get("client_kind") != CLIENT_KIND_FOR_INTENT[intent]:
                raise ValueError("selected client does not match this connection intent")
            selected_id = client_id
            label = str(selected["label"])
        else:
            selected_id = self.id_provider()
        alias = str(baseline["state"].get("current_model") or "").strip()
        if not alias:
            return IntegrationSetupOutcome(
                None, "BLOCKED", detail={
                    "reason_code": "MODEL_NOT_SELECTED",
                    "safe_action": "Choose a model, then run connection setup again.",
                })
        with self.units.read() as conn:
            active_operations = [
                item for item in OperationRepository(conn).list_active()
                if item.operation_type is OPERATION_TYPE
            ]
        if active_operations:
            return IntegrationSetupOutcome(
                active_operations[0].id, "BUSY", detail={
                    "reason_code": "INTEGRATION_ALREADY_RUNNING"})
        record = self.enqueue.enqueue(
            operation_type=OPERATION_TYPE,
            payload={
                "intent": intent,
                "client_id": selected_id,
                "client_label": label,
                "client_kind": CLIENT_KIND_FOR_INTENT[intent],
                "webui_client_id": webui_id,
                "public_alias": alias,
                "require_tailnet": bool(require_tailnet),
                "baseline_client_ids": list(baseline["client_ids"]),
                "baseline_access_enabled": bool(baseline["access_enabled"]),
                "baseline_model_active": bool(baseline["model_active"]),
                "baseline_gateway_consumers": list(baseline["gateway_consumers"]),
                "baseline_openwebui_running": bool(baseline["openwebui_running"]),
                "baseline_sharing_enabled": bool(baseline["sharing_enabled"]),
                "requested_by": requested_by,
            },
            surface=requested_by,
        )
        execution = self.engine_factory().execute_one(record.id)
        if execution.kind == "SKIPPED_BUSY":
            return IntegrationSetupOutcome(
                record.id, "BUSY", selected_id,
                detail={"reason_code": "INTEGRATION_RESOURCE_BUSY"})
        with self.units.read() as conn:
            final = OperationRepository(conn).require(record.id)
        if final.state is not OperationState.SUCCEEDED:
            return IntegrationSetupOutcome(
                record.id,
                {
                    OperationState.FAILED_SAFE: "FAILED_SAFE",
                    OperationState.FAILED_ROLLED_BACK: "FAILED_ROLLED_BACK",
                    OperationState.RECOVERY_REQUIRED: "RECOVERY_REQUIRED",
                    OperationState.CANCELLED: "CANCELLED",
                }.get(final.state, "BUSY"),
                selected_id,
                detail={
                    "reason_code": final.error_code or final.result_code,
                    "data_changed": final.state is not OperationState.FAILED_SAFE,
                },
            )
        snapshot = self.app.connections.snapshot(client_id=selected_id).to_dict()
        guide_card = {
            "OPENWEBUI": "openwebui",
            "PHONE_TABLET": "pocketpal",
            "DESKTOP_APP": "openai",
            "DEVELOPER": "curl",
        }[intent]
        guide = instructions_for(
            guide_card, urls=snapshot.get("urls") or {}, public_alias=alias)
        new_external = (
            intent != "OPENWEBUI" and selected_id not in baseline["client_ids"])
        revealed = (
            self.app.connection_credentials.secret_for_probe(selected_id)
            if new_external else None
        )
        return IntegrationSetupOutcome(
            record.id,
            "READY",
            selected_id,
            secret=revealed,
            detail={
                "intent": intent,
                "base_url": (guide.get("values") or {}).get("Base URL"),
                "model": alias,
                "streaming": "enabled",
                "timeout_seconds": int(guide["card"]["timeout_seconds"]),
                "legacy_revoke_recommended": self.legacy_status()[
                    "revoke_recommended"],
            },
        )

    def legacy_status(self) -> dict[str, Any]:
        clients = self._active_clients()
        legacy = next(
            (item for item in clients if item["client_id"] == "legacy-install"), None)
        webui = any(item.get("client_kind") == "openwebui" for item in clients)
        external = [
            item for item in clients
            if item["client_id"] != "legacy-install"
            and item.get("client_kind") != "openwebui"
        ]
        verified_external = False
        if external:
            with self.units.read() as conn:
                for item in external:
                    row = conn.execute(
                        "SELECT payload_json FROM runtime_observations "
                        "WHERE key = ? AND stale = 0 LIMIT 1",
                        (f"{PROBE_OBSERVATION_PREFIX}{item['client_id']}",),
                    ).fetchone()
                    try:
                        payload = json.loads(row["payload_json"]) if row else {}
                    except (TypeError, ValueError):
                        payload = {}
                    if payload.get("passed") is True:
                        verified_external = True
                        break
        try:
            state = self.app.read_model()
            openwebui = self.app.openwebui.status(state, self.app.runner())
            webui_verified = bool(
                openwebui.get("provider_ready")
                and openwebui.get("expected_model_visible")
                and openwebui.get("end_to_end_verified"))
        except Exception:
            webui_verified = False
        return {
            "legacy_present": legacy is not None,
            "legacy_client_id": "legacy-install" if legacy else None,
            "openwebui_replacement_ready": bool(webui and webui_verified),
            "external_replacement_ready": verified_external,
            "revoke_recommended": bool(
                legacy and webui and webui_verified and verified_external),
        }

    def retire_legacy(self, *, confirmation: str) -> dict[str, Any]:
        status = self.legacy_status()
        if confirmation != "REVOKE LEGACY":
            raise ValueError("type REVOKE LEGACY to retire the shared key")
        if not status["legacy_present"]:
            return {"retired": False, "reason_code": "LEGACY_ALREADY_ABSENT"}
        if not status["revoke_recommended"]:
            raise RuntimeError(
                "Verify separate Open WebUI and external-app clients before retiring the legacy key."
            )
        record = self.app.connection_credentials.client("legacy-install")
        if record is None:
            return {"retired": False, "reason_code": "LEGACY_ALREADY_ABSENT"}
        self.app.connection_credentials.revoke_client(
            "legacy-install", expected_revision=int(record["revision"]))
        return {"retired": True, "reason_code": "LEGACY_RETIRED"}


__all__ = ["IntegrationSetupCommandService", "IntegrationSetupOutcome"]
