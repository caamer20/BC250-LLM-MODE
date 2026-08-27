"""P9 §15.1/§15.2 + C1 §C1.2 + C7 §C7.1–§C7.3: release state & manifest (pure).

Pins the 1.0-qualification semantics: the build may NOT be tagged 1.0.0 while
any blocking gap remains (milestone gates, hardware qualification, human
acceptance, security review), and the known-unavailable capabilities are ALWAYS
visible in the release manifest rather than implying completeness.

C7 reconciliation: ``backup-restore-publish`` was IMPLEMENTED by C2, so it is no
longer an "unavailable capability" — its remaining 1.0 requirement is hardware
qualification evidence (tracked by the evidence gate + ``blocking_gaps``). The
single genuinely unavailable capability is ``model-conversion``
(DEFERRED_NOT_ADVERTISED). C7 adds the unclassified-limitation gate:
``may_tag_1_0_0()`` can never be true while a known limitation lacks a scope
classification in the release policy.
"""

from __future__ import annotations

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
    """C1.2: the deprecated approval booleans can never qualify a release on
    their own — they never satisfy the evidence requirement. (C7: there is no
    longer an unavailable mandatory capability, so the block here is purely the
    unsatisfied evidence.)"""
    state = _state(
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
    )
    assert state.blocking_gaps() == []  # no INFORMATIONAL gaps...
    # ...and C7: no mandatory capability is unavailable anymore...
    assert state.unavailable_mandatory_capabilities() == []
    # ...but booleans never satisfy the evidence requirement.
    assert state.evidence_satisfied is False
    assert state.may_tag_1_0_0() is False


def test_all_green_plus_evidence_is_the_legitimate_1_0_path():
    """C7: once backup-restore-publish is implemented (C2) and every blocking
    gap is cleared, the ONLY remaining authority is validated evidence. With all
    gaps green and evidence satisfied, the facade may tag — evidence_satisfied
    can only be set through the evidence-driven evaluator, which itself requires
    the hardware/security/human records, so this is not a boolean bypass."""
    state = _state(
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
        evidence_satisfied=True,
    )
    assert state.unavailable_mandatory_capabilities() == []
    assert state.unclassified_limitations() == []
    assert state.may_tag_1_0_0() is True


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


def test_unclassified_limitation_blocks_the_tag(monkeypatch):
    """C7 exit gate: may_tag_1_0_0() can never be true while a known limitation
    has no scope classification in the release policy."""
    import bc250_llm_mode.release_state as rs

    state = _state(
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
        evidence_satisfied=True,
    )
    assert state.may_tag_1_0_0() is True  # baseline: fully classified

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
    assert doc["database_schema_version"] == 10
    assert doc["identities"][0]["identity"] == "llamacpp:sha256:abc"
    assert doc["may_tag_1_0_0"] is False
    assert doc["unclassified_limitations"] == []


def test_partial_green_still_blocks():
    state = _state(milestone_gates_green=True,
                   hardware_qualification_green=True)
    assert state.may_tag_1_0_0() is False
    gaps = state.blocking_gaps()
    assert len(gaps) == 2  # human + security still pending
