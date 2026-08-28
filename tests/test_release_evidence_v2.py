"""G0.2 (§6, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): intentionally-RED
evidence-schema-v2 tests.

These freeze audit findings 4-7 (§1) and the §13.2 evidence matrix as failing
gates BEFORE any production change. G2 turns them green with the schema-v2
envelope (every mandatory field present, bounded, candidate-bound), value-based
secret detection, kind-specific semantic contracts, and the raw/validated/
verified type boundary.

Two layers, both RED at G0:

  * Layer A (behavioral): records the CURRENT schema-v1 validator ACCEPTS
    although they lack mandatory fields or carry secret-like values. They fail
    with AssertionError today and pass after G2 once such records are rejected
    with any stable code.
  * Layer B (contract): complete schema-v2 records with exactly one defect.
    Today the current validator rejects them for the WRONG reason
    (SCHEMA_VERSION_MISMATCH), so the exact-code assertions fail; after G2 each
    defect is rejected with its intended stable code.

Validation order pinned for G2 (so exact codes are deterministic): schema
version, unknown fields, mandatory-field presence, types, empty content,
kind vocabulary, result, candidate version, commit binding, policy-digest
binding, timestamps, secret scan (keys AND values), bounds, attachment
containment, subject digest format, issuer/environment/verification structure,
kind-specific measurement contract.
"""

from __future__ import annotations

import pytest

from bc250_llm_mode.release_evidence import validate_evidence_record

# Consistent candidate binding for every record in this file.
VERSION = "1.0.0rc1"
COMMIT = "c" * 40
POLICY_DIGEST = "sha256:" + "a" * 64
INVENTORY_DIGEST = "sha256:" + "b" * 64
WHEEL_SHA = "a" * 64

# The 18 mandatory schema-v2 envelope fields (plan §G2.1). ``expires_at`` and
# ``supersedes_evidence_id`` may be explicit null, but the KEYS must exist.
MANDATORY_FIELDS = (
    "evidence_schema_version", "evidence_id", "kind",
    "release_candidate_version", "source_repository", "source_commit",
    "policy_digest", "artifact_inventory_digest", "artifact_subjects",
    "issuer", "issued_at", "expires_at", "environment", "result",
    "measurements", "attachments", "verification", "supersedes_evidence_id",
)


def _v2_record(**overrides):
    """A COMPLETE schema-v2 evidence record (plan §G2.1) for the
    DEFAULT_TEST_SUITE kind."""
    rec = {
        "evidence_schema_version": 2,
        "evidence_id": "ev-test-0001",
        "kind": "DEFAULT_TEST_SUITE",
        "release_candidate_version": VERSION,
        "source_repository": "local",
        "source_commit": COMMIT,
        "policy_digest": POLICY_DIGEST,
        "artifact_inventory_digest": INVENTORY_DIGEST,
        "artifact_subjects": [{
            "name": "bc250_llm_mode-1.0.0rc1-py3-none-any.whl",
            "sha256": WHEEL_SHA,
            "media_type": "application/vnd.pypi.wheel.v1",
        }],
        "issuer": {"type": "ci", "identity": "test-runner"},
        "issued_at": "2026-01-01T00:00:00+00:00",
        "expires_at": None,
        "environment": {"os": "linux", "architecture": "x86_64",
                        "python": "3.14", "runner": "pytest"},
        "result": "PASS",
        "measurements": {"collected": 1064, "passed": 1062, "skipped": 2},
        "attachments": [],
        "verification": {
            "mechanism": "sigstore-bundle",
            "subject": WHEEL_SHA,
            "verifier": "tools/release/attestation.py",
            "verified_at": "2026-01-01T00:00:01+00:00",
            "bundle_digest": "sha256:" + "d" * 64,
        },
        "supersedes_evidence_id": None,
    }
    rec.update(overrides)
    return rec


def _v1_bare_record(kind="SECURITY_REVIEW"):
    """The minimal record the CURRENT (schema v1) validator accepts: no
    evidence_id, issuer, subjects, signature/verification, environment,
    source_commit, or policy binding (audit finding 3/4)."""
    return {
        "evidence_schema_version": 1,
        "kind": kind,
        "result": "PASS",
        "release_candidate_version": VERSION,
        "issued_at": "2026-01-01T00:00:00+00:00",
    }


def _validate(rec, **kwargs):
    """Call the validator the way the audited tooling does — with NO source
    commit or policy binding unless the test supplies them (that unbound path
    is exactly the hole the audit found). After G2 the binding inputs become
    mandatory keyword arguments; the retry keeps these tests meaningful across
    the signature change."""
    defaults = dict(candidate_version=VERSION)
    defaults.update(kwargs)
    try:
        return validate_evidence_record(rec, **defaults)
    except TypeError:
        # Remediated (G2) signature: commit + policy binding are mandatory.
        defaults.setdefault("source_commit", COMMIT)
        defaults.setdefault("policy_digest", POLICY_DIGEST)
        return validate_evidence_record(rec, **defaults)


# --- Layer A: records accepted today must be rejected ------------------------

@pytest.mark.parametrize("kind", [
    "SECURITY_REVIEW", "HUMAN_ACCEPTANCE", "SOAK_TEST", "RELEASE_APPROVAL"])
def test_red_bare_record_missing_mandatory_fields_is_rejected(kind):
    """Audit finding 4: a record with no evidence_id, issuer, artifact
    subjects, verification, environment, or commit binding is ACCEPTED by the
    current validator. After G2 it must be rejected with a stable code."""
    ok, code = _validate(_v1_bare_record(kind))
    assert ok is False and code, (
        f"a field-less {kind} record must never be accepted")


def test_red_token_like_value_under_benign_key_is_rejected():
    """Audit finding 7: secret checks currently match KEY NAMES only, so a
    Hugging Face token under an innocuous key is accepted."""
    rec = _v1_bare_record("SECURITY_REVIEW")
    rec["measurements"] = {"note": "hf_abcdefghijklmnopqrstuvwxyz1234567890"}
    ok, code = _validate(rec)
    assert ok is False and code, (
        "a token-like VALUE under a benign key must be rejected")


# --- Layer B: schema-v2 contract with exact rejection codes ------------------

@pytest.mark.parametrize("field", MANDATORY_FIELDS)
def test_red_each_mandatory_envelope_field_is_required(field):
    """§G2.1: omitting ANY mandatory top-level field refuses the record with
    MISSING_FIELD. No silent defaults."""
    rec = _v2_record()
    del rec[field]
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "MISSING_FIELD", (
        f"omitting {field!r} must refuse with MISSING_FIELD, got {code!r}")


def test_red_unknown_top_level_field_is_rejected():
    """§2.2/§G2.1: unknown evidence fields are never silently accepted."""
    rec = _v2_record(extra_field="surprise")
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "UNKNOWN_FIELD"


@pytest.mark.parametrize("value", [
    "hf_" + "a" * 34,                                  # Hugging Face token
    "ghp_" + "B" * 36,                                 # GitHub PAT
    "-----BEGIN RSA PRIVATE KEY-----",                 # PEM private key
    "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",         # bearer credential
    "https://user:passw0rd@host.example/private",      # URL userinfo secret
])
def test_red_credential_patterns_in_values_are_secret_material(value):
    """§G2.5: credential/token patterns in string VALUES are rejected as
    SECRET_MATERIAL even under benign keys (secret scan runs before the
    kind-contract check)."""
    rec = _v2_record(measurements={"collected": 1, "passed": 1,
                                   "note": value})
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "SECRET_MATERIAL", (
        f"value pattern must be refused as SECRET_MATERIAL, got {code!r}")


def test_red_empty_artifact_subjects_rejected_for_artifact_bound_kinds():
    """Audit finding 5: release-required artifact-bound kinds refuse an empty
    artifact_subjects list with EMPTY_FIELD."""
    for kind in ("CLEAN_WHEEL_SMOKE", "SBOM", "BUILD_PROVENANCE",
                 "ARTIFACT_ATTESTATION", "RELEASE_APPROVAL"):
        rec = _v2_record(kind=kind, evidence_id=f"ev-{kind}",
                         artifact_subjects=[])
        ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
        assert ok is False and code == "EMPTY_FIELD", (
            f"{kind} with empty subjects must refuse EMPTY_FIELD, got {code!r}")


def test_red_missing_verification_block_is_missing_field():
    """Audit finding 6a: a record without the verification block (signature/
    attestation receipt) is refused — the v1 'signature is an unverified
    string' model is gone."""
    rec = _v2_record()
    del rec["verification"]
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "MISSING_FIELD"


def test_red_unverified_attestation_block_is_rejected():
    """Audit finding 6b: a verification block that is present but empty/
    unverified (no mechanism, no bundle digest) is refused as BAD_VERIFICATION
    — a reference is never trusted merely because it is non-empty."""
    rec = _v2_record(verification={
        "mechanism": "", "subject": "", "verifier": "",
        "verified_at": None, "bundle_digest": ""})
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "BAD_VERIFICATION"


def test_red_future_dated_evidence_is_rejected():
    """§13.2: future-dated issued_at is a malformed timestamp."""
    rec = _v2_record(issued_at="2999-01-01T00:00:00+00:00")
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "BAD_TIMESTAMP"


def test_red_evidence_policy_digest_mismatch_rejected():
    """§13.2: evidence bound to a different policy digest than the evaluating
    candidate's policy is rejected (the validator takes the expected digest as
    a mandatory binding input after G2)."""
    rec = _v2_record(policy_digest="sha256:" + "f" * 64)
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "POLICY_DIGEST_MISMATCH"


def test_red_kind_contracts_enforce_semantic_payloads():
    """§G2.2: one generic PASS string must not satisfy semantically different
    evidence — DEFAULT_TEST_SUITE requires its measurement payload."""
    rec = _v2_record(measurements={})
    ok, code = _validate(rec, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "KIND_CONTRACT_UNMET"


def test_red_oversized_and_overdeep_records_are_rejected():
    """§G2.5: bounded records — oversized string values and excessive nesting
    are refused as RECORD_OVERSIZE."""
    huge = _v2_record(measurements={"collected": 1, "passed": 1,
                                    "blob": "x" * 200_000})
    ok, code = _validate(huge, policy_digest=POLICY_DIGEST)
    assert ok is False and code == "RECORD_OVERSIZE"

    deep_value: object = "leaf"
    for _ in range(60):  # nesting far beyond any bounded measurement
        deep_value = {"nested": deep_value}
    deep = _v2_record(measurements={"collected": 1, "passed": 1,
                                    "deep": deep_value})
    ok2, code2 = _validate(deep, policy_digest=POLICY_DIGEST)
    assert ok2 is False and code2 == "RECORD_OVERSIZE"


# --- §G2.3/§G2.6: verified-evidence boundary and supersession ----------------

def test_red_validated_but_unverified_evidence_cannot_qualify():
    """§13.1: a COMPLETE set of merely parsed/validated dict records can never
    qualify a release — only verifier-produced VerifiedEvidenceRecord satisfies
    a gate. After G2 the evaluator rejects raw dicts with a stable code and the
    candidate stays blocked."""
    from bc250_llm_mode.release_artifacts import ArtifactInventory
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy

    policy = default_release_policy()
    candidate = CandidateIdentity(
        version=VERSION, source_commit=COMMIT,
        source_ref="refs/heads/main", repository="local",
        policy_digest=policy.policy_digest())
    raw_records = [
        _v2_record(kind=kind, evidence_id=f"ev-{i}-{kind}")
        for i, kind in enumerate(sorted(policy.one_zero_required_kinds))]
    decision = evaluate_release(
        evidence=raw_records, candidate=candidate,
        artifacts=ArtifactInventory(), policy=policy)
    assert decision.eligible_for_1_0_0 is False
    assert decision.eligible_for_rc is False
    assert decision.evidence_used == (), (
        "raw/validated dict records must never be consumed as verified evidence")


def test_red_supersession_must_target_known_same_kind_records():
    """§G2.6: supersession targets must exist in the candidate set, be the same
    kind, and form no cycle. Set-level validation refuses all three violations."""
    from bc250_llm_mode.release_evidence import validate_evidence_set

    base = _v2_record(kind="SBOM", evidence_id="ev-old")
    # (a) unknown supersession target
    unknown = _v2_record(kind="SBOM", evidence_id="ev-new",
                         supersedes_evidence_id="ev-never-existed")
    ok, problems = validate_evidence_set([base, unknown])
    assert ok is False and any("SUPERSEDED_UNKNOWN_TARGET" in p for p in problems)

    # (b) cross-kind supersession
    cross = _v2_record(kind="SOAK_TEST", evidence_id="ev-cross",
                       supersedes_evidence_id="ev-old")
    ok2, problems2 = validate_evidence_set([base, cross])
    assert ok2 is False and any("SUPERSESSION_INVALID" in p for p in problems2)

    # (c) cycle: x supersedes y, y supersedes x
    x = _v2_record(kind="SBOM", evidence_id="ev-x",
                   supersedes_evidence_id="ev-y")
    y = _v2_record(kind="SBOM", evidence_id="ev-y",
                   supersedes_evidence_id="ev-x")
    ok3, problems3 = validate_evidence_set([x, y])
    assert ok3 is False and any("SUPERSESSION_CYCLE" in p for p in problems3)
