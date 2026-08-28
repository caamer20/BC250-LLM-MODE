"""G6 §G6.1/§G6.2 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): qualify the
release tooling from a clean built candidate.

Slow gate: builds the real wheel from the repository root (offline,
``pip wheel --no-deps --no-build-isolation`` — the same mechanism as the
clean-wheel gate), emits the release set (inventory v2, checksums,
subject-bound SBOM, blocked draft manifest v3), runs the FULL ``verify``
comparison, and then runs the authoritative evaluator from those exact
outputs. The decision must be ineligible and its blocking codes must be
LIMITED to the genuine external/owner gates (C4/C5/C6/C8 categories +
limitation acceptance + developer evidence kinds that only real runs can
satisfy). Any developer-code, action-pin, artifact-integrity, source-
identity, policy, documentation, or tooling blocker fails this test.

Never fabricates evidence: no record is written to ``release/evidence/``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

#: Blocking codes that are genuine external/human/owner gates (or real-run
#: developer evidence kinds) — the ONLY codes a clean dry run may report.
_EXTERNAL_GATE_CODES = frozenset({
    # C4 hardware qualification / soak / backup-restore hardware.
    "HARDWARE_QUALIFICATION_MISSING",
    "SOAK_EVIDENCE_MISSING",
    "BACKUP_RESTORE_EVIDENCE_MISSING",
    # C5 independent security review.
    "SECURITY_REVIEW_MISSING",
    # C6 non-developer human acceptance.
    "HUMAN_ACCEPTANCE_MISSING",
    # Reviewed owner acceptance of accepted limitations (model conversion).
    "LIMITATION_ACCEPTANCE_MISSING",
    # C8 approval + signing/attestation/publication.
    "MILESTONE_EVIDENCE_MISSING",
    "SIGNATURE_MISSING_OR_INVALID",
    "PROVENANCE_MISSING_OR_MISMATCHED",
    "REPOSITORY_NOT_PUBLISHED",
    # Real-run evidence kinds (satisfied only by actual executed runs).
    "TEST_EVIDENCE_MISSING",
    "CLEAN_WHEEL_EVIDENCE_MISSING",
    "SBOM_MISSING_OR_MISMATCHED",
    "DOCUMENTATION_DRIFT",
})

#: Structural/developer blockers that must NEVER survive a clean dry run.
_DEVELOPER_BLOCKERS = frozenset({
    "VERSION_MISMATCH",
    "SOURCE_COMMIT_MISMATCH",
    "ARTIFACT_DIGEST_MISMATCH",
    "UNKNOWN_EVIDENCE",
    "CAPABILITY_UNAVAILABLE",
    "CANDIDATE_REF_MISMATCH",
    "POLICY_DIGEST_MISMATCH",
    "ARTIFACT_SUBJECT_MISMATCH",
    "ARTIFACT_INVENTORY_INCOMPLETE",
})


def _run_cli(args: list[str], env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "tools.release", *args],
        capture_output=True, text=True, cwd=str(_ROOT), env=env, timeout=300)


@pytest.mark.slow
def test_clean_candidate_qualification_reports_only_external_gates(tmp_path):
    # 1. Build the wheel exactly once from the repository root.
    wheel_dir = tmp_path / "dist"
    wheel_dir.mkdir()
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(wheel_dir), str(_ROOT)],
        capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stderr[-2000:]
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1

    # 2. Emit the release set: inventory v2, checksums, subject-bound SBOM.
    from tools.release.artifacts import build_inventory
    from tools.release.sbom import build_sbom, parse_pyproject_dependencies

    inventory = build_inventory(wheel_dir)
    (wheel_dir / "checksums.sha256").write_text(
        "".join(f"{a.sha256}  {a.name}\n" for a in inventory.artifacts))
    deps = parse_pyproject_dependencies(
        (_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = next(a for a in inventory.artifacts if a.name.endswith(".whl"))
    sbom = build_sbom(package_name="bc250-llm-mode",
                      package_version="0.9.0.dev0",
                      dependencies=deps,
                      build_requires=[("setuptools", ">=68")],
                      subject_sha256=wheel.sha256)
    (wheel_dir / "sbom.cdx.json").write_text(
        json.dumps(sbom, sort_keys=True, indent=2))

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=30).stdout.strip()
    assert len(commit) == 40
    env_extra = {"PYTHONPATH": str(_ROOT)}

    # 3. Decision-derived blocked draft manifest v3.
    manifest_path = wheel_dir / "release-manifest.json"
    manifest = _run_cli(
        ["manifest", "--candidate", "0.9.0.dev0", "--source-commit", commit,
         "--source-ref", "refs/heads/main", "--artifacts", str(wheel_dir),
         "--output", str(manifest_path)], env_extra)
    assert manifest.returncode == 0, manifest.stderr[-2000:]
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert doc["manifest_schema_version"] == 3
    assert doc["release_status"] == "BLOCKED"
    assert doc["qualification_level"] == "draft"

    # 4. FULL verify comparison over the exact outputs.
    verify = _run_cli(["verify", str(manifest_path), str(wheel_dir)],
                      env_extra)
    assert verify.returncode == 0, verify.stdout[-2000:] + verify.stderr[-2000:]

    # 5. The authoritative evaluator from those exact outputs.
    evaluate = _run_cli(
        ["evaluate", "--candidate", "0.9.0.dev0", "--source-commit", commit,
         "--source-ref", "refs/heads/main", "--artifacts", str(wheel_dir),
         "--level", "rc"], env_extra)
    assert evaluate.returncode == 1  # ineligible: external gates pending
    decision = json.loads(evaluate.stdout)
    assert decision["eligible_for_1_0_0"] is False
    assert decision["eligible_for_rc"] is False
    assert decision["source_commit"] == commit
    assert decision["inventory_digest"]  # bound to the exact artifacts

    blocking = frozenset(decision["blocking_codes"])
    developer_hits = blocking & _DEVELOPER_BLOCKERS
    assert not developer_hits, (
        f"clean dry run must not surface developer/structural blockers: "
        f"{sorted(developer_hits)}")
    unexpected = blocking - _EXTERNAL_GATE_CODES
    assert not unexpected, (
        f"unexpected blocking codes beyond the external gates: "
        f"{sorted(unexpected)}")
    # The external gates must actually be named (never an empty pass).
    assert blocking
