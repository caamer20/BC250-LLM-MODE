"""C0.2 (V1_0_RELEASE_CLOSURE plan): intentionally-RED release-gate tests.

These tests encode the DESIRED evidence-driven release semantics and are RED on
purpose at C0: they fail for the intended reasons against the current
boolean-driven ``release_state.py`` and the not-yet-written evidence modules.
C1 implements ``release_policy`` / ``release_evidence`` / ``release_gate`` and
turns them green; they are then folded into the default suite.

Run explicitly:  PYTHONPATH=. .venv/bin/pytest -m release_gate_v2

RED reasons:
  * tests over the CURRENT model fail with AssertionError — the boolean model
    is too permissive (it can qualify a release with no evidence);
  * tests over the FUTURE evidence API fail with ModuleNotFoundError /
    ImportError — the evidence-driven evaluator does not exist yet.
"""

from __future__ import annotations

import pytest

# All of these tests are the C0 red gate.
pytestmark = pytest.mark.release_gate_v2


# ---------------------------------------------------------------------------
# Tests over the CURRENT boolean model: prove it is insufficient.
# ---------------------------------------------------------------------------

def test_red_caller_booleans_alone_must_not_qualify_release():
    """RED #5: setting every approval boolean True must NOT qualify a release
    when no evidence exists. The current model returns True -> this fails."""
    from bc250_llm_mode.release_state import ReleaseState

    state = ReleaseState(
        version="1.0.0",
        milestone_gates_green=True,
        hardware_qualification_green=True,
        human_acceptance_green=True,
        security_review_signed_off=True,
    )
    # Desired: booleans without evidence cannot qualify a release.
    assert state.may_tag_1_0_0() is False


def test_red_known_unavailable_mandatory_capability_blocks_release(monkeypatch):
    """RED #8 (C7-reconciled, G1-migrated): a mandatory capability that is
    unavailable must block release. After C2 implemented backup-restore-publish
    and C7 removed it from the unavailable list, NO real mandatory capability
    is unavailable — so this simulates the pre-C7 state (backup-restore-publish
    unavailable) to prove the evaluator still blocks it. G1: the evaluator now
    DERIVES the unavailable set from the reviewed product state and requires a
    full CandidateIdentity; the facade itself can never tag."""
    import bc250_llm_mode.release_state as rs
    from bc250_llm_mode.release_artifacts import ArtifactInventory
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy

    monkeypatch.setattr(rs, "KNOWN_UNAVAILABLE_CAPABILITIES", (
        {"capability": "backup-restore-publish",
         "reason": "simulated pre-C7 unavailable mandatory capability",
         "visible_in": "test"},
    ))
    policy = default_release_policy()
    candidate = CandidateIdentity(
        version="1.0.0", source_commit="c" * 40,
        source_ref="refs/tags/v1.0.0", repository="local",
        policy_digest=policy.policy_digest())
    decision = evaluate_release(
        evidence=[], candidate=candidate, artifacts=ArtifactInventory(),
        policy=policy)
    # Desired: an unavailable mandatory capability blocks the release.
    assert "backup-restore-publish" in (
        rs.KNOWN_UNAVAILABLE_CAPABILITIES[0]["capability"])
    assert decision.eligible_for_1_0_0 is False
    assert "CAPABILITY_UNAVAILABLE" in decision.blocking_codes


# ---------------------------------------------------------------------------
# Tests over the FUTURE evidence API: fail until C1 implements it.
# ---------------------------------------------------------------------------

def _evaluate_empty(version="1.0.0"):
    """G1-migrated helper: evaluate with NO evidence over a full candidate
    identity bound to the reviewed policy (final versions ride their tag)."""
    from bc250_llm_mode.release_artifacts import ArtifactInventory
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy

    policy = default_release_policy()
    is_final = all(part.isdigit() for part in version.split("."))
    ref = f"refs/tags/v{version}" if is_final else "refs/heads/main"
    candidate = CandidateIdentity(
        version=version, source_commit="c" * 40, source_ref=ref,
        repository="local", policy_digest=policy.policy_digest())
    return evaluate_release(
        evidence=[], candidate=candidate, artifacts=ArtifactInventory(),
        policy=policy)


def test_red_missing_signing_evidence_blocks_release():
    """RED #1: may_tag_1_0_0 must be False when signing evidence is missing."""
    from bc250_llm_mode.release_gate import evaluate_release  # noqa: F401
    from bc250_llm_mode.release_policy import ReleasePolicyV1  # noqa: F401

    decision = _evaluate_empty("1.0.0")
    assert decision.eligible_for_1_0_0 is False
    assert "SIGNATURE_MISSING_OR_INVALID" in decision.blocking_codes


def test_red_missing_sbom_evidence_blocks_release():
    """RED #2: may_tag_1_0_0 must be False when SBOM evidence is missing."""
    decision = _evaluate_empty("1.0.0")
    assert decision.eligible_for_1_0_0 is False
    assert "SBOM_MISSING_OR_MISMATCHED" in decision.blocking_codes


def test_red_missing_provenance_attestation_blocks_release():
    """RED #3: missing provenance/attestation evidence blocks release."""
    decision = _evaluate_empty("1.0.0")
    assert decision.eligible_for_1_0_0 is False
    assert "PROVENANCE_MISSING_OR_MISMATCHED" in decision.blocking_codes


def test_red_missing_backup_restore_hardware_evidence_blocks_release():
    """RED #4: missing backup-restore hardware evidence blocks release."""
    decision = _evaluate_empty("1.0.0")
    assert decision.eligible_for_1_0_0 is False
    assert "BACKUP_RESTORE_EVIDENCE_MISSING" in decision.blocking_codes


def test_red_evidence_bound_to_wrong_candidate_is_rejected():
    """RED #6: evidence for the wrong commit/version/artifact/hardware/policy
    digest must be rejected, never counted."""
    from bc250_llm_mode.release_evidence import validate_evidence_record

    record = {
        "evidence_schema_version": 1,
        "evidence_id": "ev-1",
        "kind": "DEFAULT_TEST_SUITE",
        "release_candidate_version": "0.9.0.dev0",  # wrong version
        "source_commit": "0" * 40,
        "result": "PASS",
    }
    ok, reason = validate_evidence_record(
        record, candidate_version="1.0.0", source_commit="f" * 40)
    assert ok is False
    assert reason  # a stable rejection reason is required


def test_red_bad_evidence_states_are_rejected():
    """RED #7: unknown/expired/failed/inconclusive/duplicated/superseded
    evidence must never satisfy a requirement."""
    from bc250_llm_mode.release_evidence import validate_evidence_record

    base = {
        "evidence_schema_version": 1,
        "evidence_id": "ev-1",
        "kind": "DEFAULT_TEST_SUITE",
        "release_candidate_version": "1.0.0",
        "source_commit": "f" * 40,
        "result": "FAIL",  # failed evidence
    }
    ok, reason = validate_evidence_record(
        base, candidate_version="1.0.0", source_commit="f" * 40)
    assert ok is False and reason

    unknown = dict(base, kind="NOT_A_REAL_KIND", result="PASS")
    ok2, reason2 = validate_evidence_record(
        unknown, candidate_version="1.0.0", source_commit="f" * 40)
    assert ok2 is False and reason2


def test_red_limitation_without_acceptance_record_blocks_release():
    """RED #9: an accepted limitation without a reviewed acceptance record
    blocks release. G1: limitations derive from the reviewed policy alone
    (model-conversion is classified there); no caller-declared list."""
    decision = _evaluate_empty("1.0.0")
    assert decision.eligible_for_1_0_0 is False
    assert "LIMITATION_ACCEPTANCE_MISSING" in decision.blocking_codes


def test_red_strict_checkout_rejects_untracked_build_inputs(tmp_path):
    """RED #10: a strict release-checkout check rejects untracked inputs in the
    build workspace WITHOUT deleting the developer's unrelated files."""
    from bc250_llm_mode.release_gate import check_release_checkout

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tracked.py").write_text("x = 1\n")
    (workspace / "stray_untracked.txt").write_text("local scratch\n")

    result = check_release_checkout(
        workspace, tracked_files=["tracked.py"])
    assert result.ok is False
    assert "stray_untracked.txt" in result.untracked
    # The developer's file must NOT be deleted by the check.
    assert (workspace / "stray_untracked.txt").exists()
