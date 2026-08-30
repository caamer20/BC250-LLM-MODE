"""EXP-7 bounded palette, privacy inventory, and formatter contracts."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from bc250_llm_mode.__main__ import _parser
from bc250_llm_mode.command_palette import (
    MAX_PALETTE_COMMANDS,
    MAX_PALETTE_QUERY,
    MAX_PALETTE_RESULTS,
    match_palette,
    palette_commands,
)
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.presentation import (
    FormatConventions,
    format_bytes,
    format_duration,
    format_number,
    format_temperature,
    format_timestamp,
    format_tokens,
)
from bc250_llm_mode.privacy_center import (
    MAX_PRIVACY_ITEMS,
    PrivacyCenterQueryService,
)


ROOT = Path(__file__).parent.parent
PACKAGE = ROOT / "bc250_llm_mode"


def test_palette_is_closed_bounded_local_and_deterministic():
    commands = palette_commands(setup_complete=True, operational=True)
    assert len(commands) <= MAX_PALETTE_COMMANDS
    assert len({command.command_id for command in commands}) == len(commands)
    assert match_palette(commands, "model install") == match_palette(
        commands, "model install"
    )
    assert len(match_palette(commands, "", limit=MAX_PALETTE_RESULTS)) <= 12
    assert match_palette(commands, "x" * (MAX_PALETTE_QUERY + 100)) == ()
    with pytest.raises(ValueError, match="1..12"):
        match_palette(commands, "", limit=13)


def test_protected_palette_entries_only_carry_routes_and_plain_context():
    commands = palette_commands(setup_complete=True, operational=True)
    protected = [command for command in commands if command.protected]
    assert {command.command_id for command in protected} == {
        "start-stop-model", "install-model", "guided-repair",
        "storage-cleanup", "application-update",
    }
    assert all(command.enabled for command in protected)
    assert all(isinstance(command.route_context(), dict) for command in protected)
    assert all(not callable(value) for command in protected for value in vars(command).values())
    shell = (PACKAGE / "gui/shell.py").read_text(encoding="utf-8")
    assert "self.navigate(command.route, command.route_context())" in shell
    assert "command.action" not in shell and "command.execute" not in shell


def test_setup_palette_does_not_offer_management_mutations():
    commands = palette_commands(setup_complete=False, operational=True)
    assert [command.command_id for command in commands] == ["continue-setup"]
    assert commands[0].route == "setup"


def test_privacy_inventory_names_every_required_owner_and_real_location(tmp_path):
    paths = AppPaths.temporary(tmp_path)
    assert not paths.app_dir.exists()
    snapshot = PrivacyCenterQueryService(paths).snapshot()
    assert not paths.app_dir.exists(), "privacy query must not create or inspect storage"
    assert len(snapshot.items) <= MAX_PRIVACY_ITEMS
    by_id = {item.item_id: item for item in snapshot.items}
    assert {
        "conversations", "logs", "operation-events", "benchmarks",
        "credentials", "openwebui", "backups", "support-bundles",
        "update-bundles", "notices", "update-network",
    } <= set(by_id)
    assert by_id["conversations"].location == str(paths.conversations_dir)
    assert by_id["logs"].location == str(paths.logs_dir)
    assert by_id["operation-events"].location == str(paths.database_path)
    assert by_id["backups"].location == str(paths.backups_dir)
    assert by_id["update-bundles"].location == str(paths.application_update_bundles_dir)
    assert "no telemetry" in snapshot.telemetry.lower()
    assert "No automatic update check" in by_id["update-network"].leaves_machine


def test_privacy_snapshot_is_json_safe_and_contains_no_secret_value(tmp_path):
    snapshot = PrivacyCenterQueryService(AppPaths.temporary(tmp_path)).snapshot()
    rendered = json.dumps(snapshot.to_dict(), sort_keys=True)
    assert "prompt" in rendered.lower()
    assert "gateway-credential" not in rendered
    assert "hf_" not in rendered and "Bearer " not in rendered
    assert all(item.manage_route for item in snapshot.items)


def test_presentation_formatters_are_injectable_and_do_not_change_identifiers():
    european = FormatConventions(decimal_separator=",", thousands_separator=".")
    assert format_number(12345.5, decimals=1) == "12,345.5"
    assert format_number(12345.5, decimals=1, conventions=european) == "12.345,5"
    assert format_bytes(12 * 1024**3) == "12.0 GiB"
    assert format_tokens(32768) == "32,768 tokens"
    assert format_temperature(85) == "85.0 °C"
    assert format_duration(3720) == "1 hour 2 minutes"
    assert format_timestamp("bad-value") == "Unknown time"
    opaque = "op-ABCD_1234"
    assert opaque == "op-ABCD_1234"


def test_palette_privacy_and_glossary_import_no_network_or_process_modules():
    for name in ("command_palette.py", "privacy_center.py", "presentation.py", "message_catalog.py"):
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and not node.level
        )
        assert not ({"httpx", "urllib", "requests", "socket", "subprocess"} & imports)


def test_cli_exposes_query_only_privacy_inventory():
    args = _parser().parse_args(["privacy"])
    assert args.command == "privacy"
