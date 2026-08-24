"""Pure legacy canonicalization (Session 4.1 §3.5).

The v1→v5 interpretation of pre-SQLite payloads is a pure function: no file
I/O, no staging JSON, no writable store. The importer feeds a parsed dict in
and receives the canonical v5 dict back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.legacy_schema import canonicalize_legacy_state

FIXTURES = Path(__file__).parent / "fixtures"


def test_v1_payload_canonicalizes_to_v5_without_io():
    raw = {
        "schema_version": 1,
        "setup_phase": 7,
        "optimizations": {"gpu_enabled": True},
        "system_mode": "llm",
    }
    state = canonicalize_legacy_state(raw, boot_id="fixed-boot")
    assert state["schema_version"] == 5
    # v2 phase shift for pre-phase-6 layouts.
    assert state["setup_phase"] == 8
    # v3 boot policy defaults and llm-session rename.
    assert state["boot_policy"] == "desktop"
    assert state["system_mode"] == "llm-session"
    assert state["llm_session_boot_id"] == "fixed-boot"
    # v4 gpu flag rename.
    assert state["optimizations"]["gpu_tuning_enabled"] is True
    # v5 telemetry keys.
    assert state["bench_history"] == []
    assert state["thermal_watchdog_state"] == "nominal"


def test_stale_llm_session_reconciles_against_explicit_boot_id():
    raw = {
        "schema_version": 5,
        "boot_policy": "desktop",
        "system_mode": "llm-session",
        "llm_session_boot_id": "old-boot",
        "llm_mode_done": True,
    }
    state = canonicalize_legacy_state(
        json.loads(json.dumps(raw)), boot_id="new-boot"
    )
    assert state["system_mode"] == "desktop"
    assert state["llm_mode_done"] is False
    assert state["llm_session_boot_id"] is None


def test_non_mapping_payload_is_rejected_before_any_io():
    with pytest.raises(ValueError, match="JSON object"):
        canonicalize_legacy_state([1, 2, 3])  # type: ignore[arg-type]


def test_frozen_v5_fixture_matches_importer_canonicalization(tmp_path):
    fixture = FIXTURES / "state_v5.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    pure = canonicalize_legacy_state(raw, boot_id="test-boot")

    from bc250_llm_mode.legacy_import import LegacyImporter

    class _NoopRunner:
        def emit(self, *_lines):
            pass

    importer = LegacyImporter.__new__(LegacyImporter)
    importer.warnings = []
    importer.counts = {}
    migrated = importer.canonicalize_raw(raw)
    assert migrated == pure
