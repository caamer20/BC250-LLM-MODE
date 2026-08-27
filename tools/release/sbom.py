"""CycloneDX SBOM generation + validation for the release tooling (C3 §C3.4).

Generates a deterministic CycloneDX JSON SBOM covering the package itself,
its direct runtime dependencies, the build backend, and the managed
third-party / external runtime identities the appliance references (Open WebUI
container digest pin, llama.cpp). Deterministic by construction: the serial and
timestamp are injectable and default to fixed values so the SBOM digest is
stable for a given input set (a release audit must be reproducible).

Validation is fail-closed: the package and every required direct dependency
MUST appear, secret-like material and non-normalized paths are refused, and the
SBOM subject digest must match the built artifact when one is bound.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

SBOM_SPEC_VERSION = "1.5"
SBOM_TOOL_NAME = "bc250-release-sbom"
SBOM_TOOL_VERSION = "1"

# Secret-like keys/values never belong in an SBOM (C3.4 fail-closed).
_SECRET_KEY_RE = re.compile(
    r"(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    kind: str = "library"  # library | application | container | operating-system
    purl: str = ""
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.kind,
            "name": self.name,
            "version": self.version,
        }
        if self.purl:
            d["purl"] = self.purl
        if self.sha256:
            d["hashes"] = [{"alg": "SHA-256", "content": self.sha256}]
        return d


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sbom_digest(sbom: dict[str, Any]) -> str:
    """Stable sha256 over the canonical SBOM document."""
    return hashlib.sha256(_canonical(sbom).encode("utf-8")).hexdigest()


def parse_pyproject_dependencies(pyproject_text: str) -> list[tuple[str, str]]:
    """Extract the runtime ``dependencies`` list as (name, specifier) pairs.

    Bounded, dependency-name-only parse (no TOML library required): reads the
    ``dependencies = [ ... ]`` array under ``[project]``.
    """
    deps: list[tuple[str, str]] = []
    in_deps = False
    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if re.match(r"^dependencies\s*=\s*\[", stripped):
            in_deps = True
            if "]" in stripped:  # single-line array
                in_deps = False
            continue
        if in_deps:
            if stripped.startswith("]"):
                in_deps = False
                continue
            item = stripped.strip(",").strip().strip('"').strip("'")
            if not item:
                continue
            match = re.match(r"^([A-Za-z0-9._-]+)\s*(.*)$", item)
            if match:
                deps.append((match.group(1), match.group(2).strip()))
    return deps


def build_sbom(
    *,
    package_name: str,
    package_version: str,
    dependencies: list[tuple[str, str]],
    build_requires: list[tuple[str, str]] | None = None,
    container_refs: list[Component] | None = None,
    runtime_refs: list[Component] | None = None,
    subject_sha256: str = "",
    timestamp: str = "1970-01-01T00:00:00Z",
    serial: str = "urn:uuid:00000000-0000-0000-0000-000000000000",
) -> dict[str, Any]:
    """Build a deterministic CycloneDX SBOM document."""
    components: list[Component] = []
    for name, spec in dependencies:
        components.append(Component(
            name=name, version=spec or "unspecified", kind="library",
            purl=f"pkg:pypi/{name.lower()}"))
    for name, spec in (build_requires or []):
        components.append(Component(
            name=name, version=spec or "unspecified", kind="library",
            purl=f"pkg:pypi/{name.lower()}"))
    components.extend(container_refs or [])
    components.extend(runtime_refs or [])

    metadata_component: dict[str, Any] = {
        "type": "application",
        "name": package_name,
        "version": package_version,
        "purl": f"pkg:pypi/{package_name.lower()}@{package_version}",
    }
    if subject_sha256:
        metadata_component["hashes"] = [
            {"alg": "SHA-256", "content": subject_sha256}]

    return {
        "bomFormat": "CycloneDX",
        "specVersion": SBOM_SPEC_VERSION,
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": metadata_component,
            "tools": [{"name": SBOM_TOOL_NAME, "version": SBOM_TOOL_VERSION}],
        },
        "components": [c.to_dict() for c in components],
    }


def _walk_strings(obj: Any):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from _walk_strings(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_strings(item)
    elif isinstance(obj, str):
        yield obj


def validate_sbom(
    sbom: dict[str, Any],
    *,
    required_dependencies: list[str],
    package_name: str,
    expected_subject_sha256: str = "",
) -> tuple[bool, str]:
    """Fail-closed SBOM validation. Returns (ok, rejection_code)."""
    if sbom.get("bomFormat") != "CycloneDX":
        return False, "SBOM_BAD_FORMAT"
    metadata = sbom.get("metadata") or {}
    component = metadata.get("component") or {}
    if component.get("name") != package_name:
        return False, "SBOM_PACKAGE_MISSING"
    names = {c.get("name") for c in sbom.get("components", [])}
    for dep in required_dependencies:
        if dep not in names:
            return False, "SBOM_DEPENDENCY_MISSING"
    # Secret material never appears in an SBOM.
    for text in _walk_strings(sbom):
        if _SECRET_KEY_RE.search(text):
            return False, "SBOM_SECRET_MATERIAL"
    # Paths (purl/name) must be normalized identifiers, never filesystem paths.
    for c in sbom.get("components", []):
        for field in ("name", "purl"):
            value = c.get(field, "")
            if value and ("/" in value and not value.startswith("pkg:")):
                return False, "SBOM_PATH_NOT_NORMALIZED"
    if expected_subject_sha256:
        hashes = component.get("hashes") or []
        bound = next(
            (h.get("content") for h in hashes if h.get("alg") == "SHA-256"), "")
        if bound != expected_subject_sha256:
            return False, "SBOM_SUBJECT_MISMATCH"
    return True, "OK"
