"""C3 §C3.3/§C3.4/§C3.8: supply-chain tooling — SBOM + artifact inventory.

Tests the CycloneDX SBOM generator/validator (package + required direct
dependencies present, deterministic digest, fail-closed refusal of missing
deps/secret material/non-normalized paths/subject mismatch) and the hardened
artifact inventory (content identity, media type, symlink rejection).
"""

from __future__ import annotations

import os

import pytest

from tools.release.artifacts import build_inventory, media_type_for
from tools.release.sbom import (
    build_sbom,
    parse_pyproject_dependencies,
    sbom_digest,
    validate_sbom,
)

_DEPS = [("gguf", ">=0.17"), ("httpx", ">=0.27"),
         ("prompt-toolkit", ">=3.0"), ("rich", ">=13.7")]
_REQUIRED = [name for name, _ in _DEPS]


def test_sbom_contains_package_and_dependencies():
    sbom = build_sbom(package_name="bc250-llm-mode",
                      package_version="1.0.0", dependencies=_DEPS)
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["component"]["name"] == "bc250-llm-mode"
    names = {c["name"] for c in sbom["components"]}
    for dep in _REQUIRED:
        assert dep in names


def test_sbom_is_deterministic():
    a = build_sbom(package_name="p", package_version="1", dependencies=_DEPS)
    b = build_sbom(package_name="p", package_version="1", dependencies=_DEPS)
    assert sbom_digest(a) == sbom_digest(b)


def test_sbom_binds_subject_digest():
    sbom = build_sbom(package_name="p", package_version="1",
                      dependencies=_DEPS, subject_sha256="a" * 64)
    ok, code = validate_sbom(sbom, required_dependencies=_REQUIRED,
                             package_name="p", expected_subject_sha256="a" * 64)
    assert ok and code == "OK"
    bad, bad_code = validate_sbom(sbom, required_dependencies=_REQUIRED,
                                  package_name="p",
                                  expected_subject_sha256="b" * 64)
    assert not bad and bad_code == "SBOM_SUBJECT_MISMATCH"


def test_sbom_refuses_missing_dependency():
    sbom = build_sbom(package_name="p", package_version="1",
                      dependencies=[("gguf", ">=0.17")])
    ok, code = validate_sbom(sbom, required_dependencies=["gguf", "httpx"],
                             package_name="p")
    assert not ok and code == "SBOM_DEPENDENCY_MISSING"


def test_sbom_refuses_secret_material():
    sbom = build_sbom(package_name="p", package_version="1", dependencies=_DEPS)
    sbom["components"][0]["name"] = "hf_api_token"
    ok, code = validate_sbom(sbom, required_dependencies=[], package_name="p")
    assert not ok and code == "SBOM_SECRET_MATERIAL"


def test_sbom_refuses_non_normalized_path():
    sbom = build_sbom(package_name="p", package_version="1", dependencies=_DEPS)
    sbom["components"][0]["name"] = "../../tmp/evil"
    ok, code = validate_sbom(sbom, required_dependencies=[], package_name="p")
    assert not ok and code == "SBOM_PATH_NOT_NORMALIZED"


def test_parse_pyproject_dependencies():
    text = '''
[project]
name = "x"
dependencies = [
  "gguf>=0.17",
  "httpx>=0.27",
]
[project.scripts]
'''
    deps = parse_pyproject_dependencies(text)
    assert ("gguf", ">=0.17") in deps
    assert ("httpx", ">=0.27") in deps


def test_inventory_assigns_media_type_and_digest(tmp_path):
    (tmp_path / "pkg-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
    (tmp_path / "pkg-1.0.0.tar.gz").write_bytes(b"sdist")
    (tmp_path / "sbom.cdx.json").write_bytes(b"{}")
    inv = build_inventory(tmp_path)
    by = inv.by_name()
    assert by["pkg-1.0.0-py3-none-any.whl"].media_type == \
        "application/vnd.pypi.wheel.v1"
    assert by["pkg-1.0.0.tar.gz"].media_type == "application/gzip"
    assert by["sbom.cdx.json"].media_type == "application/json"


def test_inventory_rejects_symlink(tmp_path):
    target = tmp_path / "real.whl"
    target.write_bytes(b"data")
    link = tmp_path / "link.whl"
    os.symlink(target, link)
    with pytest.raises(ValueError):
        build_inventory(tmp_path)


def test_media_type_for_fallback():
    assert media_type_for("unknown.bin") == "application/octet-stream"
    assert media_type_for("checksums.sha256") == "text/plain"
