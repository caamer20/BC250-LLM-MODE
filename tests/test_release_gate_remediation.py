"""G0.2 (§6, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): intentionally-RED
eligibility-authority tests.

These freeze audit findings 1-3 (§1) and the §13.1 release-authority matrix as
failing gates BEFORE any production change. G1 turns them green by making
``evaluate_release`` the SOLE eligibility authority over a mandatory immutable
``CandidateIdentity`` and by removing the ``ReleaseState.evidence_satisfied``
boolean bypass. They are RED on purpose at G0:

  * AssertionError — the current code still permits the audited bypass;
  * ImportError    — the remediated authority types do not exist yet.

Do not weaken these to make them pass: each one names the exact bypass it
closes. They run in the default collection immediately (plan §G0.3).
"""

from __future__ import annotations

import pytest

# A plausible full 40-character lowercase commit SHA (test data only).
FULL_COMMIT = "0123456789abcdef0123456789abcdef01234567"


# --- audit finding 1: ReleaseState boolean bypass ---------------------------

def test_red_release_state_evidence_satisfied_cannot_qualify_tag():
    """Audit finding 1: ``ReleaseState(..., evidence_satisfied=True)`` currently
    returns ``may_tag_1_0_0() == True`` without any evaluator-produced evidence.
    After G1 the field is deleted (TypeError) or permanently ignored (the facade
    can never assert eligibility)."""
    from bc250_llm_mode.release_state import ReleaseState

    try:
        state = ReleaseState(
            version="1.0.0",
            milestone_gates_green=True,
            hardware_qualification_green=True,
            human_acceptance_green=True,
            security_review_signed_off=True,
            evidence_satisfied=True,
        )
    except TypeError:
        return  # field removed: the bypass is structurally impossible
    assert state.may_tag_1_0_0() is False, (
        "a public ReleaseState boolean must never qualify a 1.0.0 tag")


# --- audit finding 2: optional source commit --------------------------------

def test_red_evaluate_release_refuses_missing_source_commit():
    """Audit finding 2: ``evaluate_release`` currently permits
    ``source_commit=None``; a complete synthetic evidence set could qualify
    without source binding. After G1 the sourceless call path no longer exists
    (TypeError) — eligibility always names a full candidate commit."""
    from bc250_llm_mode.release_gate import evaluate_release

    try:
        decision = evaluate_release(evidence=[], candidate_version="1.0.0")
    except TypeError:
        return  # remediated: evaluation without a candidate identity is gone
    assert decision.eligible_for_rc is False
    assert decision.eligible_for_1_0_0 is False
    assert decision.source_commit, (
        "an evaluation that cannot name its full source commit must not exist")


def test_red_cli_evaluate_requires_source_commit(tmp_path):
    """§G3.3 sibling of finding 2: the release CLI must refuse to evaluate
    without ``--source-commit`` (usage error, exit 2) instead of silently
    deciding with no source binding."""
    from tools.release.__main__ import main as release_main

    ev = tmp_path / "evidence"
    ev.mkdir()
    try:
        rc = release_main(
            ["evaluate", "--candidate", "1.0.0rc1", "--evidence", str(ev)])
    except SystemExit as exc:
        rc = exc.code
    assert rc == 2, "--source-commit must be mandatory for release evaluation"


# --- audit finding 3: candidate identity validation --------------------------

def test_red_candidate_identity_rejects_bad_commits():
    """Audit finding 3 / §4.2: ``CandidateIdentity`` must refuse abbreviated,
    malformed, non-hex, uppercase, all-zero, and wrong-length commit hashes."""
    from bc250_llm_mode.release_gate import CandidateIdentity
    from bc250_llm_mode.release_policy import default_release_policy

    digest = default_release_policy().policy_digest()
    for bad in ("", "abc123", "z" * 40, "0" * 40, "F" * 40,
                "a" * 39, "a" * 41):
        with pytest.raises(ValueError):
            CandidateIdentity(
                version="1.0.0rc1", source_commit=bad,
                source_ref="refs/heads/main", repository="local",
                policy_digest=digest)


def test_red_candidate_identity_requires_ref_repository_and_policy_digest():
    """§4.2: a bare branch name, an empty repository identity, and a malformed
    policy digest are all invalid candidate identities."""
    from bc250_llm_mode.release_gate import CandidateIdentity
    from bc250_llm_mode.release_policy import default_release_policy

    digest = default_release_policy().policy_digest()
    with pytest.raises(ValueError):
        CandidateIdentity(  # bare branch name is not a full ref
            version="1.0.0rc1", source_commit=FULL_COMMIT,
            source_ref="main", repository="local", policy_digest=digest)
    with pytest.raises(ValueError):
        CandidateIdentity(
            version="1.0.0rc1", source_commit=FULL_COMMIT,
            source_ref="refs/heads/main", repository="", policy_digest=digest)
    with pytest.raises(ValueError):
        CandidateIdentity(
            version="1.0.0rc1", source_commit=FULL_COMMIT,
            source_ref="refs/heads/main", repository="local",
            policy_digest="sha256:nope")
    with pytest.raises(ValueError):
        CandidateIdentity(  # empty version
            version="", source_commit=FULL_COMMIT,
            source_ref="refs/heads/main", repository="local",
            policy_digest=digest)


def test_red_final_release_requires_exact_protected_tag():
    """Audit finding 3 / §4.2: a FINAL 1.0.0 evaluation must refuse a candidate
    whose ref is not exactly the approved final tag ``refs/tags/v1.0.0`` — a
    moving branch name is never sufficient identity for a final release."""
    from bc250_llm_mode.release_artifacts import ArtifactInventory
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy

    policy = default_release_policy()
    candidate = CandidateIdentity(
        version="1.0.0", source_commit=FULL_COMMIT,
        source_ref="refs/heads/main",  # NOT refs/tags/v1.0.0
        repository="local", policy_digest=policy.policy_digest())
    decision = evaluate_release(
        evidence=[], candidate=candidate,
        artifacts=ArtifactInventory(), policy=policy)
    assert decision.eligible_for_1_0_0 is False
    assert "CANDIDATE_REF_MISMATCH" in decision.blocking_codes


def test_red_candidate_policy_digest_mismatch_blocks():
    """§13.1: a candidate carrying a policy digest that does not match the
    evaluating policy must block with a stable code."""
    from bc250_llm_mode.release_artifacts import ArtifactInventory
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy

    policy = default_release_policy()
    candidate = CandidateIdentity(
        version="1.0.0rc1", source_commit=FULL_COMMIT,
        source_ref="refs/heads/main", repository="local",
        policy_digest="sha256:" + "e" * 64)  # wrong digest
    decision = evaluate_release(
        evidence=[], candidate=candidate,
        artifacts=ArtifactInventory(), policy=policy)
    assert decision.eligible_for_rc is False
    assert decision.eligible_for_1_0_0 is False
    assert "POLICY_DIGEST_MISMATCH" in decision.blocking_codes


# --- §G1.4: decision must name candidate + inventory -------------------------

def test_red_decision_names_candidate_identity_and_inventory_digest():
    """§G1.4: the serialized decision must carry the full candidate identity
    (including the source commit) and the artifact inventory digest so a
    manifest can be bound to exact bytes — and never the private evaluator
    marker."""
    from bc250_llm_mode.release_artifacts import ArtifactInventory
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy

    policy = default_release_policy()
    candidate = CandidateIdentity(
        version="1.0.0rc1", source_commit=FULL_COMMIT,
        source_ref="refs/heads/main", repository="local",
        policy_digest=policy.policy_digest())
    decision = evaluate_release(
        evidence=[], candidate=candidate,
        artifacts=ArtifactInventory(), policy=policy)
    doc = decision.to_dict()
    assert doc.get("source_commit") == FULL_COMMIT
    assert doc.get("candidate_version") == "1.0.0rc1"
    assert "inventory_digest" in doc, (
        "the decision must bind the artifact inventory digest")
    assert "_evaluator_key" not in doc


# --- §G1.5: single-authority architecture guard ------------------------------

def test_eligibility_authority_is_the_single_evaluator_guard():
    """§G1.5: no production module outside the release authority pair may
    claim tag eligibility — no ``may_tag_1_0_0`` calls and no
    ``evidence_satisfied`` references anywhere in the package or the release
    tooling except the authority modules themselves."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    allowed = {"release_gate.py", "release_state.py"}
    offenders: list[str] = []
    for base in (root / "bc250_llm_mode", root / "tools"):
        for path in sorted(base.rglob("*.py")):
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if "may_tag_1_0_0" in text or "evidence_satisfied" in text:
                offenders.append(str(path.relative_to(root)))
    assert offenders == [], (
        f"eligibility authority leaked into: {offenders}")
