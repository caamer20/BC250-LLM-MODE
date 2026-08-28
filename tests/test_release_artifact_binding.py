"""G0.2 (§6, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): intentionally-RED
artifact-binding tests.

These freeze audit findings 5, 6, 8 (§1: ignored ``--artifacts``,
verify-without-comparison, SBOM subject never compared with the actual wheel)
plus the §13.3/§13.4 artifact/manifest/CLI matrix as failing gates BEFORE any
production change. G3 turns them green: artifact inventory v2 with roles and a
canonical digest, evidence subject binding (ARTIFACT_SUBJECT_MISMATCH),
decision-derived manifest v3, a ``tools.release verify`` that performs full
integrity checks, and SBOM validation against the exact built wheel.

RED reasons at G0: ImportError/AttributeError (the remediated modules and
inventory/manifest APIs do not exist yet), SystemExit (the remediated CLI shape
does not exist yet), or AssertionError (verify currently accepts anything).
"""

from __future__ import annotations

import json

import pytest

VERSION = "1.0.0rc1"
COMMIT = "c" * 40
WHEEL_NAME = f"bc250_llm_mode-{VERSION}-py3-none-any.whl"
SDIST_NAME = f"bc250_llm_mode-{VERSION}.tar.gz"
WHEEL_BYTES = b"synthetic-wheel-bytes-for-binding-tests"
SDIST_BYTES = b"synthetic-sdist-bytes-for-binding-tests"


def _candidate(policy):
    from bc250_llm_mode.release_gate import CandidateIdentity
    return CandidateIdentity(
        version=VERSION, source_commit=COMMIT,
        source_ref="refs/heads/main", repository="local",
        policy_digest=policy.policy_digest())


def _write_build_outputs(dist, *, sbom_subject_override=None):
    """Write wheel + sdist + checksums + SBOM into ``dist`` and return the
    (wheel_sha, sdist_sha) pair. Uses only APIs that exist at G0."""
    from tools.release.artifacts import sha256_file
    from tools.release.sbom import build_sbom

    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / WHEEL_NAME
    wheel.write_bytes(WHEEL_BYTES)
    sdist = dist / SDIST_NAME
    sdist.write_bytes(SDIST_BYTES)
    wheel_sha = sha256_file(wheel)
    sdist_sha = sha256_file(sdist)
    (dist / "checksums.sha256").write_text(
        f"{wheel_sha}  {WHEEL_NAME}\n{sdist_sha}  {SDIST_NAME}\n")
    sbom = build_sbom(
        package_name="bc250-llm-mode", package_version=VERSION,
        dependencies=[("httpx", ">=0.27")],
        subject_sha256=sbom_subject_override or wheel_sha)
    (dist / "sbom.cdx.json").write_text(json.dumps(sbom, sort_keys=True))
    return wheel_sha, sdist_sha


def _make_candidate_fixture(tmp_path, *, sbom_subject_override=None):
    """Full remediated (G3) candidate fixture: build outputs + inventory v2 +
    blocked draft manifest v3. Every import here is a G1/G3 deliverable, so at
    G0 each calling test fails with ImportError — the intended red reason."""
    from bc250_llm_mode.release_gate import evaluate_release
    from bc250_llm_mode.release_manifest import build_release_manifest
    from bc250_llm_mode.release_policy import default_release_policy
    from tools.release.artifacts import build_inventory

    dist = tmp_path / "dist"
    wheel_sha, sdist_sha = _write_build_outputs(
        dist, sbom_subject_override=sbom_subject_override)
    policy = default_release_policy()
    candidate = _candidate(policy)
    inventory = build_inventory(dist)  # inventory v2: roles + digest (G3)
    decision = evaluate_release(
        evidence=[], candidate=candidate, artifacts=inventory, policy=policy)
    manifest = build_release_manifest(
        decision=decision, inventory=inventory,
        sbom_digest=None)  # draft: BLOCKED, manifest excluded from itself
    manifest_path = dist / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    return dist, manifest_path, wheel_sha, sdist_sha


# --- audit finding 8 (§13.2): evidence subjects must bind the inventory ------

def test_red_evidence_subjects_must_match_candidate_inventory():
    """Audit finding 8: evidence whose subjects name digests absent from the
    candidate inventory is rejected (never counted), with a stable mismatch
    code — valid-looking but unrelated digests cannot qualify."""
    from bc250_llm_mode.release_artifacts import Artifact, ArtifactInventory
    from bc250_llm_mode.release_gate import evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy
    from tests.release_evidence_fixtures import make_verified_record

    policy = default_release_policy()
    candidate = _candidate(policy)
    inv_a = ArtifactInventory(artifacts=(
        Artifact(name=WHEEL_NAME, sha256="a" * 64, size=39,
                 media_type="application/vnd.pypi.wheel.v1"),))
    inv_b = ArtifactInventory(artifacts=(
        Artifact(name=WHEEL_NAME, sha256="b" * 64, size=39,
                 media_type="application/vnd.pypi.wheel.v1"),))
    # Verified against inventory A, evaluated against inventory B:
    rec = make_verified_record("SBOM", candidate=candidate, inventory=inv_a)
    decision = evaluate_release(
        evidence=[rec], candidate=candidate, artifacts=inv_b, policy=policy)
    assert decision.eligible_for_1_0_0 is False
    assert decision.evidence_used == ()
    rejected_codes = {code for _, code in decision.evidence_rejected}
    assert rejected_codes & {"INVENTORY_DIGEST_MISMATCH", "SUBJECT_MISMATCH",
                             "ARTIFACT_SUBJECT_MISMATCH"}, (
        f"subject substitution must be refused with a stable mismatch code, "
        f"got {rejected_codes!r}")


def test_red_verified_record_with_matching_inventory_is_consumed():
    """Control for the mismatch test: a verified record whose subjects equal
    the candidate inventory IS consumed (the binding check is precise, not a
    blanket refusal)."""
    from bc250_llm_mode.release_artifacts import Artifact, ArtifactInventory
    from bc250_llm_mode.release_gate import evaluate_release
    from bc250_llm_mode.release_policy import default_release_policy
    from tests.release_evidence_fixtures import make_verified_record

    policy = default_release_policy()
    candidate = _candidate(policy)
    inv = ArtifactInventory(artifacts=(
        Artifact(name=WHEEL_NAME, sha256="a" * 64, size=39,
                 media_type="application/vnd.pypi.wheel.v1"),))
    rec = make_verified_record("SBOM", candidate=candidate, inventory=inv)
    decision = evaluate_release(
        evidence=[rec], candidate=candidate, artifacts=inv, policy=policy)
    assert decision.evidence_used, (
        "a verified, subject-matching record must be consumed")
    assert "SBOM_MISSING_OR_MISMATCHED" not in decision.blocking_codes


# --- audit finding 5: --artifacts is required and used -----------------------

def test_red_cli_evaluate_requires_artifacts_flag(tmp_path):
    """Audit finding 5: ``tools.release evaluate`` currently parses but IGNORES
    ``--artifacts``. After G3, omitting --artifacts for RC/final evaluation is
    a usage error (exit 2)."""
    from tools.release.__main__ import main as release_main

    ev = tmp_path / "evidence"
    ev.mkdir()
    try:
        rc = release_main([
            "evaluate", "--candidate", VERSION, "--source-commit", COMMIT,
            "--evidence", str(ev)])
    except SystemExit as exc:
        rc = exc.code
    assert rc == 2, "--artifacts must be mandatory for release evaluation"


def test_red_cli_evaluate_binds_artifact_inventory(tmp_path, capsys):
    """Audit finding 5 (second half): the evaluation actually consumes the
    artifact directory — the JSON decision on stdout binds the inventory digest
    of the REAL files."""
    from tools.release.__main__ import main as release_main
    from tools.release.artifacts import sha256_file

    dist = tmp_path / "dist"
    _write_build_outputs(dist)
    ev = tmp_path / "evidence"
    ev.mkdir()
    try:
        release_main([
            "evaluate", "--candidate", VERSION, "--source-commit", COMMIT,
            "--evidence", str(ev), "--artifacts", str(dist), "--level", "rc"])
    except SystemExit as exc:
        pytest.fail(f"evaluate refused the remediated invocation (exit {exc.code})")
    out = capsys.readouterr().out
    doc = json.loads(out)  # after G3 stdout is ONLY the JSON decision
    assert doc.get("inventory_digest"), (
        "the decision must bind the artifact inventory digest")
    assert doc.get("source_commit") == COMMIT


# --- audit finding 6: verify must compare, not print -------------------------

def test_red_verify_green_on_consistent_candidate_fixture(tmp_path):
    """Baseline: a fully consistent manifest + dist verifies green under the
    remediated full-comparison verify."""
    from tools.release.__main__ import main as release_main

    dist, manifest_path, _, _ = _make_candidate_fixture(tmp_path)
    rc = release_main(["verify", str(manifest_path), str(dist)])
    assert rc == 0


def test_red_verify_fails_on_content_mutation(tmp_path):
    """Audit finding 6: mutating one artifact's bytes after manifest generation
    must fail verification (today verify prints an inventory and returns 0)."""
    from tools.release.__main__ import main as release_main

    dist, manifest_path, _, _ = _make_candidate_fixture(tmp_path)
    (dist / WHEEL_NAME).write_bytes(b"MUTATED-CONTENT")
    rc = release_main(["verify", str(manifest_path), str(dist)])
    assert rc != 0, "content mutation must fail verify"


def test_red_verify_fails_on_added_artifact(tmp_path):
    from tools.release.__main__ import main as release_main

    dist, manifest_path, _, _ = _make_candidate_fixture(tmp_path)
    (dist / "extra-unexpected.bin").write_bytes(b"surprise")
    rc = release_main(["verify", str(manifest_path), str(dist)])
    assert rc != 0, "an unexpected extra artifact must fail verify"


def test_red_verify_fails_on_removed_artifact(tmp_path):
    from tools.release.__main__ import main as release_main

    dist, manifest_path, _, _ = _make_candidate_fixture(tmp_path)
    (dist / SDIST_NAME).unlink()
    rc = release_main(["verify", str(manifest_path), str(dist)])
    assert rc != 0, "a missing artifact must fail verify"


def test_red_verify_fails_when_sbom_subject_not_wheel_digest(tmp_path):
    """Audit finding 8 (tooling side): verify must compare the SBOM's bound
    subject digest with the wheel's ACTUAL digest and fail on mismatch."""
    from tools.release.__main__ import main as release_main

    dist, manifest_path, _, _ = _make_candidate_fixture(
        tmp_path, sbom_subject_override="f" * 64)
    rc = release_main(["verify", str(manifest_path), str(dist)])
    assert rc != 0, "SBOM subject != actual wheel digest must fail verify"


# --- §G3.1: inventory v2 ------------------------------------------------------

def test_red_inventory_v2_has_schema_version_roles_and_digest(tmp_path):
    """§G3.1: the inventory carries a schema version, required artifact-role
    classification, and a deterministic canonical digest."""
    from tools.release.artifacts import build_inventory

    dist = tmp_path / "dist"
    _write_build_outputs(dist)
    (dist / "release-manifest.json").write_text("{}")
    inv = build_inventory(dist)
    doc = inv.to_dict()
    assert doc.get("inventory_schema_version") == 2
    assert inv.inventory_digest(), "inventory must carry a canonical digest"
    roles = {a.role for a in inv.artifacts}
    assert {"python-wheel", "python-sdist", "checksums",
            "cyclonedx-sbom", "release-manifest"} <= roles
    # Determinism: same bytes -> same digest.
    assert build_inventory(dist).inventory_digest() == inv.inventory_digest()


def test_red_inventory_rejects_duplicate_roles(tmp_path):
    """§G3.1: two wheels (duplicate python-wheel role) are refused."""
    from tools.release.artifacts import build_inventory

    dist = tmp_path / "dist"
    _write_build_outputs(dist)
    (dist / "rogue-second-wheel-0.0.1-py3-none-any.whl").write_bytes(b"x")
    with pytest.raises(ValueError):
        build_inventory(dist)


# --- §G3.6: SBOM strengthening ------------------------------------------------

def test_red_sbom_rejects_duplicate_components():
    """§13.3: duplicate CycloneDX components fail validation."""
    from tools.release.sbom import build_sbom, validate_sbom

    sbom = build_sbom(
        package_name="bc250-llm-mode", package_version=VERSION,
        dependencies=[("httpx", ">=0.27"), ("httpx", ">=0.27")],
        subject_sha256="a" * 64)
    ok, code = validate_sbom(
        sbom, required_dependencies=["httpx"],
        package_name="bc250-llm-mode", expected_subject_sha256="a" * 64)
    assert ok is False and code == "SBOM_DUPLICATE_COMPONENT"


def test_red_sbom_parses_pyproject_with_a_real_toml_parser():
    """§G3.6: pyproject dependencies are parsed with the standard TOML parser
    (comments and trailing junk handled), not a line-oriented partial parser."""
    from tools.release.sbom import parse_pyproject_dependencies

    text = (
        "[project]\n"
        'name = "x"\n'
        "dependencies = [\n"
        "  # a comment inside the array\n"
        '  "httpx>=0.27",  # trailing comment\n'
        '  "psutil>=5.9",\n'
        "]\n"
    )
    deps = parse_pyproject_dependencies(text)
    assert ("httpx", ">=0.27") in deps
    assert ("psutil", ">=5.9") in deps


# --- §G3.4: manifest v3 --------------------------------------------------------

def test_red_blocked_draft_manifest_is_labeled_blocked(tmp_path):
    """§G3.4: a draft manifest generated from a blocked decision says
    release_status=BLOCKED prominently and carries schema version 3."""
    from bc250_llm_mode.release_gate import evaluate_release
    from bc250_llm_mode.release_manifest import (
        RELEASE_MANIFEST_SCHEMA_VERSION, build_release_manifest)
    from bc250_llm_mode.release_policy import default_release_policy
    from tools.release.artifacts import build_inventory

    dist = tmp_path / "dist"
    _write_build_outputs(dist)
    policy = default_release_policy()
    candidate = _candidate(policy)
    inventory = build_inventory(dist)
    decision = evaluate_release(
        evidence=[], candidate=candidate, artifacts=inventory, policy=policy)
    doc = build_release_manifest(
        decision=decision, inventory=inventory, sbom_digest=None)
    assert RELEASE_MANIFEST_SCHEMA_VERSION == 3
    assert doc["manifest_schema_version"] == 3
    assert doc["release_status"] == "BLOCKED"
    assert doc["blocking_codes"], "a blocked manifest names its blockers"
    assert doc.get("manifest_digest"), "manifest carries a canonical digest"


def test_red_final_manifest_refuses_ineligible_decision(tmp_path):
    """§13.3: final manifest generation refuses an ineligible decision — a
    blocked candidate can never masquerade as a final release manifest."""
    from bc250_llm_mode.release_gate import evaluate_release
    from bc250_llm_mode.release_manifest import build_release_manifest
    from bc250_llm_mode.release_policy import default_release_policy
    from tools.release.artifacts import build_inventory

    dist = tmp_path / "dist"
    _write_build_outputs(dist)
    policy = default_release_policy()
    candidate = _candidate(policy)
    inventory = build_inventory(dist)
    decision = evaluate_release(
        evidence=[], candidate=candidate, artifacts=inventory, policy=policy)
    assert decision.eligible_for_1_0_0 is False
    with pytest.raises(ValueError):
        build_release_manifest(
            decision=decision, inventory=inventory, sbom_digest=None,
            final=True)


def test_red_manifest_digest_changes_with_any_input_change(tmp_path):
    """§13.3: the manifest digest changes when the candidate or the artifacts
    change — it is bound to exact bytes, not a template."""
    from bc250_llm_mode.release_gate import CandidateIdentity, evaluate_release
    from bc250_llm_mode.release_manifest import build_release_manifest
    from bc250_llm_mode.release_policy import default_release_policy
    from tools.release.artifacts import build_inventory

    dist = tmp_path / "dist"
    _write_build_outputs(dist)
    policy = default_release_policy()
    candidate = _candidate(policy)
    inventory = build_inventory(dist)

    def _manifest_for(cand, inv):
        decision = evaluate_release(
            evidence=[], candidate=cand, artifacts=inv, policy=policy)
        return build_release_manifest(
            decision=decision, inventory=inv, sbom_digest=None)

    base = _manifest_for(candidate, inventory)
    other_commit = _manifest_for(
        CandidateIdentity(
            version=VERSION, source_commit="d" * 40,
            source_ref="refs/heads/main", repository="local",
            policy_digest=policy.policy_digest()),
        inventory)
    assert base["manifest_digest"] != other_commit["manifest_digest"]

    dist2 = tmp_path / "dist2"
    _write_build_outputs(dist2)
    (dist2 / WHEEL_NAME).write_bytes(b"different-wheel-bytes")
    other_inv = _manifest_for(candidate, build_inventory(dist2))
    assert base["manifest_digest"] != other_inv["manifest_digest"]
