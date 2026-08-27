"""C3 §C3.1/§C3.2/§C3.8: release-workflow least-privilege + structure gates.

Verifies the CI and release workflows are least-privilege by default, that OIDC
`id-token: write` exists only on the attest + publish jobs, that the publish job
performs NO build (it retrieves the exact previously built artifacts), and that
publication is approval/environment-gated. These are static gates over the
workflow YAML — they never run the workflows or publish anything.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def _load_workflow(name: str) -> dict:
    with open(_ROOT / ".github" / "workflows" / name) as fh:
        return yaml.safe_load(fh)


def test_ci_workflow_is_least_privilege():
    ci = _load_workflow("ci.yml")
    assert ci.get("permissions") == {"contents": "read"}


def test_release_workflow_is_least_privilege_by_default():
    rel = _load_workflow("release.yml")
    assert rel.get("permissions") == {"contents": "read"}


def test_release_workflow_oidc_only_on_attest_and_publish():
    rel = _load_workflow("release.yml")
    for name, job in rel["jobs"].items():
        perms = job.get("permissions") or {}
        if perms.get("id-token") == "write":
            assert name in {"attest", "publish"}, (
                f"id-token: write leaked into job {name!r}")
    assert rel["jobs"]["publish"]["permissions"].get("id-token") == "write"


def test_release_publish_job_performs_no_build():
    rel = _load_workflow("release.yml")
    publish = rel["jobs"]["publish"]
    blob = " ".join(str(step.get("run", "")) for step in publish["steps"])
    assert "python -m build" not in blob
    used = {str(step.get("uses", "")).split("@")[0] for step in publish["steps"]}
    assert not any("build" in u for u in used if u)


def test_release_publish_is_approval_gated():
    rel = _load_workflow("release.yml")
    publish = rel["jobs"]["publish"]
    assert publish.get("environment"), "publish must be environment-gated"
    assert "approval-environment" in publish.get("needs", [])


def test_release_workflow_has_expected_pipeline():
    rel = _load_workflow("release.yml")
    for job in ("validate-candidate", "build-once", "verify-artifacts",
                "attest", "approval-environment", "publish"):
        assert job in rel["jobs"]


def test_checkout_is_sha_pinned():
    # C3.1: at least the checkout action is pinned to a full-length commit SHA
    # (verified against the upstream release); remaining actions carry a
    # TODO(C3) until their SHAs are verified with network access.
    for name in ("ci.yml", "release.yml"):
        doc = _load_workflow(name)
        text = yaml.dump(doc)
        assert "actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11" in text
