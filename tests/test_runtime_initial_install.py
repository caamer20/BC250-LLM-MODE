"""No-model installation must not claim live runtime verification."""

from dataclasses import replace

from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.workflow import ProbeResult
from test_runtime_workflow import Harness
from test_runtime_adapter_real import real_adapter

import pytest
from bc250_llm_mode.operations.runtime_lifecycle import RuntimeUpdateRequestV1
from bc250_llm_mode.operations.workflow import StepFailure
from bc250_llm_mode.runtime_handoff import RuntimeHandoffRenderer
from test_runtime_cutover_real import ServiceFixture, seed_tree


def test_no_model_installation_keeps_service_handoff_and_promotion_absent(tmp_path, monkeypatch):
    world = Harness(tmp_path)
    capture = world.host.capture_activation_boundary
    monkeypatch.setattr(world.host, "capture_activation_boundary", lambda *args: replace(
        capture(*args), installation_only=True, installation_fingerprint="fresh-config"))

    def installed(snapshot, target):
        assert snapshot.installation_only
        assert world.host.active_build_id() == target
        assert not world.host.service().get("running")
        assert not world.host.handoff_path.exists()
        assert not (world._component() or {}).get("promoted_build_id")
        return ProbeResult(RecoveryClass.COMPLETE, "INITIAL_RUNTIME_INSTALLED",
                           output={"installation_verified": True, "inference_deferred": True})

    monkeypatch.setattr(world.host, "observe_initial_installation", installed, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("No model exists for service start or promotion")

    for method in ("publish_handoff_v2", "restart_for_runtime_change", "verify_runtime_identity",
                   "verify_runtime_inference", "promote_verified_runtime", "observe_promotion"):
        monkeypatch.setattr(world.host, method, forbidden)
    world.enqueue_update()
    world.run_to_terminal()
    assert world.state() is OperationState.SUCCEEDED
    assert world.row().result_code == "RUNTIME_INSTALLED"
    assert not (world._component() or {}).get("promoted_build_id")
    assert not world.host.service()["running"]


def test_installation_observation_failure_cannot_report_success(tmp_path, monkeypatch):
    world = Harness(tmp_path)
    capture = world.host.capture_activation_boundary
    monkeypatch.setattr(world.host, "capture_activation_boundary", lambda *args: replace(
        capture(*args), installation_only=True, installation_fingerprint="fresh-config"))
    monkeypatch.setattr(world.host, "observe_initial_installation", lambda *args: ProbeResult(
        RecoveryClass.REVERTIBLE, "INITIAL_INSTALLATION_CHANGED"), raising=False)
    world.enqueue_update()
    world.run_to_terminal()
    assert world.state() is OperationState.FAILED_ROLLED_BACK
    assert world.host.active_build_id() is None
    assert not (world._component() or {}).get("promoted_build_id")


def test_production_capture_fences_no_model_configuration(real_adapter, tmp_path):
    adapter, _, _ = real_adapter
    view = {"current_ctx": 8192, "optimizations": {"parallel_slots": 1}}
    adapter.state_supplier = lambda: dict(view)
    adapter.renderer = RuntimeHandoffRenderer(tmp_path / "profile")
    adapter.server_port = ServiceFixture(adapter.renderer)
    adapter.server_port.running = False
    target = seed_tree(adapter, tmp_path / "target", "initial")
    request = RuntimeUpdateRequestV1()
    snapshot = adapter.capture_activation_boundary(request, target.build_id)
    assert snapshot.installation_only and snapshot.installation_fingerprint
    adapter.verify_activation_boundary(request, snapshot, target.build_id)
    view["current_model"] = "new-selection"
    with pytest.raises(StepFailure, match="INITIAL_INSTALLATION_UNPROVEN"):
        adapter.verify_activation_boundary(request, snapshot, target.build_id)


def test_production_capture_refuses_unknown_stopped_observation(real_adapter, tmp_path, monkeypatch):
    adapter, _, _ = real_adapter
    adapter.state_supplier = lambda: {"current_ctx": 8192}
    adapter.renderer = RuntimeHandoffRenderer(tmp_path / "profile")
    adapter.server_port = ServiceFixture(adapter.renderer)
    adapter.server_port.running = False
    monkeypatch.setattr(adapter.server_port, "capture", lambda view: {})
    target = seed_tree(adapter, tmp_path / "target", "initial")
    with pytest.raises(StepFailure, match="INITIAL_INSTALLATION_UNPROVEN"):
        adapter.capture_activation_boundary(RuntimeUpdateRequestV1(), target.build_id)


def test_missing_model_cannot_discard_existing_known_good_state(real_adapter, tmp_path):
    from bc250_llm_mode.repositories import KnownGoodRuntimeRepository

    adapter, _, _ = real_adapter
    adapter.state_supplier = lambda: {"current_ctx": 8192}
    adapter.renderer = RuntimeHandoffRenderer(tmp_path / "profile")
    adapter.server_port = ServiceFixture(adapter.renderer)
    adapter.server_port.running = False
    with adapter._units.begin() as conn:
        KnownGoodRuntimeRepository(conn).set(model_alias="previous", context=4096, slots=1,
                                             runtime={}, verified_at="2026-09-17T00:00:00Z")
        prior = KnownGoodRuntimeRepository(conn).get()
    target = seed_tree(adapter, tmp_path / "target", "initial")
    with pytest.raises(StepFailure, match="INITIAL_INSTALLATION_UNPROVEN"):
        adapter.capture_activation_boundary(RuntimeUpdateRequestV1(), target.build_id)
    with adapter._units.read() as conn:
        assert KnownGoodRuntimeRepository(conn).get() == prior


def test_initial_boundary_rechecks_thermal_stop(real_adapter, tmp_path, monkeypatch):
    adapter, _, _ = real_adapter
    adapter.state_supplier = lambda: {"current_ctx": 8192}
    adapter.renderer = RuntimeHandoffRenderer(tmp_path / "profile")
    adapter.server_port = ServiceFixture(adapter.renderer)
    adapter.server_port.running = False
    target = seed_tree(adapter, tmp_path / "target", "initial")
    request = RuntimeUpdateRequestV1()
    snapshot = adapter.capture_activation_boundary(request, target.build_id)
    monkeypatch.setattr(adapter, "_thermal_ok", lambda: False)
    with pytest.raises(StepFailure, match="THERMAL_LATCH_STOPPED"):
        adapter.verify_activation_boundary(request, snapshot, target.build_id)
