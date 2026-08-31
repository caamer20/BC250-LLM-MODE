"""GUI-5 contracts for System, Settings, Help, Activity, and log drawer."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.gui.activity import (  # noqa: E402
    ACTIVITY_FILTERS,
    filter_operations,
)
from bc250_llm_mode.gui.system_page import (  # noqa: E402
    ServiceCardView,
    system_card_views,
)
from bc250_llm_mode.gui.widgets import (  # noqa: E402
    MAX_LOG_BYTES,
    MAX_LOG_LINES,
    _bounded_log,
)
from bc250_llm_mode.log_tail import LogTailService  # noqa: E402
from bc250_llm_mode.operations.views import OperationSummary  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402


PACKAGE = Path(__file__).parent.parent / "bc250_llm_mode"


def _summary(state: str, suffix: str) -> OperationSummary:
    return OperationSummary(
        operation_id=f"op-{suffix}", kind="MODEL_ACTIVATE", kind_version=1,
        title="Activate model", state=state, phase="apply",
        progress_current=1, progress_total=3, progress_unit="steps",
        created_at="2026-08-29T00:00:00Z",
        updated_at=f"2026-08-29T00:00:0{suffix}Z", surface="gui",
        cancellable=False, resumable=False, retryable=False,
        recoverable=False, dismissable=False, failure_code=None,
        recovery_recommendation="Wait for verification.",
    )


def test_activity_filters_are_closed_and_bounded():
    rows = (
        _summary("RUNNING", "1"),
        _summary("RECOVERY_REQUIRED", "2"),
        _summary("SUCCEEDED", "3"),
        _summary("FAILED_SAFE", "4"),
    )
    assert ACTIVITY_FILTERS == ("Active", "Needs attention", "Recent", "All")
    assert {row.state for row in filter_operations(rows, "Active")} == {"RUNNING"}
    assert {row.state for row in filter_operations(rows, "Needs attention")} == {
        "RECOVERY_REQUIRED", "FAILED_SAFE",
    }
    assert len(filter_operations(rows * 40, "All")) == 100
    with pytest.raises(ValueError, match="unknown activity filter"):
        filter_operations(rows, "Everything")


def test_activity_has_no_independent_timer_or_polling_owner():
    source = (PACKAGE / "gui" / "activity.py").read_text(encoding="utf-8")
    assert ".after(" not in source
    assert "start_polling" not in source
    assert "Toplevel" not in source


def test_system_cards_offer_one_primary_service_direction():
    cards = system_card_views(
        home={"cards": {"thermal": {"health": {"state": "READY"}}},
              "system_mode": "desktop"},
        server={"active": False}, webui={"installed": True, "running": False},
        tailscale={"daemon_active": False, "connected": False},
        sharing={"status": "disabled"}, runtime={}, backups=2,
        platform_label="Bazzite",
    )
    assert all(isinstance(card, ServiceCardView) for card in cards)
    by_key = {card.key: card for card in cards}
    assert by_key["server"].primary_code == "start-server"
    assert by_key["server"].more_code is None
    assert by_key["webui"].primary_code == "start-webui"
    assert by_key["webui"].more_code == "update-webui"
    assert by_key["webui"].more_label == "Update pinned Open WebUI"
    assert by_key["remote"].primary_code == "start-tailscale"
    assert by_key["host"].primary_code == "llm-mode"
    assert by_key["host"].primary_label == "Enter LLM Mode…"
    assert "http://127.0.0.1:3000" in by_key["webui"].detail

    shared = system_card_views(
        home={"cards": {"thermal": {"health": {"state": "READY"}}},
              "system_mode": "desktop"},
        server={"active": True},
        webui={"installed": True, "running": True,
               "url": "http://127.0.0.1:3000"},
        tailscale={
            "daemon_active": True, "connected": True,
            "addresses": ["100.115.57.31", "fd7a:115c:a1e0::4838:3920"],
        },
        sharing={
            "webui_enabled": True,
            "webui_url": "https://bc250.example.ts.net:8443/",
        },
        runtime={}, backups=0, platform_label="Bazzite",
    )
    webui = {card.key: card for card in shared}["webui"]
    assert "Local: http://127.0.0.1:3000" in webui.detail
    assert "Tailnet HTTPS: https://bc250.example.ts.net:8443/" in webui.detail
    assert "Device Tailscale IP: 100.115.57.31" in webui.detail
    assert (webui.primary_code, webui.more_code) == ("stop-webui", "update-webui")
    remote = {card.key: card for card in shared}["remote"]
    assert "IP 100.115.57.31" in remote.detail

    stale_record = system_card_views(
        home={"system_mode": "llm-session", "desktop_active": True},
        server={}, webui={}, tailscale={}, sharing={}, runtime={}, backups=0,
        platform_label="Bazzite",
    )
    assert {card.key: card for card in stale_record}["host"].primary_code == "llm-mode"

    console = system_card_views(
        home={"system_mode": "llm-session", "desktop_active": False},
        server={}, webui={}, tailscale={}, sharing={}, runtime={}, backups=0,
        platform_label="Bazzite",
    )
    assert {card.key: card for card in console}["host"].primary_code == "desktop"


def test_log_tail_service_is_closed_bounded_and_missing_safe(tmp_path):
    paths = AppPaths.temporary(tmp_path)
    paths.ensure_directories()
    source = paths.logs_dir / "setup.log"
    source.write_text("\n".join(f"line-{index}" for index in range(2500)), encoding="utf-8")
    service = LogTailService(paths)
    assert service.sources == ("setup", "server", "worker")
    assert service.tail("setup", lines=3) == ("line-2497", "line-2498", "line-2499")
    assert service.tail("server") == ()
    with pytest.raises(ValueError, match="unknown log source"):
        service.tail("arbitrary-path")
    with pytest.raises(ValueError, match="1..2000"):
        service.tail("setup", lines=MAX_LOG_LINES + 1)


def test_log_widget_bounds_lines_and_encoded_bytes():
    assert len(_bounded_log(["small"] * (MAX_LOG_LINES + 100))) == MAX_LOG_LINES
    retained = _bounded_log(["x" * 4096] * 1000)
    assert sum(len(line.encode("utf-8")) for line in retained) <= MAX_LOG_BYTES


def test_unified_shell_routes_system_settings_help_and_composed_logs():
    source = (PACKAGE / "gui" / "shell.py").read_text(encoding="utf-8")
    for page in ("SystemPage", "SettingsPage", "HelpPage"):
        assert page in source
    assert "application.logs.tail(" in source
    assert "dashboard" not in source.lower()


def test_system_page_has_immediate_status_and_periodic_retry():
    page = (PACKAGE / "gui" / "system_page.py").read_text(encoding="utf-8")
    shell = (PACKAGE / "gui" / "shell.py").read_text(encoding="utf-8")
    assert '"loading", "System status", "Checking…"' in page
    assert '"unavailable", "System status", "Retrying…"' in page
    refresh_block = shell.split("def _refresh_cycle(self) -> None:", 1)[1]
    assert "Route.SYSTEM" in refresh_block


def test_system_page_routes_the_pinned_openwebui_update_through_its_service():
    page = (PACKAGE / "gui" / "system_page.py").read_text(encoding="utf-8")
    assert 'elif code == "update-webui"' in page
    assert "self.application.openwebui.update(state, runner)" in page


def test_system_page_confirms_and_activates_full_screen_llm_console():
    page = (PACKAGE / "gui" / "system_page.py").read_text(encoding="utf-8")
    assert '"Enter LLM Mode?"' in page
    assert '"Leave desktop"' in page
    assert "activate_console_now=True" in page


def test_every_gui_module_avoids_direct_infrastructure_imports():
    banned_modules = {
        "server", "openwebui", "tailscale", "sharing", "llmmode",
        "desktop", "optimize", "model_manager", "repositories", "db",
        "unit_of_work", "subprocess", "sqlite3",
    }
    violations: list[str] = []
    for path in sorted((PACKAGE / "gui").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".")[-1]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[-1] in banned_modules:
                        violations.append(f"{path.name}:{alias.name}")
            if module in banned_modules:
                violations.append(f"{path.name}:{module}")
    assert violations == []


def test_settings_are_staged_and_help_exports_redacted_support():
    settings = (PACKAGE / "gui" / "settings_page.py").read_text(encoding="utf-8")
    help_source = (PACKAGE / "gui" / "help_page.py").read_text(encoding="utf-8")
    assert "Preview" in settings and "Discard changes" in settings
    assert "application.optimizations.validate_selection" in settings
    assert "application.activation.activate" in settings
    assert "askdirectory" in help_source
    assert "application.doctor.run()" in help_source
    assert "application.support_bundle.build(" in help_source
