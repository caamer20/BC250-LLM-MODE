"""P9 §15.1/§15.2: release state and manifest contract (pure).

Pins the 1.0-qualification semantics: the build may NOT be tagged 1.0.0 while
any blocking gap remains (milestone gates, hardware qualification, human
acceptance, security review), and the known-unavailable capabilities (model
conversion, hardware-gated backup-restore publish) are ALWAYS visible in the
release manifest rather than implying completeness.
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


def test_all_green_state_is_1_0_ready():
    state = _state(
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
    )
    assert state.blocking_gaps() == []
    assert state.may_tag_1_0_0() is True


def test_unavailable_capabilities_always_visible():
    doc = build_release_manifest(_state())
    caps = {c["capability"] for c in doc["unavailable_capabilities"]}
    assert "model-conversion" in caps
    assert "backup-restore-publish" in caps
    for cap in doc["unavailable_capabilities"]:
        assert cap["reason"]
        assert cap["visible_in"]


def test_manifest_carries_identities_and_schema():
    doc = build_release_manifest(_state())
    assert doc["manifest_schema_version"] == RELEASE_MANIFEST_SCHEMA_VERSION
    assert doc["version"] == "0.9.0.dev0"
    assert doc["database_schema_version"] == 9
    assert doc["identities"][0]["identity"] == "llamacpp:sha256:abc"
    assert doc["may_tag_1_0_0"] is False


def test_partial_green_still_blocks():
    state = _state(milestone_gates_green=True,
                   hardware_qualification_green=True)
    assert state.may_tag_1_0_0() is False
    gaps = state.blocking_gaps()
    assert len(gaps) == 2  # human + security still pending
