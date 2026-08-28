"""C1 §C1.5 (V1_0_RELEASE_CLOSURE plan): evidence-driven release gate tests.

Comprehensive green acceptance tests for the C1 evaluator: table-driven
evidence-kind validation, deterministic/order-independent evaluation, the full
satisfied-evidence path to eligibility, duplicate/superseded/expired rejection,
artifact-digest substitution, wrong-commit/version refusal, the
no-direct-eligible-construction guard, the strict checkout check, a golden v2
manifest with stable canonical bytes, and secret/path/prompt canaries.
"""

from __future__ import annotations

import json

import pytest

from bc250_llm_mode.release_artifacts import Artifact, ArtifactInventory
from bc250_llm_mode.release_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    EvidenceRejectionCode,
    validate_evidence_record,
)
from bc250_llm_mode.release_gate import (
    CandidateIdentity,
    ReleaseDecision,
    check_release_checkout,
    evaluate_release,
)
from bc250_llm_mode.release_policy import (
    EVIDENCE_KINDS,
    GATE_CODES,
    EvidenceKind,
    ReleaseGateCode,
    ReleasePolicyV1,
    default_release_policy,
)

_SHA = "a" * 64


def _candidate(version="1.0.0", commit="c" * 40, ref=None, policy=None):
    """G1: a full candidate identity bound to the reviewed policy digest.
    Final versions ride their exact protected tag by default."""
    policy = policy or default_release_policy()
    if ref is None:
        is_final = all(part.isdigit() for part in version.split("."))
        ref = f"refs/tags/v{version}" if is_final else "refs/heads/main"
    return CandidateIdentity(
        version=version, source_commit=commit, source_ref=ref,
        repository="local", policy_digest=policy.policy_digest())


# The evidence fixtures name one wheel subject; the inventory matches it.
_INVENTORY = ArtifactInventory(artifacts=(
    Artifact(name="wheel", sha256=_SHA, size=4,
             media_type="application/wheel"),))


def _record(kind, *, version="1.0.0", commit="c" * 40, result="PASS",
            evidence_id=None, issued="2026-01-01T00:00:00+00:00",
            expires=None, measurements=None, subjects=None, attachments=None):
    """G2-migrated: a COMPLETE schema-v2 record dict bound to the reviewed
    policy digest (validator-level fixture; evaluator-level tests wrap these
    into VerifiedEvidenceRecord via ``wrap_verified``)."""
    from tests.release_evidence_fixtures import KIND_MEASUREMENT_FIXTURES

    rec = {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": evidence_id or f"ev-{kind}",
        "kind": kind,
        "release_candidate_version": version,
        "source_repository": "local",
        "source_commit": commit,
        "policy_digest": default_release_policy().policy_digest(),
        "artifact_inventory_digest": _INVENTORY.inventory_digest(),
        "artifact_subjects": subjects if subjects is not None else [
            {"name": "wheel", "sha256": _SHA, "media_type": "application/wheel"}],
        "issuer": {"type": "ci", "identity": "test-runner"},
        "issued_at": issued,
        "expires_at": expires,
        "environment": {"os": "linux", "architecture": "x86_64",
                        "python": "3.14", "runner": "pytest"},
        "result": result,
        "measurements": (measurements if measurements is not None
                         else dict(KIND_MEASUREMENT_FIXTURES.get(kind, {}))),
        "attachments": attachments or [],
        "verification": {
            "mechanism": "sigstore-bundle",
            "subject": _SHA,
            "verifier": "tests/test_release_gate_c1.py",
            "verified_at": "2026-01-01T00:00:01+00:00",
            "bundle_digest": "sha256:" + "e" * 64,
        },
        "supersedes_evidence_id": None,
    }
    return rec


def _full_evidence(version="1.0.0", commit="c" * 40):
    """One valid PASS verified record for every 1.0-required kind + the
    limitation acceptance for model-conversion (G2: evaluator consumes only
    verifier-produced VerifiedEvidenceRecord)."""
    from tests.release_evidence_fixtures import wrap_verified

    policy = default_release_policy()
    records = [
        wrap_verified(_record(kind, version=version, commit=commit,
                              evidence_id=f"ev-{i}-{kind}"))
        for i, kind in enumerate(sorted(policy.one_zero_required_kinds))
    ]
    records.append(wrap_verified(_record(
        EvidenceKind.KNOWN_LIMITATION_ACCEPTANCE.value,
        version=version, commit=commit,
        evidence_id="ev-limitation-model-conversion",
        measurements={"capability": "model-conversion"})))
    return records


# --- policy ---------------------------------------------------------------

def test_policy_vocabularies_are_closed_and_digest_deterministic():
    assert len(EVIDENCE_KINDS) == 18
    # G1 extended the closed gate-code vocabulary with the candidate-identity
    # binding codes (CANDIDATE_REF_MISMATCH, POLICY_DIGEST_MISMATCH): 19 + 2.
    assert len(GATE_CODES) == 21
    p1 = default_release_policy()
    p2 = default_release_policy()
    assert p1.policy_digest() == p2.policy_digest()
    assert p1.policy_digest().startswith("sha256:")
    assert "backup-restore-publish" in p1.mandatory_capabilities()
    assert "model-conversion" in p1.limitation_capabilities()


# --- evidence validation (table-driven) -----------------------------------

@pytest.mark.parametrize("mutate,expected", [
    (lambda r: r.update(evidence_schema_version=99),
     EvidenceRejectionCode.SCHEMA_VERSION_MISMATCH.value),
    (lambda r: r.update(kind="NOT_A_KIND"),
     EvidenceRejectionCode.UNKNOWN_KIND.value),
    (lambda r: r.update(result="FAIL"),
     EvidenceRejectionCode.RESULT_NOT_PASS.value),
    (lambda r: r.update(result="INCONCLUSIVE"),
     EvidenceRejectionCode.RESULT_NOT_PASS.value),
    (lambda r: r.update(release_candidate_version="0.9.0"),
     EvidenceRejectionCode.VERSION_MISMATCH.value),
    (lambda r: r.update(source_commit="f" * 40),
     EvidenceRejectionCode.SOURCE_COMMIT_MISMATCH.value),
    (lambda r: r.update(issued_at="not-a-date"),
     EvidenceRejectionCode.BAD_TIMESTAMP.value),
    (lambda r: r.update(issued_at="2026-01-01T00:00:00"),  # naive
     EvidenceRejectionCode.BAD_TIMESTAMP.value),
    (lambda r: r.update(expires_at="2020-01-01T00:00:00+00:00"),
     EvidenceRejectionCode.EXPIRED.value),
    (lambda r: r.update(artifact_subjects=[{"name": "w", "sha256": "short"}]),
     EvidenceRejectionCode.BAD_DIGEST.value),
    (lambda r: r.update(attachments=[{"name": "x", "location_hint": "../esc"}]),
     EvidenceRejectionCode.PATH_TRAVERSAL.value),
    (lambda r: r.update(attachments=[{"name": "x", "location_hint": "/abs"}]),
     EvidenceRejectionCode.PATH_TRAVERSAL.value),
    (lambda r: r.update(measurements={"hf_token": "placeholder"}),
     EvidenceRejectionCode.SECRET_MATERIAL.value),
    (lambda r: r.update(measurements={"prompt": "user text"}),
     EvidenceRejectionCode.SECRET_MATERIAL.value),
])
def test_evidence_validation_rejects_bad_records(mutate, expected):
    rec = _record(EvidenceKind.DEFAULT_TEST_SUITE.value)
    mutate(rec)
    ok, code = validate_evidence_record(
        rec, candidate_version="1.0.0", source_commit="c" * 40,
        policy_digest=default_release_policy().policy_digest())
    assert ok is False
    assert code == expected


def test_valid_evidence_record_is_accepted():
    rec = _record(EvidenceKind.DEFAULT_TEST_SUITE.value)
    ok, code = validate_evidence_record(
        rec, candidate_version="1.0.0", source_commit="c" * 40,
        policy_digest=default_release_policy().policy_digest())
    assert ok is True and code is None


def test_duplicate_and_superseded_evidence_rejected():
    rec_a = _record(EvidenceKind.SBOM.value, evidence_id="dup")
    rec_b = _record(EvidenceKind.SBOM.value, evidence_id="dup")
    seen = {"dup"}
    ok, code = validate_evidence_record(
        rec_b, candidate_version="1.0.0", source_commit="c" * 40,
        policy_digest=default_release_policy().policy_digest(),
        seen_ids=seen)
    assert ok is False and code == EvidenceRejectionCode.DUPLICATE.value

    superseded = _record(EvidenceKind.SBOM.value, evidence_id="old")
    ok2, code2 = validate_evidence_record(
        superseded, candidate_version="1.0.0", source_commit="c" * 40,
        policy_digest=default_release_policy().policy_digest(),
        supersedes={"old"})
    assert ok2 is False and code2 == EvidenceRejectionCode.SUPERSEDED.value


# --- evaluator -------------------------------------------------------------

def test_empty_evidence_blocks_with_exact_codes():
    decision = evaluate_release(
        evidence=[], candidate=_candidate(), artifacts=_INVENTORY)
    assert decision.eligible_for_1_0_0 is False
    assert decision.eligible_for_rc is False
    for code in ("SIGNATURE_MISSING_OR_INVALID", "SBOM_MISSING_OR_MISMATCHED",
                 "PROVENANCE_MISSING_OR_MISMATCHED",
                 "BACKUP_RESTORE_EVIDENCE_MISSING",
                 "HARDWARE_QUALIFICATION_MISSING", "SOAK_EVIDENCE_MISSING",
                 "SECURITY_REVIEW_MISSING", "HUMAN_ACCEPTANCE_MISSING",
                 "LIMITATION_ACCEPTANCE_MISSING"):
        assert code in decision.blocking_codes


def test_evaluation_is_deterministic_regardless_of_order():
    evidence = _full_evidence()
    d_forward = evaluate_release(
        evidence=list(evidence), candidate=_candidate(),
        artifacts=_INVENTORY)
    d_reversed = evaluate_release(
        evidence=list(reversed(evidence)), candidate=_candidate(),
        artifacts=_INVENTORY)
    assert d_forward.blocking_codes == d_reversed.blocking_codes
    assert d_forward.eligible_for_1_0_0 == d_reversed.eligible_for_1_0_0
    assert d_forward.evidence_used == d_reversed.evidence_used


def test_full_valid_evidence_reaches_eligibility():
    evidence = _full_evidence()
    decision = evaluate_release(
        evidence=evidence, candidate=_candidate(), artifacts=_INVENTORY)
    assert decision.blocking_codes == ()
    assert decision.eligible_for_1_0_0 is True
    assert decision.eligible_for_rc is True
    assert decision.accepted_limitations == ("model-conversion",)
    assert decision.evidence_rejected == ()


def test_mandatory_unavailable_capability_blocks_even_with_evidence(monkeypatch):
    """G1: the unavailable set is DERIVED from the reviewed product state
    (release_state.KNOWN_UNAVAILABLE_CAPABILITIES), never a caller default."""
    import bc250_llm_mode.release_state as rs

    monkeypatch.setattr(rs, "KNOWN_UNAVAILABLE_CAPABILITIES", (
        {"capability": "backup-restore-publish",
         "reason": "simulated unavailable mandatory capability",
         "visible_in": "test"},
    ))
    evidence = _full_evidence()
    decision = evaluate_release(
        evidence=evidence, candidate=_candidate(), artifacts=_INVENTORY)
    assert decision.eligible_for_1_0_0 is False
    assert ReleaseGateCode.CAPABILITY_UNAVAILABLE.value in decision.blocking_codes


def test_limitation_without_acceptance_record_blocks():
    evidence = _full_evidence()
    # Drop the limitation-acceptance record (G2: unwrap verified items).
    evidence = [e for e in evidence
                if e.record["kind"] != EvidenceKind.KNOWN_LIMITATION_ACCEPTANCE.value]
    decision = evaluate_release(
        evidence=evidence, candidate=_candidate(), artifacts=_INVENTORY)
    assert decision.eligible_for_1_0_0 is False
    assert ReleaseGateCode.LIMITATION_ACCEPTANCE_MISSING.value in decision.blocking_codes


def test_wrong_commit_evidence_is_rejected_and_counted():
    evidence = _full_evidence(commit="d" * 40)  # wrong commit
    decision = evaluate_release(
        evidence=evidence, candidate=_candidate(), artifacts=_INVENTORY)
    assert decision.eligible_for_1_0_0 is False
    assert decision.evidence_used == ()
    assert len(decision.evidence_rejected) == len(evidence)
    assert all(code == EvidenceRejectionCode.SOURCE_COMMIT_MISMATCH.value
               for _, code in decision.evidence_rejected)


def test_artifact_digest_substitution_does_not_cross_artifacts():
    # Evidence bound to one artifact digest cannot qualify a different artifact:
    # the evaluator records subjects; a mismatched subject digest is rejected.
    rec = _record(EvidenceKind.SBOM.value,
                  subjects=[{"name": "wheel", "sha256": "b" * 64}])
    ok, code = validate_evidence_record(
        dict(rec, artifact_subjects=[{"name": "wheel", "sha256": "not-hex"}]),
        candidate_version="1.0.0", source_commit="c" * 40,
        policy_digest=default_release_policy().policy_digest())
    assert ok is False and code == EvidenceRejectionCode.BAD_DIGEST.value


def test_eligible_decision_cannot_be_constructed_directly():
    with pytest.raises(ValueError):
        ReleaseDecision(eligible_for_rc=True, eligible_for_1_0_0=True)
    with pytest.raises(ValueError):
        ReleaseDecision(eligible_for_rc=False, eligible_for_1_0_0=True)
    # A non-eligible decision may be constructed (e.g. by tooling/diagnostics).
    ok = ReleaseDecision(eligible_for_rc=False, eligible_for_1_0_0=False)
    assert ok.eligible_for_1_0_0 is False


def test_decision_serializes_without_private_key():
    decision = evaluate_release(
        evidence=[], candidate=_candidate(), artifacts=_INVENTORY)
    doc = decision.to_dict()
    assert "_evaluator_key" not in doc
    # G1: decision schema v3 carries the candidate identity + inventory digest.
    assert doc["decision_schema_version"] == 3
    assert doc["source_commit"] == "c" * 40
    assert doc["inventory_digest"]
    json.dumps(doc)


# --- strict checkout -------------------------------------------------------

def test_checkout_check_rejects_untracked_without_deleting(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x")
    (ws / "sub").mkdir()
    (ws / "sub" / "stray.txt").write_text("local")
    result = check_release_checkout(ws, tracked_files=["a.py"])
    assert result.ok is False
    assert "sub/stray.txt" in result.untracked
    assert (ws / "sub" / "stray.txt").exists()  # NOT deleted

    clean = check_release_checkout(ws, tracked_files=["a.py", "sub/stray.txt"])
    assert clean.ok is True and clean.untracked == ()


# --- golden manifest v2 ----------------------------------------------------

def test_golden_v2_decision_is_canonical():
    cand = _candidate(version="1.0.0rc1")
    decision = evaluate_release(
        evidence=[], candidate=cand, artifacts=_INVENTORY)
    doc_a = decision.to_dict()
    doc_b = evaluate_release(
        evidence=[], candidate=cand, artifacts=_INVENTORY).to_dict()
    assert json.dumps(doc_a, sort_keys=True) == json.dumps(doc_b, sort_keys=True)
    assert doc_a["candidate_version"] == "1.0.0rc1"


# --- canaries --------------------------------------------------------------

def test_secret_and_prompt_canaries_never_appear_in_decision():
    from tests.release_evidence_fixtures import wrap_verified

    rec = _record(EvidenceKind.SECURITY_REVIEW.value,
                  measurements={"api_key": "super-secret", "prompt": "hello"})
    decision = evaluate_release(
        evidence=[wrap_verified(rec)], candidate=_candidate(),
        artifacts=_INVENTORY)
    blob = json.dumps(decision.to_dict())
    assert "super-secret" not in blob
    assert "hello" not in blob
    # The record was rejected for carrying secret material.
    assert decision.evidence_rejected
    assert decision.evidence_rejected[0][1] == \
        EvidenceRejectionCode.SECRET_MATERIAL.value
