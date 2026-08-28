"""G4 §G4.9 (RELEASE_GATE_AND_PIPELINE_REMEDIATION plan): workflow policy
tests beyond the G0 red set — structural and semantic invariants over the
workflow YAML (never executing the workflows, minting attestations, or
publishing anything).

Covers the §G4.9 items not already frozen by
``tests/test_release_workflow_hardening.py``: read-only top-level
permissions, OIDC id-token scoped to attest/publish only, build commands in
exactly one release job, one shared artifact identity across the consuming
jobs, and the approval environment attached only to the intended gated jobs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def _load_workflow(name: str) -> dict:
    with open(_ROOT / ".github" / "workflows" / name) as fh:
        return yaml.safe_load(fh)


def _run_blob(doc: dict, job_name: str) -> str:
    return " \n".join(
        str(step.get("run", ""))
        for step in doc["jobs"][job_name].get("steps") or [])


def test_top_level_permissions_are_read_only():
    for name in ("ci.yml", "release.yml"):
        perms = _load_workflow(name).get("permissions") or {}
        assert perms == {"contents": "read"}, (
            f"{name} top-level permissions must be exactly contents: read, "
            f"got {perms}")


def test_id_token_write_exists_only_in_attest_and_publish():
    rel = _load_workflow("release.yml")
    for job_name, job in rel["jobs"].items():
        token = (job.get("permissions") or {}).get("id-token")
        if token == "write":
            assert job_name in {"attest", "publish"}, (
                f"id-token: write must exist only on attest/publish, "
                f"found on {job_name}")
        else:
            assert token is None, f"{job_name} carries id-token: {token}"
    # Both intended jobs actually hold it.
    for job_name in ("attest", "publish"):
        assert (rel["jobs"][job_name].get("permissions") or {}).get(
            "id-token") == "write"


def test_build_commands_occur_in_exactly_one_release_job():
    rel = _load_workflow("release.yml")
    builders = [job_name for job_name in rel["jobs"]
                if "python -m build" in _run_blob(rel, job_name)]
    assert builders == ["build-once"], (
        f"exactly one job may build; found {builders}")


def test_consuming_jobs_share_one_artifact_identity():
    rel = _load_workflow("release.yml")
    consumers = ("verify-artifacts", "attest", "verify-attestations",
                 "final-evaluation", "publish")
    for job_name in consumers:
        names = [str((step.get("with") or {}).get("name"))
                 for step in rel["jobs"][job_name].get("steps") or []
                 if "download-artifact" in str(step.get("uses", ""))]
        assert names and all(n == "release-candidate" for n in names), (
            f"{job_name} must download exactly the release-candidate bundle, "
            f"got {names}")
    uploads = [(job_name, str((step.get("with") or {}).get("name")))
               for job_name, job in rel["jobs"].items()
               for step in job.get("steps") or []
               if "upload-artifact" in str(step.get("uses", ""))]
    uploaded = {name for _, name in uploads}
    assert uploaded == {"release-candidate", "release-decision"}, (
        f"unexpected uploaded artifact bundles: {uploaded}")


def test_approval_environment_attached_only_to_gated_jobs():
    rel = _load_workflow("release.yml")
    env_jobs = [job_name for job_name, job in rel["jobs"].items()
                if job.get("environment")]
    assert sorted(env_jobs) == ["approval-environment", "publish"], (
        f"the release-approval environment must gate only the approval + "
        f"publish jobs, found {env_jobs}")
    for job_name in env_jobs:
        assert rel["jobs"][job_name]["environment"] == "release-approval"


def test_release_workflow_never_uploads_from_pr_or_wildcard_paths():
    rel = _load_workflow("release.yml")
    for job_name, job in rel["jobs"].items():
        for step in job.get("steps") or []:
            if "upload-artifact" in str(step.get("uses", "")):
                with_block = step.get("with") or {}
                assert with_block.get("name"), (
                    f"{job_name} must upload an exactly named bundle")
                assert "pattern" not in with_block, (
                    f"{job_name} must not use wildcard artifact selection")
