"""C1 §C1.4 + G5 §G5.4: read-only documentation-consistency gate.

Compares the release-relevant claims across the repository and fails on
contradictions (e.g. a package version that disagrees with ``pyproject.toml``,
a schema constant that disagrees with the migrations, "P9 complete" without
the "developer scope" qualification, "working tree clean" wording when the
release build contains untracked inputs, a policy snapshot that disagrees
with the live reviewed policy, or a "C3 complete" claim while mutable action
references remain). Read-only: never mutates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocsFinding:
    code: str
    message: str


@dataclass(frozen=True)
class DocsCheckResult:
    ok: bool
    findings: tuple[DocsFinding, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok,
                "findings": [{"code": f.code, "message": f.message}
                             for f in self.findings]}


def _read(workspace: Path, rel: str) -> str | None:
    p = workspace / rel
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else None
    except OSError:
        return None


def _pyproject_version(text: str) -> str | None:
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _package_version(text: str) -> str | None:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def check_documentation_consistency(
    workspace: str | Path,
    *,
    candidate_version: str | None = None,
    expected_schema_version: int | None = None,
    untracked_present: bool = False,
) -> DocsCheckResult:
    ws = Path(workspace)
    findings: list[DocsFinding] = []

    pyproject = _read(ws, "pyproject.toml")
    package = _read(ws, "bc250_llm_mode/__init__.py")
    changelog = _read(ws, "CHANGELOG.md")
    readme = _read(ws, "README.md")
    agents = _read(ws, "AGENTS.md")

    py_version = _pyproject_version(pyproject) if pyproject else None
    pkg_version = _package_version(package) if package else None

    if py_version is None:
        findings.append(DocsFinding("VERSION_NOT_FOUND",
                                    "pyproject.toml version not found"))
    if pkg_version is None:
        findings.append(DocsFinding("VERSION_NOT_FOUND",
                                    "package __version__ not found"))
    if py_version and pkg_version and py_version != pkg_version:
        findings.append(DocsFinding(
            "VERSION_MISMATCH",
            f"pyproject {py_version!r} != package {pkg_version!r}"))
    if candidate_version and py_version and py_version != candidate_version:
        findings.append(DocsFinding(
            "VERSION_MISMATCH",
            f"candidate {candidate_version!r} != pyproject {py_version!r}"))

    if expected_schema_version is not None:
        db = _read(ws, "bc250_llm_mode/db.py")
        if db:
            m = re.search(r"(?:DATABASE_SCHEMA_VERSION|SCHEMA_VERSION)\s*=\s*(\d+)", db)
            if m and int(m.group(1)) != expected_schema_version:
                findings.append(DocsFinding(
                    "SCHEMA_MISMATCH",
                    f"db schema constant {m.group(1)} != expected "
                    f"{expected_schema_version}"))

    # Wording canaries.
    for name, text in (("README.md", readme), ("AGENTS.md", agents),
                       ("CHANGELOG.md", changelog)):
        if not text:
            continue
        if untracked_present and re.search(r"working tree clean", text, re.I):
            findings.append(DocsFinding(
                "CLEAN_WORDING_WITH_UNTRACKED",
                f"{name} claims 'working tree clean' while untracked inputs exist"))
        # 'P9 complete' must carry the developer-scope qualification.
        for m in re.finditer(r"P9\s+complete", text, re.I):
            window = text[m.start():m.start() + 120]
            if "developer" not in window.lower():
                findings.append(DocsFinding(
                    "P9_WITHOUT_DEVELOPER_SCOPE",
                    f"{name} says 'P9 complete' without the developer-scope "
                    f"qualification"))

    # G5.4: mutable action references are a hard contradiction.
    mutable_refs: list[str] = []
    workflows_dir = ws / ".github" / "workflows"
    if workflows_dir.is_dir():
        for wf in sorted(workflows_dir.glob("*.yml")):
            try:
                wf_text = wf.read_text(encoding="utf-8")
            except OSError:
                continue
            for m in re.finditer(r"uses:\s*([^\s#]+)", wf_text):
                ref = m.group(1).partition("@")[2]
                if ref and not re.fullmatch(r"[0-9a-f]{40}", ref):
                    mutable_refs.append(f"{wf.name}: {m.group(1)}")
    for ref in mutable_refs:
        findings.append(DocsFinding(
            "ACTION_REF_MUTABLE",
            f"workflow action reference is not a full commit SHA: {ref}"))

    # G5.4: a 'C3 complete' claim while mutable refs remain is exactly the
    # historical-report-vs-repository-truth contradiction G5 removes.
    if mutable_refs:
        for name, text in (("README.md", readme), ("AGENTS.md", agents),
                           ("CHANGELOG.md", changelog)):
            if not text:
                continue
            for m in re.finditer(r"C3\s+complete", text, re.I):
                window = text[m.start():m.start() + 160].lower()
                if "scaffold" not in window and "remediation" not in window:
                    findings.append(DocsFinding(
                        "C3_CLAIM_WITHOUT_REMEDIATION",
                        f"{name} claims 'C3 complete' while mutable action "
                        f"references remain (needs the scaffold/remediation "
                        f"qualification)"))

    # G5.4: policy snapshot ↔ live policy agreement.
    policy_mod = _read(ws, "bc250_llm_mode/release_policy.py")
    if policy_mod:
        m = re.search(r"RELEASE_POLICY_VERSION\s*=\s*(\d+)", policy_mod)
        if m:
            live_version = int(m.group(1))
            snapshots = sorted(
                (ws / "release").glob("policy-v*.json")) if (
                    ws / "release").is_dir() else []
            if snapshots:
                latest = snapshots[-1]
                snap_version = re.search(r"policy-v(\d+)\.json", latest.name)
                if snap_version and int(snap_version.group(1)) != live_version:
                    findings.append(DocsFinding(
                        "POLICY_SNAPSHOT_MISMATCH",
                        f"latest snapshot {latest.name} != live policy "
                        f"version {live_version}"))
                try:
                    import json as _json
                    snap = _json.loads(latest.read_text(encoding="utf-8"))
                    from bc250_llm_mode.release_policy import (
                        default_release_policy)
                    if snap != default_release_policy().to_dict():
                        findings.append(DocsFinding(
                            "POLICY_SNAPSHOT_MISMATCH",
                            f"{latest.name} content differs from the live "
                            f"reviewed policy"))
                except (OSError, ValueError, ImportError):
                    findings.append(DocsFinding(
                        "POLICY_SNAPSHOT_MISMATCH",
                        f"{latest.name} unreadable or live policy import "
                        f"failed"))

    # G5.4: evidence README must document the current schema version.
    evidence_readme = _read(ws, "release/evidence/README.md")
    evidence_mod = _read(ws, "bc250_llm_mode/release_evidence.py")
    if evidence_readme and evidence_mod:
        m = re.search(r"EVIDENCE_SCHEMA_VERSION\s*=\s*(\d+)", evidence_mod)
        if m:
            claimed = re.search(
                r"evidence_schema_version[^0-9]*(\d+)", evidence_readme)
            if claimed and int(claimed.group(1)) != int(m.group(1)):
                findings.append(DocsFinding(
                    "EVIDENCE_SCHEMA_DOC_MISMATCH",
                    f"evidence README documents schema {claimed.group(1)}; "
                    f"the validator is schema {m.group(1)}"))

    return DocsCheckResult(ok=not findings, findings=tuple(findings))
