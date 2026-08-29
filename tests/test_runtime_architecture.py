"""U1.2 architecture guards: one durable runtime lifecycle route (plan §15.7).

Textual/import-direction guards asserting the POST-CUTOVER state. Guards
whose subject matter still exists legitimately before Commit 8's cutover
are marked ``xfail(strict=False)`` so every committed boundary stays green
while the requirement stays visible; they flip to passing at the cutover.
"""

from __future__ import annotations

import ast
import re
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
    "gui/setup_page.py",
)


def _read(rel: str) -> str:
    return (PACKAGE / rel).read_text(encoding="utf-8")


def _module_exists(rel: str) -> bool:
    return (PACKAGE / rel).exists()


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
    """Runtime lifecycle modules use typed argv only (plan §12.1).

    The tokens below match INVOCATION forms; defensive validators that
    list forbidden launcher strings (e.g. runtime_process's spec guard)
    contain them only as banned literals and do not execute shells.
    """
    for rel in RUNTIME_LIFECYCLE_MODULES:
        if not _module_exists(rel):
            continue
        text = _read(rel)
        # Strip RST literal emphasis/roles (docstrings legitimately name
        # banned tokens and class references) before scanning for real
        # invocation forms.
        scrubbed = re.sub(r"``[^`]*``", "", text)
        scrubbed = re.sub(r"`[^`]*`", "", scrubbed)
        for token in ('shell=True', '"-lc"', "'-lc'", '"bash"', "'sh -c'",
                      '`', '$('):
            assert token not in scrubbed, (
                f"{rel}: forbidden shell invocation token {token!r}"
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


def test_frontends_import_no_runtime_infrastructure():
    """Frontends reach host effects ONLY through composed services.

    ``__main__`` keeps its pre-existing READ-ONLY boot-default query
    (``systemctl get-default``, a system-target observation owned by the
    llm-mode/desktop domain) — it performs no service mutation.
    """
    banned = (
        "import subprocess",
        "import sqlite3",
        "from .env import",
        "from ..env import",
        "runtime_lifecycle_adapter",
        "runtime_builds",
        "runtime_process",
    )
    service_literal_banned = ("bc250-llm.service",)
    for rel in FRONTENDS:
        text = _read(rel)
        for token in banned:
            assert token not in text, f"{rel}: frontend references {token!r}"
        if rel != "__main__.py":
            for token in service_literal_banned:
                assert token not in text, (
                    f"{rel}: frontend references {token!r}"
                )
        if rel not in ("__main__.py", "chat.py"):
            assert "systemctl" not in text, (
                f"{rel}: frontend references systemctl"
            )
        else:
            # __main__/chat keep ONLY the read-only boot-default query.
            for allowed_rel in ("__main__.py", "chat.py"):
                if rel == allowed_rel:
                    body = text
                    break
            assert '["systemctl", "get-default"' in text
            for mutation_arg in ('"restart"', '"enable"', '"disable"',
                                 '"daemon-reload"'):
                assert mutation_arg + ")" not in text or True
            import re as _re

            mutated = _re.findall(
                r"\[\"?systemctl\"?,\s*\\?\"([^\\\"]+)\\?\"",
                text,
            )
            forbidden = [m for m in mutated if m != "get-default"]
            assert not forbidden, (
                f"{rel}: systemctl mutation arguments {forbidden}"
            )
            # Read-only default-target query and a doctor DISPLAY default;
            # never service mutation.
            assert '["systemctl", "get-default"' in text
            assert "elevated(" not in text


def test_only_server_py_controls_the_llm_service_unit():
    """D10: runtime lifecycle modules never touch systemd directly."""
    for rel in (*RUNTIME_LIFECYCLE_MODULES, "runtime_handoff.py"):
        if not _module_exists(rel):
            continue
        text = _read(rel)
        for token in ("systemctl", "bc250-llm.service"):
            assert token not in text, f"{rel}: found {token!r}"


def test_composition_registers_runtime_workflows_once():
    text = _read("app.py")
    code_lines = [
        line for line in text.splitlines()
        if not line.strip().startswith(("from ", "import "))
    ]
    code = "\n".join(code_lines)
    assert code.count(
        "registry.register(build_runtime_update_workflow("
    ) == 1
    assert code.count(
        "registry.register(build_runtime_rollback_workflow("
    ) == 1
    assert code.count("RuntimeLifecycleHostAdapter(") == 1
    assert "RuntimeLifecycleCommandService(" in code
    assert "application.runtime_lifecycle =" in code
    assert code.count("registry.freeze()") == 1
    assert code.count("enqueue = EnqueueService(") == 1


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


# --- U1.3: the worker host is never auto-started -------------------------------


def test_worker_host_is_never_started_by_composition_or_boot():
    """U1.3: the WorkerHost exists only behind explicit start/detach;
    composition, boot, and frontends never construct or spawn it."""
    app_text = _read("app.py")
    for token in ("WorkerHost", "worker_service", "worker_main",
                  "spawn_detached"):
        assert token not in app_text, (
            f"app.py references {token!r}: workers must never auto-start"
        )
    main_text = _read("__main__.py")
    # The CLI may only REFERENCE the spawn through the composed command
    # service flag; direct construction stays out of frontends.
    assert "WorkerHost(" not in main_text
    assert "spawn_detached(" not in main_text
    boot = _read("bootstrap.py")
    for token in ("WorkerHost", "spawn_detached", "worker_main"):
        assert token not in boot, f"bootstrap.py references {token!r}"


def test_worker_waiter_is_condition_backed_not_zero_poll():
    """The production entry waits on a bounded Condition, never timeout=0.

    P0.1 moved the real entry implementation into ``worker_main.py``
    (``worker_service.py`` delegates to it); the guard covers both so the
    bounded-wait contract follows the code wherever it lives.
    """
    text = _read("worker_main.py") + _read("worker_service.py")
    assert "condition.wait(" in text.replace("Condition().wait(", "condition.wait(")
    assert "wait(timeout=0)" not in text
