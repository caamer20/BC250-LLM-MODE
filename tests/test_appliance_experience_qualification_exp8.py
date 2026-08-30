"""EXP-8 fail-closed journey, resource, and handoff qualification gates."""

from __future__ import annotations

import ast
from pathlib import Path

from bc250_llm_mode.__main__ import _parser
from bc250_llm_mode.release_policy import EvidenceKind


ROOT = Path(__file__).parent.parent
GUIDE = ROOT / "docs" / "appliance-experience-physical-qualification.md"
GUI = ROOT / "bc250_llm_mode" / "gui"


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _normalized_guide() -> str:
    return " ".join(_guide().split())


def test_exp8_qualification_is_explicitly_pending_and_candidate_bound():
    guide = _normalized_guide()
    assert "Status: PENDING" in guide
    assert "40-character source commit" in guide
    for identity in (
        "policy digest", "artifact-inventory digest", "wheel filename",
        "SHA-256", "release-set digest", "installed-wheel digest",
    ):
        assert identity in guide
    assert "- [x]" not in guide.lower()
    assert "no EXP-8 physical journey" in guide
    assert "another commit" in guide and "invalid" in guide


def test_all_four_host_and_profile_cells_are_required():
    guide = _guide()
    for cell in (
        "bazzite-fresh", "bazzite-upgraded",
        "cachyos-fresh", "cachyos-upgraded",
    ):
        assert cell in guide
    assert "Run J01–J14 independently in all four" in guide


def test_all_fourteen_scripted_journeys_are_frozen_in_order():
    guide = _guide()
    positions = [guide.index(f"### J{number:02d} —") for number in range(1, 15)]
    assert positions == sorted(positions)
    for phrase in (
        "application-menu launch", "five-chapter setup", "existing model",
        "Apply a profile", "Native chat", "PocketPal", "Second client",
        "Interrupted-operation recovery", "quarantine, and Undo",
        "Backup and restore", "application update and rollback",
        "reboot with no model", "support bundle", "Uninstall preserving models",
    ):
        assert phrase in guide


def test_each_journey_records_usability_safety_privacy_and_resources():
    guide = _normalized_guide()
    for field in (
        "outcome", "stable result/error code", "completion state",
        "elapsed seconds", "time-to-first-safe-action", "wrong-turn count",
        "whether a terminal was required", "unclear labels", "recovery actions",
        "resource sample IDs", "safety defects", "privacy defects",
        "accessibility defects", "tracked issue IDs",
    ):
        assert field in guide
    assert "Journey notes remain local and are not telemetry" in guide


def test_required_participant_roles_and_non_developer_acceptance_are_present():
    guide = _guide()
    for role in (
        "Linux user unfamiliar with llama.cpp", "existing BC-250 owner",
        "recovery operator", "keyboard-only pass", "mobile-client pass",
    ):
        assert role in guide
    assert "HUMAN_ACCEPTANCE" in guide
    assert "must be a non-developer" in guide
    assert "may not waive thermal" in guide


def test_physical_resource_thresholds_match_the_frozen_gui_budget():
    guide = _normalized_guide()
    for threshold in (
        "≤1.5 seconds", "≤90 MiB idle RSS", "<1% settled CPU", "≤3",
        "≤5 MiB retained RSS", "<50 ms p95", "30 seconds", "5 seconds",
        "1 second", "24-hour minimum", "eight-hour",
    ):
        assert threshold in guide
    for measured in (
        "file-descriptor", "socket counts", "queue depth", "MemAvailable",
        "notification", "100 route changes", "main-thread callback",
    ):
        assert measured in guide


def test_resource_protocol_preserves_low_ram_and_privacy_invariants():
    guide = _normalized_guide()
    for invariant in (
        "multi-GiB GUI allocation", "full-model host read", "--no-mmap",
        "Do not add a telemetry", "never prompt or completion content",
    ):
        assert invariant in guide
    assert "raw exception text" in guide
    assert "authorization headers" in guide


def test_structural_limits_back_the_physical_measurement_protocol():
    expected = {
        "app.py": ("MAX_GUI_EVENTS = 512",),
        "tasks.py": ("MAX_RESULT_EVENTS = 512", "self.action =", "self.observation =", "self.chat ="),
        "models_page.py": ("MAX_MODEL_ROWS = 100",),
        "activity.py": ("selected[:100]",),
        "widgets.py": ("MAX_LOG_LINES = 2000", "MAX_LOG_BYTES = 2 * 1024 * 1024"),
    }
    for name, markers in expected.items():
        source = (GUI / name).read_text(encoding="utf-8")
        assert all(marker in source for marker in markers), name
    maintenance = (ROOT / "bc250_llm_mode" / "maintenance_center.py").read_text(
        encoding="utf-8"
    )
    chat = (ROOT / "bc250_llm_mode" / "chat_service.py").read_text(
        encoding="utf-8"
    )
    assert "MAX_MAINTENANCE_ITEMS = 5" in maintenance
    assert "MAX_CHAT_MESSAGES = 500" in chat


def test_one_refresh_owner_and_three_named_lanes_remain_structural():
    after_owners = []
    for path in sorted(GUI.glob("*.py")):
        if ".after(" in path.read_text(encoding="utf-8"):
            after_owners.append(path.name)
    assert after_owners == ["refresh.py"]
    tasks = ast.parse((GUI / "tasks.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tasks)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "BoundedTaskLane"
    ]
    assert len(calls) == 3


def test_documented_operator_commands_match_the_real_cli_parser():
    parser = _parser()
    commands = (
        ("desktop-integration", "plan"),
        ("operations", "list", "--active"),
        ("repair", "preview", "restart-model-server"),
        ("storage", "cleanup", "--dry-run", "--mode", "QUARANTINE"),
        ("backup", "create", "before-maintenance"),
        ("restore", "inspect", "backup-id"),
        ("connections", "instructions", "pocketpal"),
        ("update", "import-bundle", "/media/release.tar"),
        ("desktop-mode", "--now"),
        ("privacy",),
    )
    for argv in commands:
        parsed = parser.parse_args(list(argv))
        assert parsed.command


def test_qualification_routes_to_existing_specialized_physical_protocols():
    guide = _guide()
    required = (
        "connection-physical-qualification.md",
        "application-update-physical-qualification.md",
        "release/evidence/README.md",
    )
    assert all(name in guide for name in required)
    for name in required[:2]:
        assert (ROOT / "docs" / name).is_file()
    assert (ROOT / required[2]).is_file()


def test_exp8_does_not_invent_new_or_unsigned_release_evidence():
    kinds = {kind.value for kind in EvidenceKind}
    assert {
        "DEFAULT_TEST_SUITE", "SLOW_SECURITY_STRESS", "CLEAN_WHEEL_SMOKE",
        "UPGRADE_MATRIX", "HARDWARE_QUALIFICATION", "SOAK_TEST",
        "BACKUP_RESTORE_HARDWARE", "SECURITY_REVIEW", "HUMAN_ACCEPTANCE",
    } <= kinds
    assert not list((ROOT / "release" / "evidence").glob("*.json"))
    guide = _guide()
    assert "unit test" in guide and "never proof" in guide
    assert "sole release evaluator" in guide
