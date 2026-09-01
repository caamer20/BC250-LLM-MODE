import json

import pytest

from bc250_llm_mode.connection_setup import ConnectionCredentialCommandService
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.integration_setup import (
    INTEGRATION_RESOURCES,
    OPERATION_TYPE,
    build_integration_setup_workflow,
    decode_integration_setup_request,
)
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.workflow import (
    EnqueueService,
    ProbeResult,
    StepFailure,
    WorkflowRegistry,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


NOW = "2026-09-01T02:00:00Z"


def _payload(**updates):
    value = {
        "intent": "PHONE_TABLET",
        "client_id": "1" * 32,
        "client_label": "My phone",
        "client_kind": "pocketpal",
        "webui_client_id": "2" * 32,
        "public_alias": "qwen38-9b",
        "require_tailnet": True,
        "baseline_client_ids": [],
        "baseline_access_enabled": True,
        "baseline_model_active": False,
        "baseline_gateway_consumers": [],
        "baseline_openwebui_running": False,
        "baseline_sharing_enabled": False,
        "requested_by": "gui",
    }
    value.update(updates)
    return value


def test_request_is_closed_bounded_and_contains_no_secret_material():
    request = decode_integration_setup_request(_payload())
    assert request.public_alias == "qwen38-9b"
    assert request.baseline_client_ids == ()
    with pytest.raises(Exception, match="unknown integration fields"):
        decode_integration_setup_request({**_payload(), "api_key": "canary"})
    with pytest.raises(Exception, match="consumer baseline"):
        decode_integration_setup_request(_payload(
            baseline_gateway_consumers=["wildcard-listener"]))
    with pytest.raises(Exception, match="does not match"):
        decode_integration_setup_request(_payload(client_kind="curl"))
    with pytest.raises(Exception, match="separate identities"):
        decode_integration_setup_request(_payload(
            webui_client_id="1" * 32))
    assert "secret" not in json.dumps(request.__dict__).lower()


def test_workflow_has_exact_dependency_order_and_shared_exclusion_resources():
    workflow = build_integration_setup_workflow(FakeHost())
    assert [step.step_key for step in workflow.steps] == [
        "ensure_clients", "start_model", "start_gateway",
        "start_openwebui", "publish_tailnet", "verify_client",
    ]
    assert workflow.all_resources() == tuple(sorted(INTEGRATION_RESOURCES))
    assert all("credential" not in step.step_key for step in workflow.steps)


def test_ensure_client_is_idempotent_and_adopts_operation_orphan(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    secrets = iter(("first-operation-secret", "must-not-be-used"))
    service = ConnectionCredentialCommandService(
        units, tmp_path, clock=lambda: NOW,
        secret_provider=lambda: next(secrets))
    client_id = "a" * 32
    first = service.ensure_client(
        client_id=client_id, label="Phone", client_kind="pocketpal")
    second = service.ensure_client(
        client_id=client_id, label="Phone", client_kind="pocketpal")
    assert first.action == "created" and first.secret == "first-operation-secret"
    assert second.action == "reused" and second.secret is None
    assert service.secret_for_probe(client_id) == "first-operation-secret"

    orphan_id = "b" * 32
    orphan = tmp_path / "connection-secrets" / f"{orphan_id}-1.secret"
    orphan.write_text("recovered-operation-secret", encoding="utf-8")
    orphan.chmod(0o600)
    adopted = service.ensure_client(
        client_id=orphan_id, label="Desktop", client_kind="openai")
    assert adopted.action == "created" and adopted.secret is None
    assert service.secret_for_probe(orphan_id) == "recovered-operation-secret"


class FakeHost:
    def __init__(self, *, fail_verification=False):
        self.clients = False
        self.model = False
        self.gateway = False
        self.webui = False
        self.tailnet = False
        self.verified = False
        self.fail_verification = fail_verification

    @staticmethod
    def complete(code, output=None):
        return ProbeResult(RecoveryClass.COMPLETE, code, output or {})

    @staticmethod
    def absent(code):
        return ProbeResult(RecoveryClass.ABSENT, code)

    def ensure_clients(self, _ctx):
        self.clients = True
        return {"client_id": "1" * 32, "webui_client_id": "2" * 32,
                "created_client_ids": ["1" * 32, "2" * 32],
                "access_enabled": True}

    def probe_clients(self, _ctx):
        return self.complete("CLIENTS_READY", {
            "client_id": "1" * 32, "webui_client_id": "2" * 32,
            "created_client_ids": ["1" * 32, "2" * 32],
            "access_enabled": True}) if self.clients else self.absent("NO_CLIENTS")

    def remove_created_clients(self, _ctx):
        self.clients = False
        return {"removed_client_ids": ["1" * 32, "2" * 32]}

    def probe_clients_restored(self, _ctx):
        return self.complete("CLIENT_BASELINE_RESTORED") if not self.clients else self.absent("CLIENTS_ACTIVE")

    def start_model(self, _ctx):
        self.model = True
        return {"public_alias": "qwen38-9b", "started": True}

    def probe_model(self, _ctx):
        return self.complete("MODEL_READY", {"public_alias": "qwen38-9b"}) if self.model else self.absent("MODEL_STOPPED")

    def restore_model(self, _ctx):
        self.model = False
        return {"stopped": True}

    def probe_model_restored(self, _ctx):
        return self.complete("MODEL_BASELINE_RESTORED") if not self.model else self.absent("MODEL_ACTIVE")

    def start_gateway(self, _ctx):
        self.gateway = True
        return {"consumer": "client", "active": True}

    def probe_gateway(self, _ctx):
        return self.complete("GATEWAY_READY", {"active": True}) if self.gateway else self.absent("GATEWAY_STOPPED")

    def restore_gateway(self, _ctx):
        self.gateway = False
        return {"released": True}

    def probe_gateway_restored(self, _ctx):
        return self.complete("GATEWAY_BASELINE_RESTORED") if not self.gateway else self.absent("GATEWAY_ACTIVE")

    def start_openwebui(self, _ctx):
        self.webui = True
        return {"running": True, "transaction_id": "0123456789ab"}

    def probe_openwebui(self, _ctx):
        return self.complete("OPENWEBUI_READY", {"running": True}) if self.webui else self.absent("WEBUI_STOPPED")

    def restore_openwebui(self, _ctx):
        self.webui = False
        return {"stopped": True}

    def probe_openwebui_restored(self, _ctx):
        return self.complete("OPENWEBUI_BASELINE_RESTORED") if not self.webui else self.absent("WEBUI_ACTIVE")

    def publish_tailnet(self, _ctx):
        self.tailnet = True
        return {"required": True, "enabled": True, "funnel_disabled": True}

    def probe_tailnet(self, _ctx):
        return self.complete("TAILNET_READY", {"required": True, "enabled": True}) if self.tailnet else self.absent("TAILNET_OFF")

    def restore_tailnet(self, _ctx):
        self.tailnet = False
        return {"stopped": True}

    def probe_tailnet_restored(self, _ctx):
        return self.complete("TAILNET_BASELINE_RESTORED") if not self.tailnet else self.absent("TAILNET_ACTIVE")

    def verify_client(self, _ctx):
        if self.fail_verification:
            raise StepFailure("CLIENT_TEST_FAILED", "fixed failure")
        self.verified = True
        return {"client_id": "1" * 32, "local_ready": True,
                "tailnet_ready": True, "observed_at": NOW}

    def probe_client_verification(self, _ctx):
        return self.complete("CLIENT_READY", {
            "client_id": "1" * 32, "local_ready": True,
            "tailnet_ready": True, "observed_at": NOW,
        }) if self.verified else self.absent("NOT_VERIFIED")


def _run(tmp_path, host):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    registry = WorkflowRegistry()
    registry.register(build_integration_setup_workflow(host))
    registry.freeze()
    ids = iter(("operation-1", *(f"effect-{index}" for index in range(20))))
    enqueue = EnqueueService(
        units, registry, clock=lambda: NOW, uuid_factory=lambda: next(ids))
    record = enqueue.enqueue(
        operation_type=OPERATION_TYPE, payload=_payload(), surface="gui")
    engine = ExecutionEngine(
        units, registry, clock=lambda: NOW, uuid_factory=lambda: next(ids),
        worker_id="worker-integration", lease_ttl_seconds=60)
    outcome = engine.execute_one(record.id)
    with units.read() as conn:
        final = OperationRepository(conn).require(record.id)
    return outcome, final


def test_durable_integration_success_records_only_redacted_readiness(tmp_path):
    host = FakeHost()
    outcome, final = _run(tmp_path, host)
    assert outcome.reason_code == "INTEGRATION_READY"
    assert final.state is OperationState.SUCCEEDED
    assert final.result_code == "INTEGRATION_READY"
    detail = json.loads(final.result_detail)
    assert detail["client_id"] == "1" * 32
    assert detail["local_ready"] and detail["tailnet_ready"]
    assert all((host.clients, host.model, host.gateway, host.webui, host.tailnet))
    assert "secret" not in (final.request_json + final.result_detail).lower()


def test_failed_client_test_compensates_only_new_effects_in_reverse(tmp_path):
    host = FakeHost(fail_verification=True)
    outcome, final = _run(tmp_path, host)
    assert outcome.reason_code == "FAILED_ROLLED_BACK"
    assert final.state is OperationState.FAILED_ROLLED_BACK
    assert not any((host.clients, host.model, host.gateway, host.webui, host.tailnet))
