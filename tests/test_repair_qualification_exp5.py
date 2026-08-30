"""Frozen EXP-5 exit-policy guards."""

from __future__ import annotations

import ast
from pathlib import Path

from bc250_llm_mode.__main__ import _parser
from bc250_llm_mode.db import SCHEMA_VERSION
from bc250_llm_mode.operations.model import OperationType
from bc250_llm_mode.repair_center import REPAIR_ACTIONS, REPAIR_ACTION_IDS


ROOT = Path(__file__).parents[1]


def test_exp5_closed_catalogue_and_cleanup_version_do_not_claim_migration_014():
    assert SCHEMA_VERSION == 13
    assert OperationType.STORAGE_CLEANUP.value == "STORAGE_CLEANUP"
    assert len(REPAIR_ACTIONS) == len(REPAIR_ACTION_IDS) == 15
    assert len(set(REPAIR_ACTION_IDS)) == 15
    assert all(action.mutation_steps for action in REPAIR_ACTIONS)
    assert all(action.success_probe_id for action in REPAIR_ACTIONS)


def test_exp5_cli_parity_requires_exact_preview_and_confirmation():
    parse = _parser().parse_args
    assert parse(("repair", "list")).repair_action == "list"
    repair = parse((
        "repair", "run", "return-to-desktop",
        "--preview", "a" * 64, "--confirm", "REPAIR-ABC",
    ))
    assert repair.preview == "a" * 64 and repair.confirm == "REPAIR-ABC"
    undo = parse((
        "undo", "run", "cleanup:source:target",
        "--preview", "b" * 64, "--confirm", "UNDO-ABC",
    ))
    assert undo.undo_action == "run"
    cleanup = parse((
        "storage", "cleanup", "--apply", "--preview", "c" * 64,
        "--confirm", "CLEANUP-ABC",
    ))
    assert cleanup.apply and not cleanup.dry_run


def test_exp5_gui_has_no_mutation_adapter_dynamic_route_or_second_timer():
    source = (ROOT / "bc250_llm_mode/gui/repair_page.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any("repair_adapter" in name for name in imports)
    assert not any("repositories" in name for name in imports)
    assert "subprocess" not in imports
    assert "import_module" not in source and "eval(" not in source
    assert ".after(" not in source
    assert "tk.Tk(" not in source and "tk.Toplevel(" not in source


def test_exp5_physical_evidence_remains_explicitly_pending():
    guide = (ROOT / "docs/repair-physical-qualification.md").read_text(
        encoding="utf-8"
    )
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized_guide = " ".join(guide.split())
    assert "developer-qualified; physical evidence pending" in guide
    assert "does not grant release eligibility" in normalized_guide
    assert "was not fabricated" in agents
    assert "No physical PASS is inferred from developer tests" in readme
