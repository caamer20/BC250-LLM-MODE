"""C7 §C7.1/§C7.2/§C7.3 (V1_0_RELEASE_CLOSURE plan): capability-scope decision.

Tests the refined 5-value capability-scope vocabulary, the reviewed policy v2
(backup-restore-publish MANDATORY_FOR_1_0 and no longer "unavailable" after C2;
model-conversion DEFERRED_NOT_ADVERTISED), the unclassified-limitation gate, the
model-conversion scope-decision record binding, and the CLI/service consistency
that conversion returns a clear capability status (never a generic failure).
"""

from __future__ import annotations

from pathlib import Path

from bc250_llm_mode.release_policy import (
    RELEASE_POLICY_VERSION,
    CapabilityClass,
    default_release_policy,
)
from bc250_llm_mode.release_state import (
    KNOWN_UNAVAILABLE_CAPABILITIES,
    ReleaseState,
)

_ROOT = Path(__file__).resolve().parent.parent


def test_capability_vocabulary_is_the_five_scope_classes():
    values = {c.value for c in CapabilityClass}
    assert values == {
        "MANDATORY_FOR_1_0",
        "SUPPORTED_OPTIONAL",
        "EXPERIMENTAL",
        "DEFERRED_NOT_ADVERTISED",
        "REMOVED",
    }


def test_policy_v3_classifies_backup_restore_mandatory():
    """G2 migrated: policy content revision 3 (adds the approved verification
    mechanisms) keeps the C7 capability classification intact."""
    policy = default_release_policy()
    assert RELEASE_POLICY_VERSION == 3
    assert policy.policy_version == 3
    assert "backup-restore-publish" in policy.mandatory_capabilities()
    by = {c.capability: c.classification for c in policy.capabilities}
    assert by["backup-restore-publish"] == "MANDATORY_FOR_1_0"


def test_policy_v2_defers_model_conversion_not_advertised():
    policy = default_release_policy()
    by = {c.capability: c.classification for c in policy.capabilities}
    assert by["model-conversion"] == "DEFERRED_NOT_ADVERTISED"
    assert "model-conversion" in policy.limitation_capabilities()
    assert "model-conversion" not in policy.mandatory_capabilities()


def test_removed_is_not_an_accepted_limitation():
    # A REMOVED capability is gone (with migration guidance) and needs no
    # limitation-acceptance record, so it must not appear as a limitation.
    from bc250_llm_mode.release_policy import CapabilityPolicy, ReleasePolicyV1
    policy = ReleasePolicyV1(
        capabilities=(CapabilityPolicy("old-feature", "REMOVED", "deleted"),))
    assert policy.limitation_capabilities() == frozenset()
    assert policy.mandatory_capabilities() == frozenset()
    assert policy.classified_capabilities() == frozenset({"old-feature"})


def test_every_known_limitation_is_classified():
    """C7 exit gate: no unclassified limitation remains in the current build."""
    state = ReleaseState(version="0.9.0.dev0")
    assert state.unclassified_limitations() == []
    classified = default_release_policy().classified_capabilities()
    for cap in KNOWN_UNAVAILABLE_CAPABILITIES:
        assert cap["capability"] in classified


def test_backup_restore_no_longer_unavailable_model_conversion_still_is():
    unavailable = {c["capability"] for c in KNOWN_UNAVAILABLE_CAPABILITIES}
    assert "backup-restore-publish" not in unavailable
    assert "model-conversion" in unavailable


def test_no_unavailable_mandatory_capability_remains():
    """C7 exit gate: no mandatory capability is unavailable."""
    state = ReleaseState(version="0.9.0.dev0")
    assert state.unavailable_mandatory_capabilities() == []


def test_scope_decision_record_is_bound_to_policy():
    """C7.2 item 5: a reviewed scope-decision record tied to policy/version."""
    record = _ROOT / "release" / "scope-decision-model-conversion.md"
    assert record.is_file()
    text = record.read_text(encoding="utf-8")
    policy = default_release_policy()
    assert policy.policy_digest() in text
    assert "DEFERRED_NOT_ADVERTISED" in text
    assert f"policy version **{RELEASE_POLICY_VERSION}**" in text


def test_convert_returns_clear_capability_status_not_generic_failure():
    """C7.2 item 3: conversion returns a clear capability status."""
    from bc250_llm_mode.model_convert_command import ModelConvertCommandService

    svc = ModelConvertCommandService()
    outcome = svc.convert("some-model", "q4_k_m")
    assert outcome.status == "UNAVAILABLE"
    assert outcome.ok is False
    assert outcome.detail.get("reason")  # an honest reason, not a bare failure
    avail = svc.availability()
    assert avail.get("available") is False
