"""A5 + A4: typed runtime configuration (preview/apply) and the model
activation service with verified rollback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from _native import NativeApp
from bc250_llm_mode.runtime_handoff import HANDOFF_FILENAME
from bc250_llm_mode.services import (
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
