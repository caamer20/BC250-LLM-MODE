"""EXP-4 migration 013, notification privacy, dedupe, and adapter gates."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bc250_llm_mode import db
from bc250_llm_mode.db import initialize, initialize_and_close, open_database
from bc250_llm_mode.notifications import (
    CATEGORIES,
    MASTER,
    MAX_RECEIPTS,
    DeliveryResult,
    DesktopNotificationAdapter,
    NotificationCapability,
    NotificationCoordinator,
    NotificationError,
    NotificationEvent,
    NotificationPreferenceRepository,
    NotificationPreferenceService,
    NotificationReceiptRepository,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


NOW = "2026-08-29T12:00:00Z"


def _fresh(tmp_path):
    path = tmp_path / "state.db"
    initialize_and_close(path)
    return path, UnitOfWorkFactory(path)


def _create_v12(path, monkeypatch, *, legacy_enabled=None):
    migrations = db.MIGRATIONS
    version = db.SCHEMA_VERSION
    monkeypatch.setattr(db, "MIGRATIONS", migrations[:-1])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 12)
    conn = open_database(path, mode="migration")
    try:
        assert initialize(conn) == 12
        conn.execute("CREATE TABLE preserved_exp4_marker(value TEXT)")
        conn.execute("INSERT INTO preserved_exp4_marker VALUES ('keep-me')")
        if legacy_enabled is not None:
            conn.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES "
                "('notifications_enabled', ?, 'old')",
                ("true" if legacy_enabled else "false",),
            )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(db, "MIGRATIONS", migrations)
    monkeypatch.setattr(db, "SCHEMA_VERSION", version)


def _enable(units, *categories):
    with units.begin() as conn:
        repo = NotificationPreferenceRepository(conn, clock=lambda: NOW)
        repo.set(MASTER, True, expected_revision=repo.get(MASTER).revision)
        for category in categories:
            repo.set(
                category, True, expected_revision=repo.get(category).revision
            )


class _Adapter:
    def __init__(self, *, available=True, result=None):
        self.available = available
        self.result = result or DeliveryResult(True, "notify-send", "DELIVERED")
        self.calls = []

    def capability(self):
        return NotificationCapability(
            self.available,
            "notify-send" if self.available else "unavailable",
            "DELIVERED" if self.available else "CAPABILITY_UNAVAILABLE",
        )

    def deliver(self, category, *, test=False):
        self.calls.append((category, test))
        return self.result


def _operation(index, *, category="OPERATION_SUCCESS", terminal="SUCCEEDED"):
    return NotificationEvent.operation(
        operation_id=f"operation-{index}",
        terminal_state=terminal,
        revision=2,
        category=category,
    )


def test_migration_013_preserves_v12_and_requires_explicit_legacy_opt_in(
    tmp_path, monkeypatch
):
    off_path = tmp_path / "off.db"
    _create_v12(off_path, monkeypatch)
    conn = open_database(off_path, mode="migration")
    try:
        assert initialize(conn) == 13 == db.SCHEMA_VERSION
        assert conn.execute(
            "SELECT value FROM preserved_exp4_marker"
        ).fetchone()[0] == "keep-me"
        rows = NotificationPreferenceRepository(conn).list()
        assert {row.category for row in rows} == {MASTER, *CATEGORIES}
        assert all(row.enabled is False for row in rows)
        assert conn.execute(
            "SELECT COUNT(*) FROM notification_receipts"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    on_path = tmp_path / "on.db"
    _create_v12(on_path, monkeypatch, legacy_enabled=True)
    conn = open_database(on_path, mode="migration")
    try:
        initialize(conn)
        assert all(row.enabled for row in NotificationPreferenceRepository(conn).list())
    finally:
        conn.close()


def test_migration_013_is_atomic_on_mid_migration_failure(tmp_path, monkeypatch):
    path = tmp_path / "atomic.db"
    _create_v12(path, monkeypatch)
    migration = db.MIGRATIONS[-1]
    broken = (
        13,
        migration[1],
        migration[2][:-1] + ("INSERT INTO absent_table VALUES (1)",),
    )
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:-1] + (broken,))
    conn = open_database(path, mode="migration")
    try:
        with pytest.raises(sqlite3.OperationalError):
            initialize(conn)
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "notification_preferences" not in tables
        assert "notification_receipts" not in tables
        assert conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 12
    finally:
        conn.close()


def test_preferences_are_closed_revision_fenced_and_fresh_defaults_off(tmp_path):
    _path, units = _fresh(tmp_path)
    service = NotificationPreferenceService(units, clock=lambda: NOW)
    status = service.status()
    assert status["master_enabled"] is False
    assert all(not row["enabled"] for row in status["categories"].values())
    changed = service.set(MASTER, True, expected_revision=1)
    assert changed["enabled"] is True and changed["revision"] == 2
    with pytest.raises(NotificationError, match="revision conflict"):
        service.set(MASTER, False, expected_revision=1)
    with pytest.raises(NotificationError, match="unknown"):
        service.set("MODEL_RESPONSE", True, expected_revision=1)


def test_event_identity_is_stable_hashed_and_rejects_secret_path_body_inputs():
    first = _operation(1)
    second = _operation(1)
    assert first.receipt_key == second.receipt_key
    assert len(first.receipt_key) == 64
    assert "operation-1" not in first.receipt_key
    for canary in (
        "/root/models/private.gguf",
        "Bearer secret-token",
        "hf_this_is_a_token",
        "-----BEGIN PRIVATE KEY-----",
    ):
        with pytest.raises(NotificationError):
            NotificationEvent.operation(
                operation_id=canary,
                terminal_state="SUCCEEDED",
                revision=2,
                category="OPERATION_SUCCESS",
            )


def test_cross_restart_dedupe_delivers_once_and_receipt_has_no_raw_identity(tmp_path):
    path, units = _fresh(tmp_path)
    _enable(units, "OPERATION_SUCCESS")
    adapter_a = _Adapter()
    first = NotificationCoordinator(
        units, adapter=adapter_a, clock=lambda: NOW
    ).notify(_operation(7))
    assert first.delivery_state == "DELIVERED"
    assert adapter_a.calls == [("OPERATION_SUCCESS", False)]

    adapter_b = _Adapter()
    repeated = NotificationCoordinator(
        units, adapter=adapter_b, clock=lambda: NOW
    ).notify(_operation(7))
    assert repeated.delivery_state == "SUPPRESSED"
    assert repeated.reason_code == "DUPLICATE"
    assert adapter_b.calls == []
    dump = path.read_bytes()
    for canary in (b"operation-7", b"A model operation finished"):
        assert canary not in dump


def test_preferences_capability_and_adapter_failure_never_raise_domain_failure(tmp_path):
    _path, units = _fresh(tmp_path)
    off = _Adapter()
    outcome = NotificationCoordinator(
        units, adapter=off, clock=lambda: NOW
    ).notify(_operation(1))
    assert outcome.reason_code == "PREFERENCE_DISABLED" and off.calls == []

    _enable(units, "OPERATION_FAILURE")
    unavailable = _Adapter(available=False)
    event = _operation(
        2, category="OPERATION_FAILURE", terminal="FAILED_SAFE"
    )
    outcome = NotificationCoordinator(
        units, adapter=unavailable, clock=lambda: NOW
    ).notify(event)
    assert outcome.reason_code == "CAPABILITY_UNAVAILABLE"

    failing = _Adapter(result=DeliveryResult(False, "notify-send", "ADAPTER_FAILED"))
    event = _operation(
        3, category="OPERATION_FAILURE", terminal="FAILED_SAFE"
    )
    outcome = NotificationCoordinator(
        units, adapter=failing, clock=lambda: NOW
    ).notify(event)
    assert outcome.delivery_state == "FAILED"
    assert outcome.reason_code == "ADAPTER_FAILED"


def test_global_and_category_rate_limits_are_durable(tmp_path):
    _path, units = _fresh(tmp_path)
    _enable(units, "OPERATION_SUCCESS", "OPERATION_FAILURE")
    adapter = _Adapter()
    coordinator = NotificationCoordinator(units, adapter=adapter, clock=lambda: NOW)
    assert coordinator.notify(_operation(1)).delivery_state == "DELIVERED"
    category_limited = coordinator.notify(_operation(2))
    assert category_limited.reason_code == "RATE_CATEGORY"
    # Distinct failure category is allowed once, then category-limited.
    assert coordinator.notify(_operation(
        3, category="OPERATION_FAILURE", terminal="FAILED_SAFE"
    )).delivery_state == "DELIVERED"
    assert coordinator.notify(_operation(
        4, category="OPERATION_FAILURE", terminal="FAILED_SAFE"
    )).reason_code == "RATE_CATEGORY"

    # Seed a third delivered category to reach the global bound.
    _enable(units, "THERMAL_WARNING")
    thermal = NotificationEvent.thermal(
        category="THERMAL_WARNING", transitioned_at=NOW, latch_state="throttled"
    )
    assert coordinator.notify(thermal).delivery_state == "DELIVERED"
    _enable(units, "THERMAL_STOP")
    critical = NotificationEvent.thermal(
        category="THERMAL_STOP", transitioned_at="2026-08-29T12:01:00Z",
        latch_state="stopped",
    )
    assert coordinator.notify(critical).reason_code == "RATE_GLOBAL"
    assert len(adapter.calls) == 3


def test_receipts_are_bounded_to_256_and_default_views_are_redacted(tmp_path):
    _path, units = _fresh(tmp_path)
    with units.begin() as conn:
        repo = NotificationReceiptRepository(conn, clock=lambda: NOW)
        for index in range(MAX_RECEIPTS + 12):
            repo.suppress(_operation(index), reason_code="PREFERENCE_DISABLED")
        rows = repo.list_recent(limit=100)
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM notification_receipts"
        ).fetchone()["n"]
    assert count == MAX_RECEIPTS
    assert len(rows) == 100
    assert all("receipt_key" not in row.to_dict() for row in rows)


def test_desktop_adapter_uses_only_fixed_argv_no_shell_or_output(tmp_path):
    executable = str((tmp_path / "notify-send").resolve())
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    adapter = DesktopNotificationAdapter(
        which=lambda _name: executable,
        env={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/session"},
        run=run,
    )
    assert adapter.capability().available is True
    result = adapter.deliver("THERMAL_STOP")
    assert result.delivered is True
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["timeout"] == 3.0
    assert seen["kwargs"]["stdout"] is subprocess.DEVNULL
    assert seen["kwargs"]["stderr"] is subprocess.DEVNULL
    assert seen["argv"][-2:] == [
        "BC250 LLM MODE", "The LLM server stopped for temperature safety."
    ]
    rendered = " ".join(seen["argv"]).lower()
    for canary in ("/root/models", "http://", "bearer", "prompt", "exception"):
        assert canary not in rendered


def test_notification_module_has_no_thread_tray_or_caller_copy_api():
    source = Path(__import__(
        "bc250_llm_mode.notifications", fromlist=["x"]
    ).__file__).read_text(encoding="utf-8")
    assert "threading" not in source.lower()
    assert "tray" not in source.lower()
    assert "title:" not in source and "body:" not in source
    assert "shell=True" not in source

