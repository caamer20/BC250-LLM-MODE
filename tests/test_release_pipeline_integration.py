"""G3 §17 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): the isolated release
FIXTURE pipeline, end to end, in-process.

Runs the full remediated chain over synthetic artifacts with a TEST-ONLY trust
root (never real release evidence, never committed as such):

  build once -> inventory v2 -> SBOM -> validate (CLI) -> attest fixture ->
  verify attestation -> evaluate -> manifest v3 -> full verify (CLI)

and the negative twin: a tampered attestation bundle keeps the evaluator
blocked. The real-wheel install + smoke step of the §17 chain is covered by
the existing slow clean-wheel gate (``tests/test_packaging.py``); this test
pins the evidence/pipeline chain that wraps it.
"""

from __future__ import annotations

import json

import pytest

from tools.release.__main__ import main as release_main

VERSION = "1.0.0rc1"
COMMIT = "c" * 40
WHEEL_NAME = f"bc250_llm_mode-{VERSION}-py3-none-any.whl"


def _build_once(dist):
    """Build once: wheel + sdist + checksums + SBOM (synthetic bytes)."""
    from tools.release.artifacts import sha256_file
    from tools.release.sbom import build_sbom

    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / WHEEL_NAME
    wheel.write_bytes(b"fixture-wheel-bytes")
    sdist = dist / f"bc250_llm_mode-{VERSION}.tar.gz"
    sdist.write_bytes(b"fixture-sdist-bytes")
    wheel_sha = sha256_file(wheel)
    sdist_sha = sha256_file(sdist)
    (dist / "checksums.sha256").write_text(
        f"{wheel_sha}  {WHEEL_NAME}\n{sdist_sha}  {sdist.name}\n")
    sbom = build_sbom(
        package_name="bc250-llm-mode", package_version=VERSION,
        dependencies=[("httpx", ">=0.27")], subject_sha256=wheel_sha)
    (dist / "sbom.cdx.json").write_text(json.dumps(sbom, sort_keys=True))
    return wheel_sha


def _attest_fixture(record, bundle_path):
    """TEST-ONLY attestation: canonical bundle over the record's verification
    subject. This is a fixture trust root — never real release evidence."""
    from bc250_llm_mode.release_evidence import bundle_digest_of

    payload = {"subject": record["verification"]["subject"],
               "evidence_id": record["evidence_id"]}
    bundle = {"mechanism": "sigstore-bundle",
              "subject": record["verification"]["subject"],
              "payload": payload,
              "bundle_digest": bundle_digest_of(payload)}
    bundle_path.write_text(json.dumps(bundle, sort_keys=True))
    return bundle


def test_isolated_release_fixture_pipeline_end_to_end(tmp_path, capsys):
    from bc250_llm_mode.release_evidence import verify_evidence_attestation
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_manifest import build_release_manifest
    from bc250_llm_mode.release_policy import default_release_policy
    from tests.release_evidence_fixtures import make_verified_record
    from tools.release.artifacts import build_inventory

    # 1. Build once.
    dist = tmp_path / "dist"
    wheel_sha = _build_once(dist)

    # 2. Inventory v2 (roles + canonical digest).
    inventory = build_inventory(dist)
    assert inventory.to_dict()["inventory_schema_version"] == 2
    roles = {a.role for a in inventory.artifacts}
    assert {"python-wheel", "python-sdist", "checksums",
            "cyclonedx-sbom"} <= roles

    # 3. Candidate + policy.
    policy = default_release_policy()
    candidate = CandidateIdentity(
        version=VERSION, source_commit=COMMIT, source_ref="refs/heads/main",
        repository="local", policy_digest=policy.policy_digest())

    # 4. Attest fixture -> verify attestation (the Raw/Validated/Verified
    #    promotion path) for an artifact-bound SBOM record.
    record = make_verified_record(
        "SBOM", candidate=candidate, inventory=inventory).record
    record["verification"]["subject"] = wheel_sha
    record["artifact_subjects"] = [
        {"name": WHEEL_NAME, "sha256": wheel_sha,
         "media_type": "application/octet-stream"}]
    bundle = _attest_fixture(record, tmp_path / "sbom.bundle.json")
    verified = verify_evidence_attestation(
        record, bundle=bundle, candidate_version=VERSION,
        source_commit=COMMIT, policy_digest=policy.policy_digest())

    # 5. Evaluate with the verified record: consumed, but the candidate stays
    #    ineligible (the other required kinds are absent) — honest result.
    decision = evaluate_release(
        evidence=[verified], candidate=candidate, artifacts=inventory,
        policy=policy)
    assert verified.record["evidence_id"] in decision.evidence_used
    assert decision.eligible_for_1_0_0 is False

    # 6. Manifest v3 draft: BLOCKED, bound to candidate + inventory.
    manifest = build_release_manifest(
        decision=decision, inventory=inventory, sbom_digest=None)
    assert manifest["release_status"] == "BLOCKED"
    manifest_path = dist / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))

    # 7. Full verify over the complete release set: green.
    capsys.readouterr()
    rc = release_main(["verify", str(manifest_path), str(dist)])
    assert rc == 0
    assert WHEEL_NAME in capsys.readouterr().out


def test_isolated_pipeline_tampered_bundle_keeps_candidate_blocked(tmp_path):
    from bc250_llm_mode.release_evidence import (
        EvidenceVerificationError, verify_evidence_attestation)
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy
    from tests.release_evidence_fixtures import make_verified_record
    from tools.release.artifacts import build_inventory

    dist = tmp_path / "dist"
    wheel_sha = _build_once(dist)
    inventory = build_inventory(dist)
    policy = default_release_policy()
    candidate = CandidateIdentity(
        version=VERSION, source_commit=COMMIT, source_ref="refs/heads/main",
        repository="local", policy_digest=policy.policy_digest())

    record = make_verified_record(
        "SBOM", candidate=candidate, inventory=inventory).record
    record["verification"]["subject"] = wheel_sha
    bundle = _attest_fixture(record, tmp_path / "sbom.bundle.json")

    # Tamper with the bundle payload after attestation.
    doc = json.loads((tmp_path / "sbom.bundle.json").read_text())
    doc["payload"]["subject"] = "f" * 64
    (tmp_path / "sbom.bundle.json").write_text(json.dumps(doc, sort_keys=True))

    with pytest.raises(EvidenceVerificationError):
        verify_evidence_attestation(
            record, bundle=doc, candidate_version=VERSION,
            source_commit=COMMIT, policy_digest=policy.policy_digest())

    # Without a verified record the candidate stays blocked.
    decision = evaluate_release(
        evidence=[record], candidate=candidate, artifacts=inventory,
        policy=policy)
    assert decision.evidence_used == ()
    assert decision.eligible_for_rc is False
