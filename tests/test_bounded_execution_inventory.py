"""P3 §9.2: the bounded-execution migration inventory.

Every production module that touches processes or HTTP is recorded here
with its owner disposition. The test recomputes an AST census on every
run and FAILS when a NEW external-effect site appears without a
recorded disposition — the migration queue cannot silently grow. The
census also freezes known defects (e.g. ``timeout=None`` in chat) at
their current count so they can only go DOWN.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent / "bc250_llm_mode"

PROCESS_CALL_NAMES = {"run", "Popen", "check_output", "check_call", "call",
                      "getoutput"}

# Dispositions (plan §9.2): every site must name its target contract.
DISPOSITIONS = {
    # The proven port; P3 promotes it into ProcessCommandSpec v2 for all.
    "runtime_process.py": "already_bounded",
    "worker_service.py": "already_bounded",
    # Legacy runner behind server/env/tailscale/openwebui/optimize/GUI:
    # P3 migrates its internals onto the bounded port (DEF-003).
    "logging_utils.py": "migrate_process_port",
    "app.py": "frontend_terminal_launcher",
    "bootstrap.py": "migrate_process_port",
    "hardware.py": "migrate_process_port",
    # Bounded HTTP transport targets (DEF-004).
    "hub_source.py": "migrate_http_transport",
    "chat.py": "migrate_http_transport",
    "__main__.py": "migrate_http_transport",
}


def _module_census(path: Path) -> dict | None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    has_subprocess = False
    http_module = False
    proc_calls = 0
    timeout_none = 0
    shell_kwargs = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    has_subprocess = True
                if alias.name == "httpx":
                    http_module = True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                has_subprocess = True
            if node.module == "httpx":
                http_module = True
        elif isinstance(node, ast.Call):
            parts: list[str] = []
            func = node.func
            while isinstance(func, ast.Attribute):
                parts.append(func.attr)
                func = func.value
            if isinstance(func, ast.Name):
                parts.append(func.id)
            parts.reverse()
            if parts and parts[0] == "subprocess" \
                    and parts[-1] in PROCESS_CALL_NAMES:
                proc_calls += 1
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    shell_kwargs += 1
                if keyword.arg == "timeout":
                    if isinstance(keyword.value, ast.Constant) \
                            and keyword.value.value is None:
                        timeout_none += 1
    if not (has_subprocess or http_module or proc_calls or timeout_none):
        return None
    return {
        "proc_calls": proc_calls,
        "http_module": http_module,
        "shell_kwargs": shell_kwargs,
        "timeout_none": timeout_none,
    }


def actual_census() -> dict[str, dict]:
    out = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        record = _module_census(path)
        if record is not None:
            out[path.relative_to(ROOT).as_posix()] = record
    return out


FROZEN_BASELINE = {
    "__main__.py": {"proc_calls": 0, "http_module": True,
                    "shell_kwargs": 0, "timeout_none": 0},
    "app.py": {"proc_calls": 1, "http_module": False,
               "shell_kwargs": 0, "timeout_none": 0},
    "bootstrap.py": {"proc_calls": 1, "http_module": False,
                     "shell_kwargs": 0, "timeout_none": 0},
    "chat.py": {"proc_calls": 0, "http_module": True,
                "shell_kwargs": 0, "timeout_none": 2},
    "hardware.py": {"proc_calls": 1, "http_module": False,
                    "shell_kwargs": 0, "timeout_none": 0},
    "hub_source.py": {"proc_calls": 0, "http_module": True,
                      "shell_kwargs": 0, "timeout_none": 0},
    "logging_utils.py": {"proc_calls": 1, "http_module": False,
                         "shell_kwargs": 0, "timeout_none": 0},
    "runtime_process.py": {"proc_calls": 1, "http_module": False,
                           "shell_kwargs": 0, "timeout_none": 0},
    "worker_service.py": {"proc_calls": 1, "http_module": False,
                          "shell_kwargs": 0, "timeout_none": 0},
}


def test_external_effect_sites_match_the_recorded_inventory():
    """Any new subprocess/httpx/timeout=None site must be added here WITH a
    disposition — otherwise this fails and the drift stops at review."""
    actual = actual_census()
    assert actual == FROZEN_BASELINE


def test_every_recorded_site_names_a_target_contract():
    for filename in FROZEN_BASELINE:
        assert filename in DISPOSITIONS, f"{filename} lacks a disposition"
    for filename, disposition in DISPOSITIONS.items():
        assert disposition in {
            "already_bounded",
            "migrate_process_port",
            "migrate_http_transport",
            "frontend_terminal_launcher",
        }, disposition


def test_no_shell_invocation_anywhere_in_production():
    census = actual_census()
    offenders = {f: r["shell_kwargs"] for f, r in census.items()
                 if r["shell_kwargs"]}
    assert not offenders, f"shell=True is forbidden: {offenders}"


def test_known_timeout_none_defects_never_increase():
    """DEF-004 tracking: chat currently holds TWO unbounded HTTP calls
    (the non-stream POST and the SSE stream); P3 §9.4 must drive this
    to zero without ever letting it grow."""
    actual = actual_census()
    total = sum(r["timeout_none"] for r in actual.values())
    assert total <= 2
