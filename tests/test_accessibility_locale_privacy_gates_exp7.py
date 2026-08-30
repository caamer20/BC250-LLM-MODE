"""EXP-7 exit gates for keyboard, scale, terms, privacy, and local help."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bc250_llm_mode.command_palette import palette_commands
from bc250_llm_mode.gui.theme import DARK, LIGHT, contrast_ratio
from bc250_llm_mode.message_catalog import GLOSSARY, MESSAGE_CATALOG, message_for
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.privacy_center import PrivacyCenterQueryService
from bc250_llm_mode.services import UserPreferencesService


ROOT = Path(__file__).parent.parent
GUI = ROOT / "bc250_llm_mode" / "gui"


def test_every_semantic_theme_color_meets_text_contrast_floor():
    for theme in (LIGHT, DARK):
        for field in (
            "foreground", "muted", "accent", "good", "warning", "danger", "focus",
        ):
            assert contrast_ratio(getattr(theme, field), theme.background) >= 4.5
        assert contrast_ratio(theme.foreground, theme.surface) >= 4.5
        assert contrast_ratio(theme.muted, theme.surface) >= 4.5
    with pytest.raises(ValueError, match="#RRGGBB"):
        contrast_ratio("red", "#ffffff")


def test_scale_choices_cover_125_through_200_and_are_strictly_typed():
    for scale in (100, 125, 150, 175, 200):
        result = UserPreferencesService.validate({"ui_scale_percent": scale})
        assert result["ui_scale_percent"] == scale
    for invalid in (99, 110, 201, 125.0, "125", True):
        with pytest.raises(ValueError, match="ui_scale_percent"):
            UserPreferencesService.validate({"ui_scale_percent": invalid})
    settings = (GUI / "settings_page.py").read_text(encoding="utf-8")
    assert "(100, 125, 150, 175, 200)" in settings


def test_shell_shortcuts_are_local_and_enter_is_not_a_global_mutation_key():
    shell = (GUI / "shell.py").read_text(encoding="utf-8")
    for binding in (
        "<Control-Key-", "<Control-k>", "<Control-f>", "<Control-l>",
        "<Escape>",
    ):
        assert binding in shell
    assert 'self.bind("<Return>"' not in shell
    assert "self.drawer.clear()" in shell
    widgets = (GUI / "widgets.py").read_text(encoding="utf-8")
    assert "cancel_button.focus_set()" in widgets
    assert 'typed_entry.bind("<Return>"' in widgets


def test_stream_rendering_never_steals_keyboard_focus():
    tree = ast.parse((GUI / "chat_page.py").read_text(encoding="utf-8"))
    methods = {
        node.name: ast.get_source_segment(
            (GUI / "chat_page.py").read_text(encoding="utf-8"), node
        ) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "focus_set" not in methods["_flush_chunks"]
    assert "focus_set" not in methods["_finish"]
    assert "focus_set" not in methods["_render_transcript"]


def test_tables_have_text_or_details_alternatives_and_status_is_not_color_only():
    required_pairs = {
        "models_page.py": ("Treeview(", "detail_body"),
        "activity.py": ("Treeview(", "detail_message"),
        "maintenance_page.py": ("Treeview(", "_impact"),
        "profiles_page.py": ("Treeview(", "_detail"),
        "connections_page.py": ("Treeview(", "_client_detail"),
        "repair_page.py": ("Treeview(", "_cleanup_accessible"),
        "settings_page.py": ("Treeview(", "_privacy_detail"),
        "setup_forms.py": ("Treeview(", "model_selection_summary"),
    }
    for name, expected in required_pairs.items():
        source = (GUI / name).read_text(encoding="utf-8")
        assert all(marker in source for marker in expected), name
    assert "PASS" in (GUI / "connections_page.py").read_text(encoding="utf-8")
    assert "NEEDS ACTION" in (GUI / "connections_page.py").read_text(encoding="utf-8")


def test_consistency_critical_terms_and_fallbacks_stay_unambiguous():
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(GUI.glob("*.py"))
    )
    for banned in ('"Context / user"', '"User slots"', '"Stop after min"'):
        assert banned not in combined
    assert "Context per user" in combined
    assert "Concurrent user slots" in combined
    assert GLOSSARY["installed"].definition != GLOSSARY["verified"].definition
    assert GLOSSARY["active"].definition != GLOSSARY["installed"].definition
    assert message_for("UNMAPPED_SAFE_CODE").code == "UNMAPPED_SAFE_CODE"
    assert "ACTION_FAILED" in MESSAGE_CATALOG


def test_palette_protected_actions_always_open_preview_routes():
    protected = [
        command for command in palette_commands(
            setup_complete=True, operational=True
        ) if command.protected
    ]
    assert protected
    assert all(
        command.route in {
            "home", "models", "maintenance/repair", "maintenance/updates"
        }
        for command in protected
    )
    assert all(command.description.lower().find("open") >= 0 for command in protected)


def test_privacy_inventory_has_no_fake_management_or_telemetry_toggle(tmp_path):
    snapshot = PrivacyCenterQueryService(AppPaths.temporary(tmp_path)).snapshot()
    assert "no telemetry" in snapshot.telemetry.lower()
    assert all(item.manage_route and item.manage_label for item in snapshot.items)
    settings = (GUI / "settings_page.py").read_text(encoding="utf-8")
    assert "telemetry_var" not in settings and "Enable telemetry" not in settings
    assert "application.privacy.snapshot()" in settings


def test_help_palette_glossary_and_icons_need_no_network():
    targets = (
        ROOT / "bc250_llm_mode" / "message_catalog.py",
        ROOT / "bc250_llm_mode" / "command_palette.py",
        GUI / "help_page.py",
        GUI / "widgets.py",
    )
    banned = {"httpx", "urllib", "requests", "socket", "subprocess", "webbrowser"}
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                imported.add((node.module or "").split(".")[0])
        assert not (banned & imported), path.name
    assert not list((ROOT / "bc250_llm_mode" / "assets").glob("*.url"))


def test_tk_limitations_are_visible_and_not_claimed_as_qualified():
    help_source = (GUI / "help_page.py").read_text(encoding="utf-8")
    document = (ROOT / "docs/accessibility-privacy.md").read_text(encoding="utf-8")
    for phrase in (
        "Known limitation", "screen-reader", "pending physical Bazzite",
    ):
        assert phrase in help_source
    normalized = " ".join(document.split())
    assert "full screen-reader parity is still evidence-pending" in normalized
    assert "not physical qualification evidence" in normalized
