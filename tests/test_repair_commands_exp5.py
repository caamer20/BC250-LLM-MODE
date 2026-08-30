from __future__ import annotations

import json

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repair_center import REPAIR_ACTION_IDS
from bc250_llm_mode.repair_commands import (
    RepairBinding,
    RepairCommandError,
    RepairCommandService,
    RepairObservation,
    RepairOwnerResult,
    RepairProbe,
)


NOW = "2026-08-30T12:01:00Z"


def _bindings(world):
    result = {}

    def observe(_target):
        return RepairObservation(
            frozenset({"expired_worker_locks"}) if world["available"] else frozenset(),
            (("worker-lock", world["revision"]),),
            {"expired": world["available"]},
        )

    def execute(_target, observation):
        world["executions"] += 1
        world["available"] = False
        world["revision"] += 1
        return RepairOwnerResult(
            "SUCCEEDED", "WORKER_LOCK_RELEASED",
            owner_revision=world["revision"],
            one_time_secret="do-not-serialize-this-secret",
        )

    def verify(_target, _prior):
        return RepairProbe(
            not world["available"],
            "WORKER_LOCK_RELEASED" if not world["available"] else "LOCK_PRESENT",
        )

    for action_id in REPAIR_ACTION_IDS:
        if action_id == "release-expired-worker-locks":
            binding = RepairBinding(action_id, observe, execute, verify)
        else:
            binding = RepairBinding(
                action_id,
                lambda _target: RepairObservation(reason_code="TEST_UNAVAILABLE"),
                lambda _target, _observation: RepairOwnerResult(
                    "REFUSED", "TEST_UNAVAILABLE"),
                lambda _target, _prior: RepairProbe(False, "TEST_UNAVAILABLE"),
            )
        result[action_id] = binding
    return result


def _service(world):
    return RepairCommandService(_bindings(world), clock=lambda: NOW)


def test_binding_map_must_be_exhaustive_and_exact():
    world = {"available": True, "revision": 1, "executions": 0}
    bindings = _bindings(world)
    bindings.pop(REPAIR_ACTION_IDS[0])
    with pytest.raises(ValueError, match="binding mismatch"):
        RepairCommandService(bindings, clock=lambda: NOW)


def test_preview_is_deterministic_bounded_and_executes_exactly_once():
    world = {"available": True, "revision": 7, "executions": 0}
    service = _service(world)
    first = service.preview("release-expired-worker-locks")
    second = service.preview("release-expired-worker-locks")
    assert first.ready and first == second
    assert first.expected_revisions == (("worker-lock", 7),)
    assert len(first.preview_digest) == 64
    assert first.confirmation_token.startswith("REPAIR-")

    result = service.run(
        first.action.action_id,
        preview_digest=first.preview_digest,
        confirmation_token=first.confirmation_token,
    )
    assert result.ok
    assert result.probe.code == "WORKER_LOCK_RELEASED"
    assert world["executions"] == 1
    assert result.one_time_secret == "do-not-serialize-this-secret"
    encoded = json.dumps(result.to_dict())
    assert "do-not-serialize" not in encoded
    assert result.to_dict()["has_one_time_secret"] is True


def test_changed_revision_and_wrong_confirmation_cannot_execute():
    world = {"available": True, "revision": 2, "executions": 0}
    service = _service(world)
    preview = service.preview("release-expired-worker-locks")
    wrong = service.run(
        preview.action.action_id,
        preview_digest=preview.preview_digest,
        confirmation_token="REPAIR-WRONG",
    )
    assert wrong.outcome == "REFUSED"
    assert wrong.result_code == "PREVIEW_STALE"
    assert world["executions"] == 0

    world["revision"] = 3
    stale = service.run(
        preview.action.action_id,
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert stale.outcome == "REFUSED"
    assert stale.result_code == "PREVIEW_STALE"
    assert world["executions"] == 0


def test_failed_precondition_cannot_be_bypassed_with_a_matching_token():
    world = {"available": False, "revision": 1, "executions": 0}
    service = _service(world)
    preview = service.preview("release-expired-worker-locks")
    assert not preview.ready
    result = service.run(
        preview.action.action_id,
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert result.outcome == "REFUSED"
    assert world["executions"] == 0


def test_target_validation_and_unknown_actions_fail_closed():
    world = {"available": True, "revision": 1, "executions": 0}
    service = _service(world)
    with pytest.raises(RepairCommandError) as unknown:
        service.preview("run-anything")
    assert unknown.value.code == "UNKNOWN_REPAIR_ACTION"
    with pytest.raises(RepairCommandError) as unsafe:
        service.preview("recover-durable-operation", "../../operation")
    assert unsafe.value.code == "TARGET_INVALID"
    missing = service.preview("recover-durable-operation")
    assert missing.reason_code == "TARGET_REQUIRED"


def test_production_composition_maps_every_id_and_support_bundle_self_checks(tmp_path):
    app = Application.compose(AppPaths.temporary(tmp_path))
    assert app.repair_commands.mapped_action_ids == REPAIR_ACTION_IDS
    preview = app.repair_commands.preview("rebuild-support-bundle")
    assert preview.ready
    result = app.repair_commands.run(
        "rebuild-support-bundle",
        preview_digest=preview.preview_digest,
        confirmation_token=preview.confirmation_token,
    )
    assert result.ok
    assert result.probe.code == "SUPPORT_BUNDLE_VERIFIED"
    destination = app.paths.app_dir / "support-bundles" / preview.target_id
    assert app.support_bundle.verify(destination)["valid"] is True
    (destination / "home.json").write_text("changed", encoding="utf-8")
    assert app.support_bundle.verify(destination)["valid"] is False


def test_production_mutating_repairs_require_safety_acknowledgment(tmp_path):
    app = Application.compose(AppPaths.temporary(tmp_path))
    preview = app.repair_commands.preview("release-expired-worker-locks")
    assert preview.reason_code == "SAFETY_ACKNOWLEDGMENT_REQUIRED"
    assert not preview.ready
