"""A5 + A4: typed runtime configuration (preview/apply) and the model
activation service with verified rollback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from _native import NativeApp
from bc250_llm_mode.runtime_handoff import HANDOFF_FILENAME
from bc250_llm_mode.services import (
    ActivationRequest,
    ModelActivationService,
    RuntimeConfigurationService,
    RuntimeValidationError,
    RevisionConflict,
    ThermalStateService,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

MODEL = {
    "id": "lfm25-26b",
    "path": "/models/lfm25.gguf",
    "quant": "Q5_K_M",
    "display_name": "LFM2.5 2.6B",
}


def _install(store, models, current=None):
    """Seed installations/current model through typed repositories."""
    from bc250_llm_mode.repositories import ModelInstallationsRepository

    with store.units.begin() as conn:
        ModelInstallationsRepository(conn).replace_all(
            [dict(m) for m in models]
        )
    if current is not None:
        store.set_settings({"current_model": current})


def _runtime(tmp_path):
    store = NativeApp(tmp_path)
    service = RuntimeConfigurationService(
        UnitOfWorkFactory(store.paths.database_path),
        app_dir=store.paths.app_dir,
        state_supplier=store.load,
    )
    return store, service


def _seed_model(store, runtime):
    from bc250_llm_mode.optimize import normalized_settings, validate_settings

    _install(store, [MODEL])
    # Mirror the facade-era baseline: one slot and fully normalized settings.
    store.set_settings({
        "current_model": MODEL["id"],
        "optimizations": validate_settings(normalized_settings({"parallel_slots": 1})),
    })
    return runtime.current()


# --- A5 --------------------------------------------------------------------


def test_preview_performs_no_writes(tmp_path):
    store, runtime = _runtime(tmp_path)
    _seed_model(store, runtime)
    (store.paths.app_dir / HANDOFF_FILENAME).unlink(missing_ok=True)
    before = runtime.capture()
    seed_before = store.revision()

    preview = runtime.preview({"context": 4096})

    assert preview["context_per_slot"] == 4096
    assert preview["total_context"] == 4096
    assert runtime.capture() == before
    assert store.revision() == seed_before
    assert not (store.paths.app_dir / HANDOFF_FILENAME).exists()


def test_preview_and_apply_resolve_identical_settings(tmp_path):
    store, runtime = _runtime(tmp_path)
    rev = _seed_model(store, runtime)["revision"]
    desired = {"context": 8192, "slots": 2}

    preview = runtime.preview(desired)
    result = runtime.apply(desired, expected_revision=rev)

    assert result.status == "committed"
    after = runtime.preview({})
    assert after["resolved_optimizations"] == preview["resolved_optimizations"]
    assert after["slots"] == 2


def test_fit_gate_rejects_before_mutation(tmp_path):
    store, runtime = _runtime(tmp_path)
    _install(
        store,
        [{"id": "qwen25-coder-14b", "path": "/models/coder14b.gguf", "quant": "Q4_K_M"}],
        current="qwen25-coder-14b",
    )
    rev = runtime.current()["revision"]

    # 8192 x 2 slots on coder-14b Q4_K_M is a documented NO-FIT.
    with pytest.raises(RuntimeValidationError, match="fit rejected"):
        runtime.preview({"context": 8192, "slots": 2})
    with pytest.raises(RuntimeValidationError, match="fit rejected"):
        runtime.apply({"context": 8192, "slots": 2}, expected_revision=rev)

    assert runtime.current()["revision"] == rev  # zero durable effects
    assert runtime.capture()["revision"] == rev  # no handoff-triggering write


def test_stale_revision_fails_before_handoff_or_restart(tmp_path):
    store, runtime = _runtime(tmp_path)
    rev = _seed_model(store, runtime)["revision"]
    other = RuntimeConfigurationService(
        UnitOfWorkFactory(store.paths.database_path),
        app_dir=store.paths.app_dir,
        state_supplier=store.load,
    )
    other.apply({"context": 4096}, expected_revision=rev)

    with pytest.raises(RevisionConflict):
        runtime.apply({"context": 32768}, expected_revision=rev)  # stale
    handoff = store.paths.app_dir / HANDOFF_FILENAME
    if handoff.exists():
        payload = json.loads(handoff.read_text(encoding="utf-8"))
        assert payload["config_revision"] != rev


def test_invalid_values_fail_before_mutation(tmp_path):
    store, runtime = _runtime(tmp_path)
    rev = _seed_model(store, runtime)["revision"]

    for bad in (
        {"optimizations_patch": {"threads": 99999}},
        {"model_alias": "not-installed"},
    ):
        with pytest.raises(RuntimeValidationError):
            runtime.apply(bad, expected_revision=rev)
    assert runtime.current()["revision"] == rev


def test_handoff_revision_matches_committed_revision(tmp_path):
    store, runtime = _runtime(tmp_path)
    rev = _seed_model(store, runtime)["revision"]

    result = runtime.apply({"context": 8192, "slots": 2}, expected_revision=rev)

    payload = json.loads(
        (store.paths.app_dir / HANDOFF_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["config_revision"] == result.revision == rev + 1


def test_capture_and_restore_round_trip(tmp_path):
    store, runtime = _runtime(tmp_path)
    _seed_model(store, runtime)
    original = runtime.capture()

    runtime.apply({"context": 32768}, expected_revision=original["revision"])
    changed = runtime.current()
    assert changed["context"] == 32768

    restored = runtime.restore(original, expected_revision=changed["revision"])
    assert restored.status == "committed"
    now = runtime.current()
    assert now["context"] == original["context"]
    assert now["optimizations"] == original["optimizations"]


def test_known_good_promotion_round_trip(tmp_path):
    store, runtime = _runtime(tmp_path)
    _seed_model(store, runtime)
    assert runtime.known_good() is None

    runtime.promote_known_good(component_identity="b6000-abc")
    good = runtime.known_good()
    assert good["model_alias"] == MODEL["id"]
    assert good["runtime_component_identity"] == "b6000-abc"
    assert good["verified_at"]


# --- A4 --------------------------------------------------------------------


@dataclass
class FakeController:
    fail_health_model: str | None = None
    fail_restart: bool = False
    fail_probe_model: str | None = None  # probe fails only for this model
    calls: list = field(default_factory=list)

    def restart(self, view):
        self.calls.append(("restart", view.get("current_model")))
        if self.fail_restart:
            raise RuntimeError("systemctl failed")

    def health_check(self, view):
        self.calls.append(("health", view.get("current_model")))
        if self.fail_health_model == view.get("current_model"):
            raise TimeoutError("new model did not become healthy")

    def minimal_inference_probe(self, view):
        self.calls.append(("probe", view.get("current_model")))
        if self.fail_probe_model == view.get("current_model"):
            raise RuntimeError("inference probe produced no tokens")


def _activation(tmp_path):
    store = NativeApp(tmp_path)
    units = UnitOfWorkFactory(store.paths.database_path)
    service = RuntimeConfigurationService(
        units,
        app_dir=store.paths.app_dir,
        state_supplier=store.load,
    )
    _seed_model(store, service)
    controller = FakeController()
    activation = ModelActivationService(
        UnitOfWorkFactory(store.paths.database_path),
        service,
        controller,
        state_supplier=store.load,
    )
    return store, service, controller, activation


def test_successful_activation_promotes_known_good(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)
    assert runtime.known_good() is None

    result = activation.activate(
        ActivationRequest(model_id=MODEL["id"], context=8192)
    )

    assert result.ok
    assert ("probe", MODEL["id"]) in controller.calls
    good = runtime.known_good()
    assert good is not None and good["model_alias"] == MODEL["id"]


def test_no_fit_request_has_zero_effects(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)
    before = runtime.capture()

    result = activation.activate(
        ActivationRequest(model_id="qwen25-coder-14b", context=8192, slots=2)
    )

    assert result.status == "REJECTED_INVALID"
    assert controller.calls == []
    assert runtime.capture() == before
    assert controller.calls == []  # host never touched


def test_thermal_stop_blocks_activation(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)
    ThermalStateService.for_database(store.paths.database_path).mark_stopped()

    result = activation.activate(ActivationRequest(model_id=MODEL["id"]))

    assert result.status == "REJECTED_THERMAL_LATCH"
    assert controller.calls == []


def test_missing_model_follows_policy(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)
    result = activation.activate(ActivationRequest(model_id="ghost-model"))
    assert result.status == "REJECTED_INVALID"
    assert "not installed" in result.detail["reason"]
    assert controller.calls == []


def test_health_failure_restores_previous_model(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)
    _install(
        store,
        [
            MODEL,
            dict(MODEL, id="qwen38-2b-distill", display_name="Qwen 3.8 2B Distill"),
        ],
        current=MODEL["id"],
    )

    controller.fail_health_model = "qwen38-2b-distill"
    result = activation.activate(ActivationRequest(model_id="qwen38-2b-distill"))
    assert result.status == "FAILED_ROLLED_BACK", {
        "result": result.to_dict(),
        "calls": controller.calls,
        "current": runtime.current(),
    }
    assert result.detail["restored_model"] == MODEL["id"]
    kinds = [call[0] for call in controller.calls]
    # Candidate attempt, then rollback that verifies health AND inference.
    assert kinds == ["restart", "health", "restart", "health", "probe"]
    assert runtime.current()["model_alias"] == MODEL["id"]


def test_minimal_inference_failure_also_rolls_back(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)
    _install(
        store,
        [
            MODEL,
            dict(MODEL, id="qwen38-2b-distill", display_name="Qwen 3.8 2B Distill"),
        ],
        current=MODEL["id"],
    )
    # The candidate loads and becomes healthy but serves no tokens; the
    # restored known-good model still verifies end to end.
    controller.fail_probe_model = "qwen38-2b-distill"

    result = activation.activate(
        ActivationRequest(model_id="qwen38-2b-distill")
    )

    assert result.status == "FAILED_ROLLED_BACK", result.to_dict()
    assert runtime.known_good() is None  # never promoted on failure
    assert result.detail["restored_model"] == MODEL["id"]
    kinds = [call[0] for call in controller.calls]
    assert kinds == ["restart", "health", "probe", "restart", "health", "probe"]


def test_rollback_inference_failure_enters_recovery_required(tmp_path):
    """A restored config that cannot serve tokens is RECOVERY_REQUIRED."""
    store, runtime, controller, activation = _activation(tmp_path)
    _install(
        store,
        [
            MODEL,
            dict(MODEL, id="qwen38-2b-distill", display_name="Qwen 3.8 2B Distill"),
        ],
        current=MODEL["id"],
    )
    # The restored model cannot serve tokens either: rollback verification
    # (health passes, probe fails) must surface RECOVERY_REQUIRED.
    def fail_all_probes(view):
        controller.calls.append(("probe", view.get("current_model")))
        raise RuntimeError("inference probe produced no tokens")

    controller.minimal_inference_probe = fail_all_probes  # type: ignore[method-assign]
    controller.fail_health_model = "qwen38-2b-distill"

    result = activation.activate(ActivationRequest(model_id="qwen38-2b-distill"))

    assert result.status == "RECOVERY_REQUIRED", result.to_dict()
    assert "rollback_error" in result.detail


def test_rollback_failure_produces_recovery_required(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)

    real_restart = controller.restart

    def always_fail_restart(view):
        real_restart(view)
        raise RuntimeError("systemctl restart failed")

    controller.restart = always_fail_restart  # type: ignore[method-assign]
    result = activation.activate(
        ActivationRequest(model_id=MODEL["id"], context=8192)
    )

    assert result.status == "RECOVERY_REQUIRED"
    recovery = runtime.recovery_required()
    assert recovery is not None
    assert "systemctl restart failed" in recovery["rollback_error"]


def test_concurrent_activation_conflicts_rather_than_interleaving(tmp_path):
    store, runtime, controller, activation = _activation(tmp_path)
    rev = runtime.current()["revision"]

    competing = RuntimeConfigurationService(
        UnitOfWorkFactory(store.paths.database_path),
        app_dir=store.paths.app_dir,
        state_supplier=store.load,
    )
    competing.apply({"context": 4096}, expected_revision=rev)

    result = activation.activate(
        ActivationRequest(model_id=MODEL["id"], context=8192, expected_revision=rev)
    )
    assert result.status == "CONFLICT"
    assert controller.calls == []