"""P9 §15.1/§15.2 + C1 §C1.2 + C7 §C7.1–§C7.3 + G1 §G1.1: release state (pure).

Pins the release-state semantics: the facade is INFORMATIONAL ONLY. G1
(RELEASE_GATE_AND_PIPELINE_REMEDIATION plan) deleted the ``evidence_satisfied``
authority field and made ``may_tag_1_0_0()`` a non-authoritative constant
``False`` — the ONLY eligibility authority is the ``ReleaseDecision`` from
``release_gate.evaluate_release``. The known-unavailable capabilities stay
VISIBLE in the release manifest rather than implying completeness.

C7 reconciliation: ``backup-restore-publish`` was IMPLEMENTED by C2, so it is
no longer an "unavailable capability" — its remaining 1.0 requirement is
hardware qualification evidence (tracked by the evidence gate +
``blocking_gaps``). The single genuinely unavailable capability is
``model-conversion`` (DEFERRED_NOT_ADVERTISED). Every known limitation must
carry a scope classification in the release policy.
"""

from __future__ import annotations

import pytest

from bc250_llm_mode.release_state import (
    KNOWN_UNAVAILABLE_CAPABILITIES,
    RELEASE_MANIFEST_SCHEMA_VERSION,
    ReleaseIdentity,
    ReleaseState,
    build_release_manifest,
)


def _state(**overrides):
    defaults = dict(
        version="0.9.0.dev0",
        identities=(ReleaseIdentity(kind="runtime",
                                    identity="llamacpp:sha256:abc",
                                    digest="sha256:abc"),),
    )
    defaults.update(overrides)
    return ReleaseState(**defaults)


def test_current_state_is_not_1_0_ready():
    state = _state()
    assert state.may_tag_1_0_0() is False
    gaps = state.blocking_gaps()
    assert any("milestone" in g for g in gaps)
    assert any("hardware" in g for g in gaps)
    assert any("human" in g for g in gaps)
    assert any("security" in g for g in gaps)


def test_all_green_booleans_alone_are_not_1_0_ready():
    """C1.2 + G1: the informational approval booleans can never qualify a
    release — the facade has no eligibility authority at all."""
    state = _state(
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
    )
    assert state.blocking_gaps() == []  # no INFORMATIONAL gaps...
    # ...and C7: no mandatory capability is unavailable anymore...
    assert state.unavailable_mandatory_capabilities() == []
    # ...but the facade still can never assert eligibility.
    assert state.may_tag_1_0_0() is False


def test_no_public_release_state_construction_can_ever_tag():
    """G1 §G1.1/§15.1 (explicit compatibility decision): the facade's
    eligibility authority is DELETED. Constructing with the removed
    ``evidence_satisfied`` field raises ``TypeError``, and
    ``may_tag_1_0_0()`` is a non-authoritative constant ``False`` even with
    every informational boolean green. The ONLY eligibility authority is the
    ``ReleaseDecision`` from ``release_gate.evaluate_release``."""
    with pytest.raises(TypeError):
        _state(
            milestone_gates_green=True,
            hardware_qualification_green=True,
            human_acceptance_green=True,
            security_review_signed_off=True,
            evidence_satisfied=True,
        )
    state = _state(
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
    )
    assert state.unavailable_mandatory_capabilities() == []
    assert state.unclassified_limitations() == []
    assert state.may_tag_1_0_0() is False


def test_unavailable_capabilities_always_visible():
    """C7: the single genuinely unavailable capability (model-conversion) stays
    visible; backup-restore-publish is no longer listed unavailable."""
    doc = build_release_manifest(_state())
    caps = {c["capability"] for c in doc["unavailable_capabilities"]}
    assert "model-conversion" in caps
    assert "backup-restore-publish" not in caps
    for cap in doc["unavailable_capabilities"]:
        assert cap["reason"]
        assert cap["visible_in"]


def test_backup_restore_publish_no_longer_unavailable():
    """C7 §C7.1: backup-restore-publish cannot remain merely visible and
    unavailable — C2 implemented it, so it leaves the unavailable list and its
    1.0 requirement becomes hardware evidence (tracked elsewhere)."""
    unavailable = {c["capability"] for c in KNOWN_UNAVAILABLE_CAPABILITIES}
    assert "backup-restore-publish" not in unavailable
    assert "model-conversion" in unavailable


def test_unclassified_limitation_is_surfaced_and_blocks(monkeypatch):
    """C7 gate preserved under G1: an unclassified known limitation is always
    surfaced, and the facade itself can never tag regardless."""
    import bc250_llm_mode.release_state as rs

    state = _state(
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
    )
    assert state.unclassified_limitations() == []  # baseline: fully classified
    assert state.may_tag_1_0_0() is False  # G1: never from the facade

    # Inject an unclassified known limitation.
    monkeypatch.setattr(rs, "KNOWN_UNAVAILABLE_CAPABILITIES", (
        {"capability": "some-new-limitation",
         "reason": "not yet classified", "visible_in": "release notes"},
    ))
    assert state.unclassified_limitations() == ["some-new-limitation"]
    assert state.may_tag_1_0_0() is False


def test_manifest_carries_identities_and_schema():
    doc = build_release_manifest(_state())
    assert doc["manifest_schema_version"] == RELEASE_MANIFEST_SCHEMA_VERSION
    assert doc["version"] == "0.9.0.dev0"
    assert doc["database_schema_version"] == 12
    assert doc["identities"][0]["identity"] == "llamacpp:sha256:abc"
    assert doc["may_tag_1_0_0"] is False
    assert doc["unclassified_limitations"] == []


def test_partial_green_still_blocks():
    state = _state(milestone_gates_green=True,
                   hardware_qualification_green=True)
    assert state.may_tag_1_0_0() is False
    gaps = state.blocking_gaps()
    assert len(gaps) == 2  # human + security still pending
