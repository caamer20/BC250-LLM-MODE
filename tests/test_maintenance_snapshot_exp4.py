"""EXP-4 bounded, deterministic, query-only maintenance snapshot."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from bc250_llm_mode import maintenance_center as module
from bc250_llm_mode.app import Application
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.maintenance_center import (
    FRESHNESS_STATES,
    MAX_MAINTENANCE_ITEMS,
    MaintenanceCenterQueryService,
    PRIORITY_POLICY,
    SOURCE_NAMES,
)
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repositories import SettingsRepository, ThermalStateRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


NOW = "2026-08-29T12:00:00Z"
OLD = "2026-08-01T00:00:00Z"


class _Usage:
    total = 100 * 1024**3
    free = 50 * 1024**3


class _LowUsage(_Usage):
    free = 512 * 1024**2


class _Platform:
    def status(self):
        return {"integration_tier": "native", "distribution": {"id": "bazzite"}}


def _world(tmp_path, *, low_storage: bool = False):
    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    initialize_and_close(paths.database_path)
    units = UnitOfWorkFactory(paths.database_path)
    service = MaintenanceCenterQueryService(
        units,
        paths.models_dir,
        platform=_Platform(),
        clock=lambda: NOW,
        disk_usage=lambda _path: _LowUsage() if low_storage else _Usage(),
    )
    return paths, units, service


def _set(units, **values):
    with units.begin() as conn:
        SettingsRepository(conn).set_many(values)


def test_snapshot_module_has_no_mutation_or_expensive_probe_surface():
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {alias.name for alias in node.names} & {
                "subprocess", "httpx", "urllib", "hashlib"
            }
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"subprocess", "httpx", "urllib", "hashlib"}
        if isinstance(node, ast.Attribute):
            assert node.attr != "begin", "maintenance refresh must be query-only"
    assert ".run()" not in source
    assert "rglob(" not in source and "walk(" not in source


def test_snapshot_is_bounded_serializable_and_honest_about_freshness(tmp_path):
    _paths, _units, service = _world(tmp_path)
    payload = service.snapshot().to_dict()
    assert len(payload["items"]) <= MAX_MAINTENANCE_ITEMS
    assert payload["checked_live"] is False
    assert tuple(source["name"] for source in payload["sources"]) == SOURCE_NAMES
    assert all(source["freshness"] in FRESHNESS_STATES for source in payload["sources"])
    by_name = {source["name"]: source for source in payload["sources"]}
    assert by_name["storage"]["freshness"] == "live"
    assert by_name["platform"]["freshness"] == "live"
    assert by_name["doctor"]["freshness"] == "not_checked"
    assert by_name["server"]["freshness"] == "not_checked"
    assert by_name["application_update"]["freshness"] == "not_checked"
    assert payload["items"][0]["code"] == "BACKUP_STALE"
    json.dumps(payload)


def test_closed_priority_policy_caps_home_at_five(tmp_path):
    _paths, units, service = _world(tmp_path, low_storage=True)
    _set(
        units,
        current_model="missing-model",
        funnel_enabled=True,
        tailscale_serving=True,
        backup_last_completed_at=OLD,
        verified_application_update={"verified": True, "version": "9.9.9"},
    )
    with units.begin() as conn:
        ThermalStateRepository(conn).set("stopped", {"temperature_c": 95})
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version, "
            "request_json, state, surface, created_at, updated_at) VALUES "
            "('recovery', 'MODEL_IMPORT', 1, '{}', 'RECOVERY_REQUIRED', "
            "'test', ?, ?)",
            (OLD, OLD),
        )
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version, "
            "request_json, state, surface, error_code, created_at, updated_at, "
            "finished_at) VALUES ('failed', 'MODEL_IMPORT', 1, '{}', "
            "'FAILED_SAFE', 'test', 'TEST_FAILURE', ?, ?, ?)",
            (OLD, OLD, OLD),
        )
    items = service.snapshot().items
    assert len(items) == MAX_MAINTENANCE_ITEMS
    assert [item.category for item in items] == list(PRIORITY_POLICY[:5])
    assert [item.priority for item in items] == [1, 2, 3, 4, 5]
    assert all(not item.dismissible for item in items[:4])
    assert items[4].dismissible is True
    assert all(item.impact and item.resource and item.primary_action for item in items)
    assert all(item.details_action for item in items)


def test_lower_priorities_are_stable_when_no_higher_issue_exists(tmp_path):
    _paths, units, service = _world(tmp_path)
    _set(
        units,
        backup_last_completed_at=OLD,
        verified_application_update={"verified": True, "version": "9.9.9"},
    )
    with units.begin() as conn:
        conn.execute(
            "INSERT INTO backup_sets (backup_id, manifest_digest, "
            "storage_path_label, verification_state, created_at, revision) "
            "VALUES ('backup-old', ?, 'backup-old.tar', 'verified', ?, 1)",
            ("a" * 64, OLD),
        )
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version, "
            "request_json, state, surface, error_code, created_at, updated_at, "
            "finished_at) VALUES ('failed', 'MODEL_IMPORT', 1, '{}', "
            "'FAILED_SAFE', 'test', 'TEST_FAILURE', ?, ?, ?)",
            (OLD, OLD, OLD),
        )
        conn.execute(
            "INSERT INTO runtime_observations (key, payload_json, observed_at, "
            "stale) VALUES ('last_update_check', ?, ?, 0)",
            (json.dumps({"verified": True, "version": "9.9.9"}), NOW),
        )
    items = service.snapshot().items
    assert [item.category for item in items] == ["BACKUP", "OPERATION", "UPDATE"]
    assert [item.code for item in items] == [
        "BACKUP_STALE", "OPERATION_FAILED", "VERIFIED_APPLICATION_UPDATE_AVAILABLE"
    ]


def test_snapshot_never_writes_last_checked_state(tmp_path):
    _paths, units, service = _world(tmp_path)
    with units.read() as conn:
        before = conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()["n"]
        observation_before = conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_observations"
        ).fetchone()["n"]
    first = service.snapshot().to_dict()
    second = service.snapshot().to_dict()
    assert first == second
    with units.read() as conn:
        after = conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()["n"]
        observation_after = conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_observations"
        ).fetchone()["n"]
    assert (before, observation_before) == (after, observation_after)


def test_composition_exposes_query_service_without_replacing_uninstall(tmp_path):
    paths = AppPaths.temporary(tmp_path / "profile")
    application = Application.compose(paths)
    assert application.maintenance is not None
    assert isinstance(application.maintenance_snapshot, MaintenanceCenterQueryService)
    assert len(application.maintenance_snapshot.snapshot().items) <= 5

