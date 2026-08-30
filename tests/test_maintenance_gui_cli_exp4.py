"""EXP-4 Maintenance inbox, explicit checks, Home, and CLI parity."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode import __main__ as cli_module  # noqa: E402
from bc250_llm_mode.app import Application  # noqa: E402
from bc250_llm_mode.db import initialize_and_close  # noqa: E402
from bc250_llm_mode.gui.home_page import build_home_view  # noqa: E402
from bc250_llm_mode.gui.maintenance_page import (  # noqa: E402
    MaintenancePage,
    build_maintenance_view,
)
from bc250_llm_mode.gui.routes import PRIMARY_ROUTES, Route  # noqa: E402
from bc250_llm_mode.gui.shell import ApplicationWindow  # noqa: E402
from bc250_llm_mode.maintenance_center import (  # noqa: E402
    MaintenanceCenterQueryService,
)
from bc250_llm_mode.maintenance_checks import MaintenanceCheckService  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory  # noqa: E402


NOW = "2026-08-29T12:00:00Z"


def _home() -> dict:
    def card(state="READY", **values):
        return {
            "health": {"state": state, "effective_state": state, "evidence": "ok"},
            **values,
        }

    return {
        "cards": {
            "identity": card(setup_complete=True),
            "runtime": card(),
            "model": card(desired="test-model", installed_count=1),
            "inference": card(),
            "thermal": card(),
            "operations": card(summary={"active_count": 0}),
            "storage": card(available_bytes=10 * 1024**3),
            "integrations": card(),
        }
    }


def _item(category: str, priority: int, code: str) -> dict:
    return {
        "category": category,
        "priority": priority,
        "code": code,
        "title": f"{category.title()} item",
        "impact": "Review this appliance evidence.",
        "evidence_age_seconds": 60,
        "evidence_freshness": "cached",
        "resource": "appliance",
        "primary_action": "maintenance/check",
        "details_action": "maintenance/details",
        "dismissible": category not in {"SAFETY", "RECOVERY", "SECURITY", "INTEGRITY"},
    }


def test_maintenance_view_sorts_and_caps_rows_without_mutating_input():
    rows = [_item("OPERATION", 7, f"op-{index}") for index in range(8)]
    rows.insert(4, _item("SECURITY", 3, "security"))
    snapshot = {"generated_at": NOW, "items": rows, "sources": []}
    status = {"master_enabled": False, "categories": {}}
    view = build_maintenance_view(snapshot, status)
    assert len(view.items) == 5
    assert view.items[0]["code"] == "security"
    rows[4]["title"] = "mutated"
    assert view.items[0]["title"] == "Security item"


def test_home_surfaces_maintenance_before_chat_but_not_before_safety():
    maintenance = {"items": [_item("SECURITY", 3, "security")]}
    view = build_home_view(_home(), maintenance)
    assert view.primary.code == "maintenance"
    assert view.primary.route == Route.MAINTENANCE.value
    assert ("Maintenance", Route.MAINTENANCE.value) in view.shortcuts

    home = _home()
    home["cards"]["thermal"]["health"]["state"] = "BLOCKED"
    home["cards"]["thermal"]["health"]["effective_state"] = "BLOCKED"
    assert build_home_view(home, maintenance).primary.code == "thermal"


def test_maintenance_is_one_window_primary_route(tmp_path):
    application = Application.compose(AppPaths.temporary(tmp_path))
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "setup_complete": True, "disclaimer_ack": True}
    )
    window = ApplicationWindow(application, management=True)
    try:
        assert Route.MAINTENANCE in PRIMARY_ROUTES
        window.navigate(Route.MAINTENANCE)
        assert window._route is Route.MAINTENANCE
        assert isinstance(window._page, MaintenancePage)
    finally:
        window.destroy()


def test_maintenance_page_has_no_timer_thread_tray_or_secondary_window():
    source = Path("bc250_llm_mode/gui/maintenance_page.py").read_text(encoding="utf-8")
    assert "request_observation(" in source
    assert "self.shell._work(" in source
    for forbidden in (".after(", "threading", "Toplevel", "messagebox", "tray"):
        assert forbidden not in source


def test_explicit_check_persists_only_closed_redacted_evidence(tmp_path):
    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    initialize_and_close(paths.database_path)
    units = UnitOfWorkFactory(paths.database_path)

    class Usage:
        free = 9 * 1024**3
        total = 16 * 1024**3

    snapshot = MaintenanceCenterQueryService(
        units, paths.models_dir, clock=lambda: NOW, disk_usage=lambda _path: Usage()
    )
    canary = "hf_secret-canary-/root/models/private.gguf"
    doctor = SimpleNamespace(run=lambda: SimpleNamespace(
        overall="WARN",
        findings=(
            SimpleNamespace(id="INFERENCE", severity="PASS"),
            SimpleNamespace(id="INSECURE_TOPOLOGY", severity="PASS"),
            SimpleNamespace(id=canary, severity="FAIL"),
        ),
    ))
    storage = SimpleNamespace(report=lambda: {
        "free_bytes": Usage.free,
        "total_bytes": Usage.total,
        "reclaimable_bytes": 1024,
        "private_path": canary,
    })
    connections = lambda: {
        "ready": False,
        "sharing": {"public_funnel": False, "url": canary},
        "checks": [
            {"id": "gateway", "passed": False},
            {"id": canary, "passed": False},
        ],
    }
    report = MaintenanceCheckService(
        units,
        snapshot=snapshot,
        doctor=doctor,
        storage=storage,
        connection_observer=connections,
        services_observer=lambda: {"model_server": True, "detail": canary},
        thermal_reader=lambda: 77.25,
        clock=lambda: NOW,
    ).run()
    assert report["sources_completed"]["doctor"] is True
    with units.read() as conn:
        rows = conn.execute(
            "SELECT key, payload_json FROM runtime_observations ORDER BY key"
        ).fetchall()
    assert len(rows) == 6
    blob = json.dumps([dict(row) for row in rows], sort_keys=True)
    assert canary not in blob
    assert "/root/models" not in blob and "hf_" not in blob
    assert "gateway" in blob


def test_cli_parity_and_cleanup_is_explicit_dry_run(tmp_path, monkeypatch, capsys):
    preference_calls = []
    preference_status = {
        "master_enabled": False,
        "master_revision": 1,
        "categories": {
            category: {"enabled": False, "revision": 1}
            for category in (
                "OPERATION_SUCCESS", "OPERATION_FAILURE", "THERMAL_WARNING",
                "THERMAL_STOP", "STORAGE_CRITICAL", "BACKUP_FAILURE",
                "BACKUP_STALE", "REMOTE_SAFETY_DISABLE", "APPLICATION_UPDATE",
            )
        },
    }
    preferences = SimpleNamespace(
        status=lambda: preference_status,
        apply=lambda changes, expected_revisions: preference_calls.append(
            (changes, expected_revisions)
        ) or {"changed": changes},
    )
    fake = SimpleNamespace(
        operational=True,
        read_model=lambda: {"logs_dir": str(tmp_path / "logs")},
        maintenance_snapshot=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(to_dict=lambda: {"items": []})
        ),
        maintenance_checks=SimpleNamespace(run=lambda: {"checked_at": NOW}),
        storage_capacity=SimpleNamespace(
            dry_run_cleanup=lambda: {"dry_run": True, "deleted": []}
        ),
        notification_preferences=preferences,
        notifications=SimpleNamespace(
            status=lambda: {"recent": [], "master_enabled": False},
            test=lambda: SimpleNamespace(
                to_dict=lambda: {"delivered": False, "reason_code": "CAPABILITY_UNAVAILABLE"}
            ),
        ),
    )
    monkeypatch.setattr(Application, "compose", lambda *_a, **_k: fake)
    monkeypatch.setattr(cli_module, "configure_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli_module, "CommandRunner", lambda *_a, **_k: SimpleNamespace()
    )

    assert cli_module.main(["maintenance", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"items": []}
    assert cli_module.main(["maintenance", "check"]) == 0
    assert json.loads(capsys.readouterr().out)["checked_at"] == NOW
    assert cli_module.cli(["maintenance", "cleanup"]) == 1
    assert "--dry-run" in capsys.readouterr().err
    assert cli_module.main(["maintenance", "cleanup", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True

    assert cli_module.main(["notifications", "set", "all", "on"]) == 0
    assert len(preference_calls[-1][0]) == 10
    assert all(preference_calls[-1][0].values())
    json.loads(capsys.readouterr().out)
    assert cli_module.main(["notifications", "test"]) == 0
    assert json.loads(capsys.readouterr().out)["reason_code"] == "CAPABILITY_UNAVAILABLE"
