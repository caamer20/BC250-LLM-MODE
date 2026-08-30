"""Frozen developer gates for EXP-4 authority and physical handoff."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from bc250_llm_mode.app import Application
from bc250_llm_mode.notifications import DesktopNotificationAdapter
from bc250_llm_mode.paths import AppPaths


ROOT = Path(__file__).parent.parent
PACKAGE = ROOT / "bc250_llm_mode"


def test_notification_producers_are_only_on_existing_post_commit_boundaries():
    engine = (PACKAGE / "operations" / "engine.py").read_text(encoding="utf-8")
    host = (PACKAGE / "operations" / "worker_host.py").read_text(encoding="utf-8")
    worker_main = (PACKAGE / "worker_main.py").read_text(encoding="utf-8")
    thermal = (PACKAGE / "thermals.py").read_text(encoding="utf-8")
    checks = (PACKAGE / "maintenance_checks.py").read_text(encoding="utf-8")
    app = (PACKAGE / "app.py").read_text(encoding="utf-8")

    assert "outcome = self._execute(operation_id)" in engine
    assert "self.terminal_observer(operation_id)" in engine
    assert "terminal_observer=self.terminal_observer" in host
    assert "application.operation_notifications.after_execution" in worker_main
    assert "_persist_stopped(store, state)" in thermal
    assert "_notify_transition(store, STOPPED)" in thermal
    assert "notification_observer(snapshot, observed_at)" in checks
    assert "terminal_observer=application.operation_notifications.after_execution" in app

    for source in (engine, host, worker_main, thermal, checks, app):
        assert "notification_thread" not in source
        assert "notification_poll" not in source


def test_notification_capability_check_does_not_start_threads():
    before = {thread.ident for thread in threading.enumerate()}
    adapter = DesktopNotificationAdapter(which=lambda _name: None, env={})
    for _index in range(100):
        assert adapter.capability().available is False
    after = {thread.ident for thread in threading.enumerate()}
    assert after == before


def test_support_bundle_includes_bounded_redacted_notification_status(tmp_path):
    application = Application.compose(AppPaths.temporary(tmp_path / "profile"))
    output = tmp_path / "bundle"
    manifest = application.support_bundle.build(output)
    names = {row["path"] for row in manifest.files}
    assert "notifications.json" in names
    payload = json.loads((output / "notifications.json").read_text(encoding="utf-8"))
    assert payload["master_enabled"] is False
    assert len(payload["categories"]) == 9
    assert len(payload["recent"]) <= 20
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "receipt_key", "/root/models", "http://", "https://", "Bearer ",
        "prompt", "completion", "exception",
    ):
        assert forbidden not in serialized


def test_physical_handoff_covers_both_hosts_and_every_required_negative_case():
    text = (ROOT / "docs" / "notification-physical-qualification.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "Bazzite", "CachyOS", "disabled", "unavailable", "duplicate",
        "three-per-hour", "recovery-required", "throttled", "stopped",
        "critical storage", "stale-backup", "nonzero exit", "timeout",
        "physical evidence pending", "exact candidate",
    ):
        assert required in text
    assert "PASS requires both host matrices" in text
