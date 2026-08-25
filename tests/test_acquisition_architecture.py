"""U1.1 §8.6 architecture guards: the cutover is structural, not behavioral."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent / "bc250_llm_mode"
FRONTENDS = ("__main__.py", "chat.py", "gui")
FORBIDDEN_BYPASS_NAMES = (
    "download_model",
    "prepare_model",
    "prepare_local_model",
    "ModelInstallationService",
    "cleanup_conversion_intermediates",
)
FORBIDDEN_FRONTEND_MODULES = (
    "download",
    "prepare",
    "hub_source",
    "acquisition_process",
    "artifact_storage",
    "repositories",
    "sqlite3",
)


def _iter_py():
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _tree(path: Path):
    return ast.parse(path.read_text())


def test_no_synchronous_acquisition_bypass_remains_anywhere():
    offenders = []
    for path in _iter_py():
        text = path.read_text()
        for name in FORBIDDEN_BYPASS_NAMES:
            if name in text and "test" not in path.name:
                tree = _tree(path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name) and node.id == name:
                        offenders.append(f"{path.name}:{node.lineno} {name}")
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr == name
                    ):
                        offenders.append(f"{path.name}:{node.lineno} .{name}")
                    if (
                        isinstance(node, (ast.Import, ast.ImportFrom))
                        and name in ast.dump(node)
                    ):
                        offenders.append(f"{path.name}:{node.lineno} import {name}")
    assert not offenders, f"synchronous bypass references remain: {offenders}"


def test_frontends_import_no_integration_modules():
    offenders = []
    frontend_paths = [ROOT / f for f in FRONTENDS if (ROOT / f).exists()]
    files = []
    for p in frontend_paths:
        if p.is_dir():
            files.extend(p.rglob("*.py"))
        else:
            files.append(p)
    for path in files:
        tree = _tree(path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in FORBIDDEN_FRONTEND_MODULES:
                    offenders.append(f"{path.name}: imports {name}")
    assert not offenders, f"frontend integration imports: {offenders}"


def test_only_artifact_storage_writes_managed_or_quarantine_roots():
    allowed = {"artifact_storage.py"}
    markers = (
        ".bc250-artifacts",
        ".bc250-quarantine",
    )
    offenders = []
    for path in _iter_py():
        if path.name in allowed or path.name == "paths.py":
            continue
        text = path.read_text()
        for marker in markers:
            if marker in text:
                offenders.append(f"{path.name}: {marker}")
    assert not offenders, f"artifact-namespace writers outside storage: {offenders}"


def test_application_composes_one_registry_and_one_engine_factory():
    text = (ROOT / "app.py").read_text()
    assert text.count("WorkflowRegistry()") == 1
    assert text.count(".freeze()") == 1
    assert "ModelAcquisitionCommandService(" in text
    assert "ActivationCommandService(" in text
    assert "model_install" not in text.replace("model_installations", "")
    # Composition enqueues nothing and starts nothing.
    assert ".enqueue(" not in text
    assert "execute_one" not in text


def test_activation_payload_cannot_carry_a_caller_path():
    """MODEL_ACTIVATE's request vocabulary is closed; a caller path key is
    rejected by the workflow decoder before anything is persisted."""
    from bc250_llm_mode.operations.activation import (
        build_activation_workflow,
    )
    from bc250_llm_mode.operations.model import OperationValidationError

    decode = build_activation_workflow(object()).decode_request  # type: ignore[arg-type]
    with pytest.raises(OperationValidationError):
        decode(
            {
                "model_alias": "x",
                "expected_runtime_revision": 1,
                "source_path": "/outside/model.gguf",
            }
        )