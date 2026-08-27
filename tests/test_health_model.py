"""P5 §11.2: the typed health model — closed vocabulary, deterministic
severity ordering, fail-closed staleness, and the green-claim rule."""

from __future__ import annotations

import pytest

from bc250_llm_mode import health


def dim(name, state, **kwargs):
    kwargs.setdefault("basis", health.BASIS_OBSERVED)
    kwargs.setdefault("evidence", "e")
    kwargs.setdefault("as_of", "2026-01-01T00:00:00Z")
    return health.dimension(name, state, **kwargs)


def test_vocabulary_is_closed_and_complete():
    assert set(health.HEALTH_STATES) == {
        "READY", "DEGRADED", "BUSY", "BLOCKED",
        "RECOVERY_REQUIRED", "REPAIR_REQUIRED", "UNVERIFIED", "UNAVAILABLE",
    }
    assert len(health.SEVERITY_ORDER) == len(health.HEALTH_STATES)
    assert set(health.SEVERITY_ORDER) == set(health.HEALTH_STATES)


def test_unknown_states_are_rejected():
    with pytest.raises(ValueError, match="unknown health state"):
        dim("x", "GREEN")
    with pytest.raises(ValueError, match="unknown health state"):
        health.severity_rank("MOSTLY_FINE")


def test_severity_ordering_is_deterministic():
    order = health.SEVERITY_ORDER  # most severe first
    for earlier, later in zip(order, order[1:]):
        assert health.severity_rank(earlier) > health.severity_rank(later)
        assert health.more_severe(earlier, later) == earlier
        assert health.more_severe(later, earlier) == earlier
    # Safety conditions dominate every service-quality claim.
    for quality in ("READY", "DEGRADED", "BUSY", "UNVERIFIED"):
        assert health.more_severe("BLOCKED", quality) == "BLOCKED"
        assert health.more_severe("RECOVERY_REQUIRED", quality) == "RECOVERY_REQUIRED"
        assert health.more_severe("REPAIR_REQUIRED", quality) == "REPAIR_REQUIRED"


def test_ready_requires_bounded_evidence():
    # Inferred green is a construction error: desired != live.
    with pytest.raises(ValueError, match="cannot be inferred"):
        dim("inference", "READY", basis=health.BASIS_INFERRED)
    # Verified/observed green without evidence or timestamp is refused too.
    with pytest.raises(ValueError, match="evidence"):
        health.dimension("inference", "READY",
                         basis=health.BASIS_OBSERVED, evidence="", as_of="t")
    with pytest.raises(ValueError, match="timestamp"):
        health.dimension("inference", "READY",
                         basis=health.BASIS_VERIFIED, evidence="probe ok", as_of=None)
    # Non-green states may be inferred (e.g. derived from durable config).
    inferred = dim("backup", "UNAVAILABLE", basis=health.BASIS_INFERRED,
                   evidence="no backup recorded", as_of=None)
    assert inferred.state == "UNAVAILABLE"


def test_stale_positive_claims_demote_to_unverified():
    for state in ("READY", "DEGRADED", "BUSY"):
        stale = dim("x", state, stale=True, stale_reason="reading older than bound")
        assert stale.effective_state == "UNVERIFIED"
        assert stale.to_dict()["effective_state"] == "UNVERIFIED"


def test_stale_safety_conditions_persist():
    # Fail-closed: negative conditions stay until an operator clears them.
    for state in ("BLOCKED", "RECOVERY_REQUIRED", "REPAIR_REQUIRED", "UNAVAILABLE"):
        stale = dim("x", state, stale=True, stale_reason="old reading")
        assert stale.effective_state == state


def test_fresh_dimensions_keep_their_state():
    for state in health.HEALTH_STATES:
        kwargs = {} if state != "READY" else {"basis": health.BASIS_VERIFIED}
        assert dim("x", state, **kwargs).effective_state == state


def test_compose_overall_picks_most_severe_effective_state():
    dims = [
        dim("runtime", "READY", basis=health.BASIS_VERIFIED),
        dim("model", "DEGRADED"),
        dim("thermal", "BLOCKED", evidence="latch engaged"),
    ]
    overall = health.compose_overall(dims)
    assert overall.state == "BLOCKED"
    assert overall.name == "overall"
    assert "thermal" in overall.evidence


def test_compose_overall_uses_effective_not_raw_state():
    # A stale READY is not green: it surfaces as UNVERIFIED (which the
    # severity ordering ranks above DEGRADED because an unknown capability
    # is worse than a known-degraded one), never as READY.
    dims = [
        dim("inference", "READY", basis=health.BASIS_VERIFIED,
            stale=True, stale_reason="probe older than bound"),
        dim("storage", "DEGRADED", evidence="91% used"),
    ]
    overall = health.compose_overall(dims)
    assert overall.state == "UNVERIFIED"
    assert overall.state != "READY"


def test_compose_overall_ties_keep_first_dimension():
    a = dim("a", "DEGRADED", evidence="x")
    b = dim("b", "DEGRADED", evidence="y")
    assert health.compose_overall([a, b]).evidence.startswith("most severe dimension: a")
    assert health.compose_overall([b, a]).evidence.startswith("most severe dimension: b")


def test_compose_overall_carries_winning_provenance():
    winner = dim("gateway", "UNAVAILABLE", basis=health.BASIS_INFERRED,
                 evidence="credential revoked", as_of="2026-01-02T00:00:00Z")
    overall = health.compose_overall([dim("runtime", "READY",
                                          basis=health.BASIS_VERIFIED), winner])
    assert overall.basis == health.BASIS_INFERRED
    assert overall.as_of == "2026-01-02T00:00:00Z"


def test_compose_overall_requires_dimensions():
    with pytest.raises(ValueError):
        health.compose_overall([])


def test_to_dict_round_trips_the_contract_fields():
    d = dim("inference", "READY", basis=health.BASIS_VERIFIED,
            evidence="bounded probe ok", as_of="2026-01-01T00:00:00Z")
    payload = d.to_dict()
    assert payload == {
        "name": "inference",
        "state": "READY",
        "effective_state": "READY",
        "basis": "verified",
        "evidence": "bounded probe ok",
        "as_of": "2026-01-01T00:00:00Z",
        "stale": False,
        "stale_reason": None,
    }
