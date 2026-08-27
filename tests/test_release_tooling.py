"""C1 §C1.1/§C1.4: repository-only release tooling tests.

Covers the strict evidence I/O, bounded artifact inventory, the read-only
documentation-consistency gate, and the ``tools.release`` CLI subcommands.
These exercise repository-only tooling (NOT the runtime package).
"""

from __future__ import annotations

import json

import pytest

from tools.release.artifacts import Artifact, build_inventory, sha256_file
from tools.release.docs_check import check_documentation_consistency
from tools.release.evidence_io import load_evidence_dir, read_evidence_file
from tools.release.__main__ import main as release_main

from bc250_llm_mode.release_evidence import EVIDENCE_SCHEMA_VERSION


def _good_record(kind="DEFAULT_TEST_SUITE", version="1.0.0rc1"):
    return {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": f"ev-{kind}",
        "kind": kind,
        "release_candidate_version": version,
        "source_commit": "c" * 40,
        "artifact_subjects": [{"name": "w", "sha256": "a" * 64}],
        "issuer": {"type": "ci", "identity": "test"},
        "issued_at": "2026-01-01T00:00:00+00:00",
        "expires_at": None,
        "environment": {"os": "linux", "architecture": "x86_64",
                        "python": "3.14", "runner": "pytest"},
        "result": "PASS",
        "measurements": {},
        "attachments": [],
        "signature_or_attestation_reference": "sig",
        "supersedes_evidence_id": None,
    }


# --- evidence_io -----------------------------------------------------------

def test_read_evidence_file_accepts_valid_and_rejects_bad(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_good_record()))
    rec, reason = read_evidence_file(good)
    assert rec is not None and reason == ""

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    rec2, reason2 = read_evidence_file(bad)
    assert rec2 is None and reason2 == "invalid-json"

    arr = tmp_path / "arr.json"
    arr.write_text("[1,2]")
    rec3, reason3 = read_evidence_file(arr)
    assert rec3 is None and reason3 == "not-an-object"

    missing, reason4 = read_evidence_file(tmp_path / "nope.json")
    assert missing is None and reason4 == "not-a-file"


def test_load_evidence_dir_partitions_records_and_errors(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_good_record("SBOM")))
    (tmp_path / "broken.json").write_text("!!!")
    records, errors = load_evidence_dir(tmp_path)
    assert len(records) == 1
    assert errors and errors[0][0] == "broken.json"

    empty, errs = load_evidence_dir(tmp_path / "missing")
    assert empty == [] and errs[0][1] == "missing-evidence-dir"


# --- artifacts -------------------------------------------------------------

def test_build_inventory_digests_and_bounds(tmp_path):
    f = tmp_path / "wheel.whl"
    f.write_bytes(b"hello")
    inv = build_inventory(tmp_path)
    assert len(inv.artifacts) == 1
    assert inv.artifacts[0].sha256 == sha256_file(f)
    assert inv.by_name()["wheel.whl"].size == 5

    empty = build_inventory(tmp_path / "nodir")
    assert empty.artifacts == ()


def test_build_inventory_rejects_too_many(tmp_path, monkeypatch):
    import tools.release.artifacts as mod
    monkeypatch.setattr(mod, "MAX_ARTIFACTS", 2)
    for i in range(3):
        (tmp_path / f"a{i}").write_bytes(b"x")
    with pytest.raises(ValueError):
        build_inventory(tmp_path)


# --- docs_check ------------------------------------------------------------

def _mini_repo(tmp_path, py_version="1.0.0rc1", pkg_version="1.0.0rc1",
               schema=9):
    (tmp_path / "bc250_llm_mode").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{py_version}"\n')
    (tmp_path / "bc250_llm_mode" / "__init__.py").write_text(
        f'__version__ = "{pkg_version}"\n')
    (tmp_path / "bc250_llm_mode" / "db.py").write_text(
        f"DATABASE_SCHEMA_VERSION = {schema}\n")
    (tmp_path / "README.md").write_text("# BC-250\n")
    (tmp_path / "AGENTS.md").write_text("# guide\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    return tmp_path


def test_docs_check_clean_repo_passes(tmp_path):
    ws = _mini_repo(tmp_path)
    result = check_documentation_consistency(
        ws, candidate_version="1.0.0rc1", expected_schema_version=9)
    assert result.ok is True and result.findings == ()


def test_docs_check_detects_version_and_schema_drift(tmp_path):
    ws = _mini_repo(tmp_path, pkg_version="0.9.0.dev0", schema=8)
    result = check_documentation_consistency(
        ws, candidate_version="1.0.0rc1", expected_schema_version=9)
    codes = {f.code for f in result.findings}
    assert "VERSION_MISMATCH" in codes
    assert "SCHEMA_MISMATCH" in codes
    assert result.ok is False


def test_docs_check_clean_wording_and_p9_canaries(tmp_path):
    ws = _mini_repo(tmp_path)
    (ws / "README.md").write_text("The working tree clean is great.\n")
    (ws / "AGENTS.md").write_text("P9 complete and shipped.\n")
    result = check_documentation_consistency(
        ws, candidate_version="1.0.0rc1", expected_schema_version=9,
        untracked_present=True)
    codes = {f.code for f in result.findings}
    assert "CLEAN_WORDING_WITH_UNTRACKED" in codes
    assert "P9_WITHOUT_DEVELOPER_SCOPE" in codes


def test_docs_check_p9_with_developer_scope_passes(tmp_path):
    ws = _mini_repo(tmp_path)
    (ws / "AGENTS.md").write_text("P9 complete (developer-executable scope).\n")
    result = check_documentation_consistency(
        ws, candidate_version="1.0.0rc1", expected_schema_version=9)
    assert "P9_WITHOUT_DEVELOPER_SCOPE" not in {f.code for f in result.findings}


# --- CLI -------------------------------------------------------------------

def test_cli_validate_and_evaluate_fail_closed(tmp_path, capsys):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "a.json").write_text(json.dumps(_good_record()))
    # validate: one accepted record
    rc = release_main(["validate", str(ev), "--candidate", "1.0.0rc1"])
    assert rc == 0
    # evaluate: still not eligible (most evidence kinds missing) -> exit 1
    rc2 = release_main(["evaluate", "--candidate", "1.0.0rc1",
                        "--source-commit", "c" * 40, "--evidence", str(ev)])
    assert rc2 == 1
    out = capsys.readouterr().out
    assert "eligible_for_1_0_0=False" in out


def test_cli_manifest_and_verify_round_trip(tmp_path, capsys):
    out_manifest = tmp_path / "release-manifest.json"
    rc = release_main(["manifest", "--candidate", "1.0.0rc1",
                       "--output", str(out_manifest)])
    assert rc == 0
    doc = json.loads(out_manifest.read_text())
    assert doc["version"] == "1.0.0rc1"

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "pkg.whl").write_bytes(b"data")
    rc2 = release_main(["verify", str(out_manifest), str(dist)])
    assert rc2 == 0
    out = capsys.readouterr().out
    assert "pkg.whl" in out


def test_cli_verify_rejects_schema_mismatch(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"manifest_schema_version": 999}))
    dist = tmp_path / "dist"
    dist.mkdir()
    rc = release_main(["verify", str(manifest), str(dist)])
    assert rc == 1
