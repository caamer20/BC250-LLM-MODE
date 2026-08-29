"""GUI-8 removal and package-completeness gates."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parent.parent
GUI = ROOT / "bc250_llm_mode" / "gui"


def test_legacy_gui_modules_are_deleted():
    assert not (GUI / "dashboard.py").exists()
    assert not (GUI / "forms.py").exists()


def test_concrete_window_has_no_legacy_mixin_hierarchy_or_step_export():
    shell = ast.parse((GUI / "shell.py").read_text(encoding="utf-8"))
    window = next(
        node for node in shell.body
        if isinstance(node, ast.ClassDef) and node.name == "ApplicationWindow"
    )
    assert len(window.bases) == 1
    assert isinstance(window.bases[0], ast.Name)
    assert window.bases[0].id == "SetupWindow"
    production = "\n".join(
        path.read_text(encoding="utf-8") for path in GUI.glob("*.py")
    )
    assert "class FormsMixin" not in production
    assert "class SetupPageMixin" not in production
    assert "STEP_TITLES" not in production


def test_required_unified_gui_modules_are_present():
    required = {
        "activity.py", "app.py", "chat_page.py", "help_page.py",
        "home_page.py", "models_page.py", "refresh.py", "routes.py",
        "settings_page.py", "setup_forms.py", "setup_page.py", "shell.py",
        "system_page.py", "tasks.py", "theme.py", "view_state.py",
        "widgets.py",
    }
    assert required <= {path.name for path in GUI.glob("*.py")}


def test_wizard_name_is_only_a_documented_compatibility_alias():
    source = (GUI / "__init__.py").read_text(encoding="utf-8")
    assert 'name in {"Wizard", "ApplicationWindow"}' in source
    assert "STEP_TITLES" not in source
