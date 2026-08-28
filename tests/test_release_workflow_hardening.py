"""G0.2 (§6, RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): intentionally-RED
workflow-hardening tests.

These freeze audit findings 9-10 and the C3 exit-gate gaps (§1: mutable action
refs, attestations never verified, no complete release set emitted, evaluator
never run by the workflow, publication placeholder) plus the §13.5 workflow
matrix as failing gates BEFORE any production change. G4 turns them green:
every action pinned to a reviewed full 40-character SHA, candidate-bound
invocation, build-once emitting the complete release set, post-attestation
verification, an authoritative final-evaluation job gating approval/publish,
and Dependabot-managed pin updates.

These are static gates over the workflow YAML plus CLI behavior — they never
run the workflows, mint attestations, or publish anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _load_workflow(name: str) -> dict:
    with open(_ROOT / ".github" / "workflows" / name) as fh:
        return yaml.safe_load(fh)


def _triggers(doc: dict):
    # YAML parses the bare `on:` key as boolean True.
    return doc.get("on", doc.get(True)) or {}


def _run_blob(doc: dict, job_name: str | None = None) -> str:
    jobs = doc["jobs"]
    selected = {job_name: jobs[job_name]} if job_name else jobs
    return " \n".join(
        str(step.get("run", ""))
        for job in selected.values()
        for step in job.get("steps") or [])


def _transitive_needs(doc: dict, job_name: str) -> set[str]:
    jobs = doc["jobs"]
    seen: set[str] = set()
    stack = [job_name]
    while stack:
        for dep in jobs.get(stack.pop(), {}).get("needs") or []:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


# --- audit finding: mutable action references --------------------------------

def test_red_every_uses_ref_is_a_full_commit_sha():
    """§G4.1: every third-party action reference must be a reviewed full
    40-character commit SHA — no mutable tags anywhere."""
    for name in ("ci.yml", "release.yml"):
        doc = _load_workflow(name)
        for job_name, job in doc["jobs"].items():
            for step in job.get("steps") or []:
                uses = step.get("uses")
                if not uses:
                    continue
                _, _, ref = str(uses).partition("@")
                assert _SHA_RE.fullmatch(ref), (
                    f"{name}:{job_name} carries an unpinned reference: {uses}")


def test_red_no_todo_c3_markers_remain():
    """§G4 exit gate: no unresolved TODO(C3) pin markers remain."""
    for name in ("ci.yml", "release.yml"):
        text = (_ROOT / ".github" / "workflows" / name).read_text()
        assert "TODO(C3)" not in text, f"TODO(C3) remains in {name}"


def test_red_dependabot_manages_github_actions_pins():
    """§G4.1.5: pin updates are proposed as reviewed Dependabot changes, never
    by restoring mutable tags."""
    path = _ROOT / ".github" / "dependabot.yml"
    assert path.is_file(), ".github/dependabot.yml is required"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    ecosystems = {u.get("package-ecosystem") for u in doc.get("updates", [])}
    assert "github-actions" in ecosystems


# --- audit finding: evaluator never run by the workflow -----------------------

def test_red_release_workflow_runs_authoritative_evaluator():
    """§G4.7/finding 9: the release workflow must execute the authoritative
    release evaluator over the produced artifacts/evidence."""
    rel = _load_workflow("release.yml")
    assert "tools.release evaluate" in _run_blob(rel), (
        "the release workflow must run `python -m tools.release evaluate`")


def test_red_final_evaluation_gates_approval_and_publish():
    """§G4.7: a dedicated final-evaluation job must gate BOTH the approval
    environment and publish — environment approval cannot override the
    evaluator."""
    rel = _load_workflow("release.yml")
    assert "final-evaluation" in rel["jobs"], (
        "a final-evaluation job is required")
    assert "final-evaluation" in _transitive_needs(rel, "approval-environment")
    assert "final-evaluation" in _transitive_needs(rel, "publish")


# --- audit finding: attestations are never verified ----------------------------

def test_red_attestations_are_verified_before_approval():
    """§G4.6/finding: attestation creation must be followed by verification
    (repository, workflow identity, commit, subject digests) BEFORE the
    approval environment — an attestation that is never verified does not
    satisfy the gate."""
    rel = _load_workflow("release.yml")
    jobs = rel["jobs"]
    verify_jobs = [n for n in jobs
                   if "attest" in n and "verif" in n and n != "attest"]
    assert verify_jobs, "a post-attestation verification job is required"
    vjob = verify_jobs[0]
    assert "attest" in (jobs[vjob].get("needs") or []), (
        f"{vjob} must consume the attest job")
    assert vjob in _transitive_needs(rel, "approval-environment"), (
        "approval must depend on post-attestation verification")
    blob = _run_blob(rel, vjob)
    assert "verify" in blob, f"{vjob} must actually verify attestations"


def test_red_workflow_sbom_check_compares_actual_wheel_digest():
    """Audit finding 8 (workflow side): the SBOM check must compare the SBOM
    subject against the wheel's ACTUAL digest (via tools.release verify), not
    merely assert that some SHA-256 exists."""
    rel = _load_workflow("release.yml")
    blob = _run_blob(rel, "verify-artifacts")
    assert "tools.release verify" in blob, (
        "verify-artifacts must run the full tools.release verify path")


# --- audit finding: incomplete release set -------------------------------------

def test_red_build_once_emits_complete_release_set():
    """Finding: the release workflow does not emit the complete candidate
    inventory/release manifest it claims. build-once must emit checksums, SBOM,
    inventory v2, AND the release manifest."""
    rel = _load_workflow("release.yml")
    blob = _run_blob(rel, "build-once")
    for required in ("checksums.sha256", "sbom.cdx.json",
                     "inventory", "release-manifest"):
        assert required in blob, f"build-once must emit {required}"


# --- candidate binding (§G4.2) --------------------------------------------------

def test_red_release_workflow_has_qualification_level_and_ref_inputs():
    """§G4.2: one normalized workflow input model — candidate version, ref, and
    qualification level (rc/final) — binds the invocation."""
    rel = _load_workflow("release.yml")
    inputs = _triggers(rel).get("workflow_dispatch", {}).get("inputs") or {}
    for field in ("candidate_version", "candidate_ref", "qualification_level"):
        assert field in inputs, f"release workflow input missing: {field}"


# --- publication gating (§G4.8) --------------------------------------------------

def test_red_publish_consumes_decision_verification_not_shell_refusal():
    """Finding/§G4.8: the publish job must consume the release decision via
    full manifest/artifact verification — the refusal must be a release-state
    blocker, not a shell line bypassable by editing a workflow input."""
    rel = _load_workflow("release.yml")
    blob = _run_blob(rel, "publish")
    assert "tools.release verify" in blob, (
        "publish must re-verify the exact artifacts + manifest before upload")
    assert "python -m build" not in blob, "publish must never rebuild"


def test_publish_has_no_wildcard_artifact_selection():
    """§13.5 guard (green at G0, must stay green): publish downloads the exact
    named artifact bundle — never a wildcard expansion."""
    rel = _load_workflow("release.yml")
    for step in rel["jobs"]["publish"].get("steps") or []:
        uses = str(step.get("uses", ""))
        if "download-artifact" in uses:
            with_block = step.get("with") or {}
            assert with_block.get("name"), (
                "publish must download an exactly named artifact bundle")
            assert "pattern" not in with_block, (
                "wildcard artifact selection is forbidden in publish")


def test_pull_request_paths_cannot_attest_or_publish():
    """§13.5 guard (green at G0, must stay green): pull-request runs never
    reach attestation or publication permissions."""
    ci = _load_workflow("ci.yml")
    assert "id-token" not in yaml.dump(ci), (
        "CI must never hold OIDC id-token permissions")
    assert "attest-build-provenance" not in yaml.dump(ci)
    rel = _load_workflow("release.yml")
    assert "pull_request" not in _triggers(rel), (
        "the release workflow must not run on pull_request")


def test_current_checkout_is_not_publishable_guard():
    """§13.5 guard (green at G0, must stay green): no workflow input can make
    the current checkout eligible — the evaluator over the real (empty)
    evidence set keeps it blocked. Written to survive the G1 signature change."""
    from bc250_llm_mode.release_gate import evaluate_release

    try:
        decision = evaluate_release(evidence=[], candidate_version="1.0.0")
    except TypeError:
        # Remediated (G1) signature: candidate identity + inventory mandatory.
        from bc250_llm_mode.release_artifacts import ArtifactInventory
        from bc250_llm_mode.release_gate import CandidateIdentity
        from bc250_llm_mode.release_policy import default_release_policy

        policy = default_release_policy()
        candidate = CandidateIdentity(
            version="1.0.0",
            source_commit="0123456789abcdef0123456789abcdef01234567",
            source_ref="refs/heads/main", repository="local",
            policy_digest=policy.policy_digest())
        decision = evaluate_release(
            evidence=[], candidate=candidate,
            artifacts=ArtifactInventory(), policy=policy)
    assert decision.eligible_for_1_0_0 is False
    assert decision.eligible_for_rc is False
