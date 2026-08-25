"""U1.2 architecture guards: one durable runtime lifecycle route (plan §15.7).

Textual/import-direction guards asserting the POST-CUTOVER state. Guards
whose subject matter still exists legitimately before Commit 8's cutover
are marked ``xfail(strict=False)`` so every committed boundary stays green
while the requirement stays visible; they flip to passing at the cutover.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "bc250_llm_mode"
RUNTIME_LIFECYCLE_MODULES = (
    "operations/runtime_lifecycle.py",
    "runtime_lifecycle_adapter.py",
    "runtime_lifecycle_command.py",
    "runtime_process.py",
    "runtime_exchange_helper.py",
)
FRONTENDS = (
    "__main__.py",
    "chat.py",
    "gui/app.py",
    "gui/dashboard.py",
    "gui/forms.py",
    "gui/steps.py",
)


def _read(rel: str) -> str:
    return (PACKAGE / rel).read_text(encoding="utf-8")


def _module_exists(rel: str) -> bool:
    return (PACKAGE / rel).exists()


@pytest.mark.xfail(
    reason="U1.2 Commit 8: synchronous update/rollback not yet deleted",
    strict=False,
)
def test_no_synchronous_update_rollback_definitions():
    """No production function may still own the legacy lifecycle names."""
    violations = []
    for py in sorted(PACKAGE.rglob("*.py")):
        if "tests" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in ("update_llamacpp", "rollback_llamacpp"):
                    violations.append(f"{py.name}:{node.name}")
            if isinstance(node, ast.Attribute) and node.attr in (
                "update_llamacpp",
                "rollback_llamacpp",
            ):
                violations.append(f"{py.name}:.{node.attr}")
    assert violations == [], f"legacy lifecycle definitions remain: {violations}"


def test_runtime_modules_use_no_shell_interpolation():
    """Runtime lifecycle modules use typed argv only (plan §12.1)."""
    for rel in RUNTIME_LIFECYCLE_MODULES:
        if not _module_exists(rel):
            continue
        text = _read(rel)
        for token in ("bash -lc", "shell=True", "`", "$(", "executescript"):
            assert token not in text.replace("``", "").replace("`{}`", ""), (
                f"{rel}: forbidden shell token {token!r}"
            )


@pytest.mark.xfail(
    reason="U1.2 Commit 8: fixed staging/backup suffix paths not yet removed",
    strict=False,
)
def test_no_fixed_staging_backup_suffix_paths():
    violations = []
    for py in sorted(PACKAGE.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for token in (
            'f"{root}-staging"',
            'f"{root}-backup"',
            'f"{root}-rolled"',
            '"llama.cpp-staging"',
            '"llama.cpp-backup"',
            '"llama.cpp-rolled"',
        ):
            if token in text:
                violations.append(f"{py.name}:{token}")
    assert violations == [], f"fixed lifecycle suffix paths remain: {violations}"


@pytest.mark.xfail(
    reason="U1.2 Commit 8: frontends still route through env.py",
    strict=False,
)
def test_frontends_import_no_runtime_infrastructure():
    banned = (
        "import subprocess",
        "import sqlite3",
        "from .env import",
        "from ..env import",
        "runtime_lifecycle_adapter",
        "runtime_builds",
        "runtime_process",
        "systemctl",
        "bc250-llm.service",
    )
    for rel in FRONTENDS:
        text = _read(rel)
        for token in banned:
            assert token not in text, f"{rel}: frontend references {token!r}"


def test_only_server_py_controls_the_llm_service_unit():
    """D10: runtime lifecycle modules never touch systemd directly."""
    for rel in (*RUNTIME_LIFECYCLE_MODULES, "runtime_handoff.py"):
        if not _module_exists(rel):
            continue
        text = _read(rel)
        for token in ("systemctl", "bc250-llm.service"):
            assert token not in text, f"{rel}: found {token!r}"


@pytest.mark.xfail(
    reason="U1.2 Commit 8: composition does not yet wire runtime workflows",
    strict=False,
)
def test_composition_registers_runtime_workflows_once():
    text = _read("app.py")
    assert text.count("build_runtime_update_workflow") == 1
    assert text.count("build_runtime_rollback_workflow") == 1
    assert "RuntimeLifecycleHostAdapter(" in text
    assert "RuntimeLifecycleCommandService(" in text
    assert "application.runtime_lifecycle =" in text


@pytest.mark.xfail(
    reason="U1.2 Commit 8: dashboard caller-side commits not yet removed",
    strict=False,
)
def test_dashboard_runtime_actions_do_not_commit():
    text = _read("gui/dashboard.py")
    for needle in ("component.update_llamacpp", "component.rollback_llamacpp"):
        assert needle not in text, f"dashboard legacy call {needle!r} remains"
    update_action = text.index("_dashboard_llamacpp_update")
    rollback_action = text.index("_dashboard_llamacpp_rollback")
    span = text[update_action : rollback_action + 800]
    assert "commit_narrow" not in span, (
        "dashboard must not persist service-owned runtime changes"
    )


@pytest.mark.xfail(
    reason="U1.2 Commit 8: setup still clones/builds llama.cpp directly",
    strict=False,
)
def test_setup_cannot_clone_or_build_llamacpp_directly():
    text = _read("env.py")
    for token in ("git clone", "cmake --build", "GGML_VULKAN=ON"):
        assert token not in text, (
            f"env.py setup provisions llama.cpp directly ({token!r}); "
            "the first runtime must come from RUNTIME_UPDATE v1"
        )


def test_mutable_refs_are_display_only_in_workflow_module():
    """The pure workflow module may carry a display ref but must never
    derive identities from it (D1/D2)."""
    if not _module_exists("operations/runtime_lifecycle.py"):
        pytest.skip("U1.2 Commit 4 pending")
    text = _read("operations/runtime_lifecycle.py")
    assert "sha256(" not in text, (
        "workflow module computes identities; identity belongs to "
        "runtime_builds primitives"
    )
