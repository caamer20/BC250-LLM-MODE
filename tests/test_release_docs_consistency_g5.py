"""G5 §G5.3/§G5.4 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): repository-
state truth + documentation-consistency gate tests.

The live-repository test is the G5 exit gate: every living document must
agree with the reviewed policy, the evidence schema, and the pinned
workflows. The seeded-contradiction tests prove the gate fails on exactly
the historical-report-vs-truth discrepancies G5 removes.
"""

from __future__ import annotations

import json
from pathlib import Path

from bc250_llm_mode.release_gate import (
    DEFAULT_BUILD_INPUT_PREFIXES,
    check_release_checkout,
)
from tools.release.docs_check import check_documentation_consistency

_ROOT = Path(__file__).resolve().parent.parent


def _codes(result) -> set[str]:
    return {f.code for f in result.findings}


# --- live repository truth (G5 exit gate) ----------------------------------

def test_live_repository_documentation_is_consistent():
    result = check_documentation_consistency(_ROOT)
    assert result.ok, [f"{f.code}: {f.message}" for f in result.findings]


# --- seeded contradictions (G5.4) -------------------------------------------

def _mini_repo(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "bc250_llm_mode").mkdir(parents=True)
    (ws / "pyproject.toml").write_text('version = "0.9.0.dev0"\n')
    (ws / "bc250_llm_mode" / "__init__.py").write_text(
        '__version__ = "0.9.0.dev0"\n')
    return ws


def test_seeded_mutable_action_ref_fails(tmp_path):
    ws = _mini_repo(tmp_path)
    wf = ws / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n  t:\n    steps:\n"
        "      - uses: actions/setup-python@v5\n")
    result = check_documentation_consistency(ws)
    assert "ACTION_REF_MUTABLE" in _codes(result)


def test_seeded_c3_complete_claim_with_mutable_refs_fails(tmp_path):
    ws = _mini_repo(tmp_path)
    wf = ws / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n  t:\n    steps:\n"
        "      - uses: actions/setup-python@v5\n")
    (ws / "CHANGELOG.md").write_text("C3 complete — pipeline done.\n")
    result = check_documentation_consistency(ws)
    assert "C3_CLAIM_WITHOUT_REMEDIATION" in _codes(result)

    # The scaffold/remediation qualification silences the finding.
    (ws / "CHANGELOG.md").write_text(
        "C3 complete: implementation scaffold complete; remediation G1-G4 "
        "closed the audit findings.\n")
    assert "C3_CLAIM_WITHOUT_REMEDIATION" not in _codes(
        check_documentation_consistency(ws))


def test_seeded_policy_snapshot_mismatch_fails(tmp_path):
    ws = _mini_repo(tmp_path)
    (ws / "bc250_llm_mode" / "release_policy.py").write_text(
        "RELEASE_POLICY_VERSION = 3\n")
    rel = ws / "release"
    rel.mkdir()
    (rel / "policy-v2.json").write_text("{}\n")  # stale snapshot version
    assert "POLICY_SNAPSHOT_MISMATCH" in _codes(
        check_documentation_consistency(ws))

    # Right version, wrong content, still fails.
    (rel / "policy-v2.json").unlink()
    (rel / "policy-v3.json").write_text(
        json.dumps({"release_policy_version": 3, "tampered": True}))
    assert "POLICY_SNAPSHOT_MISMATCH" in _codes(
        check_documentation_consistency(ws))


def test_seeded_evidence_schema_doc_mismatch_fails(tmp_path):
    ws = _mini_repo(tmp_path)
    (ws / "bc250_llm_mode" / "release_evidence.py").write_text(
        "EVIDENCE_SCHEMA_VERSION = 2\n")
    ev = ws / "release" / "evidence"
    ev.mkdir(parents=True)
    (ev / "README.md").write_text(
        "- `evidence_schema_version` — currently `1`.\n")
    assert "EVIDENCE_SCHEMA_DOC_MISMATCH" in _codes(
        check_documentation_consistency(ws))


# --- checkout truth modes (G5.3) --------------------------------------------

def test_checkout_build_input_scoping_and_diagnostics_mode(tmp_path):
    ws = tmp_path / "ws"
    (ws / "bc250_llm_mode").mkdir(parents=True)
    (ws / "bc250_llm_mode" / "stray.py").write_text("x")   # build input
    (ws / "notes").mkdir()
    (ws / "notes" / "scratch.md").write_text("owner file")  # not an input
    tracked = ["bc250_llm_mode/__init__.py"]

    # Strict default: EVERY untracked file blocks (unchanged behavior).
    strict = check_release_checkout(ws, tracked_files=tracked)
    assert strict.ok is False
    assert set(strict.blocking) == {
        "bc250_llm_mode/stray.py", "notes/scratch.md"}

    # Build-input scoping: only files that can affect the build block;
    # owner scratch files stay diagnostic-only (reported, not failing).
    scoped = check_release_checkout(
        ws, tracked_files=tracked,
        build_input_prefixes=DEFAULT_BUILD_INPUT_PREFIXES)
    assert scoped.ok is False
    assert scoped.blocking == ("bc250_llm_mode/stray.py",)
    assert "notes/scratch.md" in scoped.untracked
    assert "notes/scratch.md" not in scoped.blocking

    # Diagnostics-only mode never fails (ordinary developer checkout).
    diag = check_release_checkout(ws, tracked_files=tracked,
                                  diagnostics_only=True)
    assert diag.ok is True and diag.blocking == ()
    assert len(diag.untracked) == 2

    # Nothing is ever deleted by any mode.
    assert (ws / "notes" / "scratch.md").exists()
    assert (ws / "bc250_llm_mode" / "stray.py").exists()
