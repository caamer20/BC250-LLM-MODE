"""P8 §14.4: Repair Center contract (pure).

Pins the closed repair-action catalogue, precondition gating (an action is
available only when EVERY precondition is met), idempotent/auditable flags,
and the condition->finding mapping.
"""

from __future__ import annotations

from bc250_llm_mode.repair_center import (
    REPAIR_ACTIONS,
    available_action_ids,
    findings_from_conditions,
    repair_actions_for_conditions,
)


def test_catalogue_covers_the_supported_repairs():
    ids = {a.action_id for a in REPAIR_ACTIONS}
    assert {
        "retry-legacy-import", "upgrade-newer-schema",
        "reclaim-orphaned-content", "release-expired-worker-locks",
        "regenerate-runtime-handoff", "restore-known-good-lineage",
        "rotate-gateway-credentials", "rebuild-support-bundle",
    } <= ids


def test_actions_are_idempotent_and_auditable():
    for action in REPAIR_ACTIONS:
        assert action.idempotent is True
        assert action.auditable is True


def test_precondition_gating():
    # No conditions -> only the precondition-free action is available.
    assert available_action_ids(set()) == ["rebuild-support-bundle"]
    # Single-precondition action becomes available.
    assert "retry-legacy-import" in available_action_ids({"legacy_import_failed"})
    # Multi-precondition action requires ALL preconditions.
    assert "regenerate-runtime-handoff" not in available_action_ids(
        {"handoff_missing_or_stale"})
    assert "regenerate-runtime-handoff" in available_action_ids(
        {"handoff_missing_or_stale", "runtime_active_verified"})


def test_rendered_actions_report_availability():
    rendered = repair_actions_for_conditions({"gateway_provisioned"})
    by_id = {r["action_id"]: r for r in rendered}
    assert by_id["rotate-gateway-credentials"]["available"] is True
    assert by_id["retry-legacy-import"]["available"] is False
    assert by_id["rebuild-support-bundle"]["available"] is True


def test_findings_from_conditions():
    findings = findings_from_conditions(
        {"legacy_import_failed", "expired_worker_locks", "unrelated"})
    ids = [f.finding_id for f in findings]
    assert "repair-legacy-import" in ids
    assert "repair-worker-locks" in ids
    assert len(findings) == 2  # 'unrelated' maps to nothing
    for f in findings:
        assert f.recommended_action_id
        assert f.to_dict()["schema_version"] == 2


def test_exp5_catalogue_has_complete_typed_metadata_and_no_executable_routes():
    for action in REPAIR_ACTIONS:
        rendered = action.to_dict(set(action.preconditions))
        assert rendered["owner_kind"] in {"SERVICE", "OPERATION", "QUERY"}
        assert 1 <= len(rendered["mutation_steps"]) <= 16
        assert rendered["privilege"] in {"USER", "ELEVATED", "MIXED"}
        assert rendered["cancellation_policy"] in {
            "NOT_APPLICABLE", "BEFORE_EFFECT", "OWNER_SAFE_POINTS"}
        assert rendered["reversibility"] in {
            "EXACT_UNTIL", "COMPENSATED_BY_OWNER", "IRREVERSIBLE"}
        assert rendered["success_probe_id"]
        assert rendered["failure_codes"]
        assert rendered["routes_to"] == rendered["owner_id"]


def test_newer_schema_finding_is_fail_severity():
    findings = findings_from_conditions({"newer_schema_refused"})
    assert findings[0].severity == "FAIL"
    assert findings[0].recommended_action_id == "upgrade-newer-schema"
