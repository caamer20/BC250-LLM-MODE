"""Production host port for durable guided connection setup."""

from __future__ import annotations

import threading
from typing import Any

from .operations.integration_setup import IntegrationSetupHost
from .operations.recovery import RecoveryClass
from .operations.workflow import EffectContext, ProbeResult, StepFailure


class IntegrationSetupHostAdapter(IntegrationSetupHost):
    """Resolve composed services lazily; composition performs no effects."""

    def __init__(self, application: Any) -> None:
        self.app = application

    @staticmethod
    def _complete(code: str, output: dict[str, Any] | None = None) -> ProbeResult:
        return ProbeResult(RecoveryClass.COMPLETE, code, output or {})

    @staticmethod
    def _absent(code: str) -> ProbeResult:
        return ProbeResult(RecoveryClass.ABSENT, code)

    def _snapshot(self, client_id: str | None = None) -> dict[str, Any]:
        return self.app.connections.snapshot(client_id=client_id).to_dict()

    def _runner_view(self):
        return self.app.runner(), self.app.read_model()

    @staticmethod
    def _run_with_lease_pulses(ctx: EffectContext, action):
        """Keep durable leases current during one bounded blocking host call."""
        stopped = threading.Event()

        def pulse_until_stopped() -> None:
            while not stopped.wait(10.0):
                ctx.pulse()

        heartbeat = threading.Thread(
            target=pulse_until_stopped,
            name="integration-setup-lease-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        try:
            return action()
        finally:
            stopped.set()
            heartbeat.join(timeout=1.0)

    def _client_ids(self, ctx: EffectContext) -> tuple[str, ...]:
        request = ctx.request
        if request.client_id == request.webui_client_id:
            return (request.client_id,)
        return (request.webui_client_id, request.client_id)

    def ensure_clients(self, ctx: EffectContext) -> dict[str, Any]:
        request = ctx.request
        ctx.pulse(phase="client", current=0, summary="Creating private client access")
        created: list[str] = []
        webui = self.app.connection_credentials.ensure_client(
            client_id=request.webui_client_id,
            label="Open WebUI",
            client_kind="openwebui",
        )
        if webui.action == "created":
            created.append(request.webui_client_id)
        if request.client_id != request.webui_client_id:
            client = self.app.connection_credentials.ensure_client(
                client_id=request.client_id,
                label=request.client_label,
                client_kind=request.client_kind,
            )
            if client.action == "created":
                created.append(request.client_id)
        access = self.app.connection_credentials.access_state()
        if not access["enabled"]:
            self.app.connection_credentials.enable_for_sharing(
                expected_revision=int(access["revision"]))
        return {
            "client_id": request.client_id,
            "webui_client_id": request.webui_client_id,
            "created_client_ids": sorted(created),
            "access_enabled": True,
        }

    def probe_clients(self, ctx: EffectContext) -> ProbeResult:
        request = ctx.request
        try:
            for client_id in self._client_ids(ctx):
                self.app.connection_credentials.secret_for_probe(client_id)
            access = self.app.connection_credentials.access_state()
        except Exception:
            return self._absent("CLIENT_FILES_OR_METADATA_MISSING")
        if not access.get("enabled"):
            return self._absent("CLIENT_ACCESS_DISABLED")
        baseline = set(request.baseline_client_ids)
        return self._complete("CLIENTS_READY", {
            "client_id": request.client_id,
            "webui_client_id": request.webui_client_id,
            "created_client_ids": sorted(
                client_id for client_id in self._client_ids(ctx)
                if client_id not in baseline),
            "access_enabled": True,
        })

    def remove_created_clients(self, ctx: EffectContext) -> dict[str, Any]:
        output = ctx.prior_outputs.get("ensure_clients") or {}
        removed: list[str] = []
        for client_id in reversed(output.get("created_client_ids") or []):
            record = self.app.connection_credentials.client(str(client_id))
            if record is None or record.get("revoked_at"):
                continue
            self.app.connection_credentials.revoke_client(
                str(client_id), expected_revision=int(record["revision"]))
            removed.append(str(client_id))
        if not ctx.request.baseline_access_enabled:
            access = self.app.connection_credentials.access_state()
            if access["enabled"]:
                self.app.connection_credentials.disable_all(
                    expected_revision=int(access["revision"]))
        return {"removed_client_ids": sorted(removed)}

    def probe_clients_restored(self, ctx: EffectContext) -> ProbeResult:
        output = ctx.prior_outputs.get("ensure_clients") or {}
        for client_id in output.get("created_client_ids") or []:
            record = self.app.connection_credentials.client(str(client_id))
            if record is not None and not record.get("revoked_at"):
                return self._absent("CREATED_CLIENT_REMAINS_ACTIVE")
        access = self.app.connection_credentials.access_state()
        if bool(access["enabled"]) != bool(ctx.request.baseline_access_enabled):
            return self._absent("CLIENT_ACCESS_BASELINE_NOT_RESTORED")
        return self._complete("CLIENT_BASELINE_RESTORED")

    def start_model(self, ctx: EffectContext) -> dict[str, Any]:
        ctx.pulse(phase="model", current=1, summary="Starting the selected model")
        runner, view = self._runner_view()
        status = self.app.model_server.status(view, runner)
        if not status.get("active"):
            self.app.model_server.start(view, runner)
        return {"public_alias": ctx.request.public_alias, "started": not status.get("active")}

    def probe_model(self, ctx: EffectContext) -> ProbeResult:
        model = self._snapshot().get("model") or {}
        if model.get("healthy") and model.get("public_alias") == ctx.request.public_alias:
            return self._complete("MODEL_READY", {"public_alias": ctx.request.public_alias})
        return self._absent("MODEL_NOT_READY")

    def restore_model(self, ctx: EffectContext) -> dict[str, Any]:
        if not ctx.request.baseline_model_active:
            runner, view = self._runner_view()
            self.app.model_server.stop(view, runner)
            return {"stopped": True}
        return {"stopped": False}

    def probe_model_restored(self, ctx: EffectContext) -> ProbeResult:
        runner, view = self._runner_view()
        active = bool(self.app.model_server.status(view, runner).get("active"))
        if active == bool(ctx.request.baseline_model_active):
            return self._complete("MODEL_BASELINE_RESTORED")
        return self._absent("MODEL_BASELINE_NOT_RESTORED")

    def start_gateway(self, ctx: EffectContext) -> dict[str, Any]:
        ctx.pulse(phase="gateway", current=2, summary="Starting private API access")
        runner = self.app.runner()
        self.app.gateway_service.acquire("client", runner)
        return {"consumer": "client", "active": True}

    def probe_gateway(self, ctx: EffectContext) -> ProbeResult:
        gateway = self._snapshot().get("gateway") or {}
        if gateway.get("healthy"):
            return self._complete("GATEWAY_READY", {"active": True})
        return self._absent("GATEWAY_NOT_READY")

    def restore_gateway(self, ctx: EffectContext) -> dict[str, Any]:
        if "client" not in ctx.request.baseline_gateway_consumers:
            runner = self.app.runner()
            status = self.app.gateway_service.status(runner)
            if "client" in status.get("current_boot_consumers", ()):
                self.app.gateway_service.release("client", runner)
            return {"released": True}
        return {"released": False}

    def probe_gateway_restored(self, ctx: EffectContext) -> ProbeResult:
        status = self.app.gateway_service.status(self.app.runner())
        consumers = set(status.get("current_boot_consumers", ()))
        if "client" in consumers and "client" not in ctx.request.baseline_gateway_consumers:
            return self._absent("GATEWAY_CONSUMER_NOT_RESTORED")
        return self._complete("GATEWAY_BASELINE_RESTORED")

    def start_openwebui(self, ctx: EffectContext) -> dict[str, Any]:
        ctx.pulse(phase="openwebui", current=3, summary="Starting Open WebUI")
        runner, view = self._runner_view()
        result = self._run_with_lease_pulses(
            ctx, lambda: self.app.openwebui.start(view, runner))
        if not (
            result.get("running") and result.get("provider_ready")
            and result.get("expected_model_visible") and result.get("stream_ready")
        ):
            raise StepFailure(
                "OPENWEBUI_NOT_READY", "Open WebUI verification did not finish",
                mutation_possible=True)
        return {"running": True, "transaction_id": result.get("transaction_id")}

    def probe_openwebui(self, ctx: EffectContext) -> ProbeResult:
        webui = self._snapshot().get("openwebui") or {}
        if all(webui.get(key) for key in (
            "running", "http_ready", "provider_configured",
            "expected_model_visible", "end_to_end_verified",
        )):
            return self._complete("OPENWEBUI_READY", {"running": True})
        return self._absent("OPENWEBUI_NOT_READY")

    def restore_openwebui(self, ctx: EffectContext) -> dict[str, Any]:
        if not ctx.request.baseline_openwebui_running:
            runner, view = self._runner_view()
            self.app.openwebui.stop(view, runner)
            return {"stopped": True}
        return {"stopped": False}

    def probe_openwebui_restored(self, ctx: EffectContext) -> ProbeResult:
        runner, view = self._runner_view()
        status = self.app.openwebui.status(view, runner)
        if bool(status.get("running")) == bool(ctx.request.baseline_openwebui_running):
            return self._complete("OPENWEBUI_BASELINE_RESTORED")
        return self._absent("OPENWEBUI_BASELINE_NOT_RESTORED")

    def publish_tailnet(self, ctx: EffectContext) -> dict[str, Any]:
        if not ctx.request.require_tailnet:
            return {"required": False, "enabled": False}
        ctx.pulse(phase="tailnet", current=4, summary="Publishing private HTTPS access")
        runner, view = self._runner_view()
        result = self.app.sharing.start_verified_backends(view, runner)
        return {
            "required": True,
            "enabled": bool(result.get("enabled")),
            "funnel_disabled": result.get("public_funnel") is False,
        }

    def probe_tailnet(self, ctx: EffectContext) -> ProbeResult:
        if not ctx.request.require_tailnet:
            return self._complete("TAILNET_NOT_REQUESTED", {"required": False})
        snapshot = self._snapshot()
        tail = snapshot.get("tailscale") or {}
        sharing = snapshot.get("sharing") or {}
        ready = (
            tail.get("connected")
            and sharing.get("webui_mapping_exact")
            and sharing.get("api_mapping_exact")
            and sharing.get("public_funnel") is False
        )
        if ready:
            return self._complete("TAILNET_READY", {"required": True, "enabled": True})
        return self._absent("TAILNET_NOT_READY")

    def restore_tailnet(self, ctx: EffectContext) -> dict[str, Any]:
        if ctx.request.require_tailnet and not ctx.request.baseline_sharing_enabled:
            runner, view = self._runner_view()
            self.app.sharing.stop(view, runner)
            return {"stopped": True}
        return {"stopped": False}

    def probe_tailnet_restored(self, ctx: EffectContext) -> ProbeResult:
        runner, view = self._runner_view()
        status = self.app.sharing.status(view, runner)
        if bool(status.get("enabled")) == bool(ctx.request.baseline_sharing_enabled):
            return self._complete("TAILNET_BASELINE_RESTORED")
        return self._absent("TAILNET_BASELINE_NOT_RESTORED")

    def verify_client(self, ctx: EffectContext) -> dict[str, Any]:
        ctx.pulse(phase="verify", current=5, summary="Testing authorized and blocked access")
        snapshot = self._snapshot(ctx.request.client_id)
        base_url = (snapshot.get("urls") or {}).get("base_url")
        report = self.app.connection_probes.run(
            client_id=ctx.request.client_id,
            public_alias=ctx.request.public_alias,
            tailnet_base_url=base_url if ctx.request.require_tailnet else None,
        )
        if not report.passed:
            raise StepFailure(
                "CLIENT_TEST_FAILED", "The guided client checks did not all pass")
        return {
            "client_id": ctx.request.client_id,
            "local_ready": True,
            "tailnet_ready": bool(ctx.request.require_tailnet),
            "observed_at": report.observed_at,
        }

    def probe_client_verification(self, ctx: EffectContext) -> ProbeResult:
        probes = (self._snapshot(ctx.request.client_id).get("probes") or {})
        local = all(probes.get(key) == "passed" for key in (
            "local_unauthorized", "local_authorized"))
        tailnet = all(probes.get(key) == "passed" for key in (
            "tailnet_unauthorized", "tailnet_authorized_models", "tailnet_stream"))
        if local and (tailnet or not ctx.request.require_tailnet):
            return self._complete("CLIENT_READY", {
                "client_id": ctx.request.client_id,
                "local_ready": True,
                "tailnet_ready": bool(ctx.request.require_tailnet),
                "observed_at": probes.get("observed_at"),
            })
        return self._absent("CLIENT_VERIFICATION_MISSING")


__all__ = ["IntegrationSetupHostAdapter"]
