"""C1 §C1.4: read-only documentation-consistency gate.

Compares the release-relevant claims across the repository and fails on
contradictions (e.g. a package version that disagrees with ``pyproject.toml``,
a schema constant that disagrees with the migrations, "P9 complete" without
the "developer scope" qualification, or "working tree clean" wording when the
release build contains untracked inputs). Read-only: never mutates.
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

    return DocsCheckResult(ok=not findings, findings=tuple(findings))
