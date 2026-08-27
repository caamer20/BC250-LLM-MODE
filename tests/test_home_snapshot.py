"""P5 §11.1: the unified home snapshot — query-only discipline, card
timestamps/staleness, evidence-backed green claims, and deterministic
overall composition over a REAL temporary database."""

from __future__ import annotations

import ast
import datetime
import json
from pathlib import Path

import pytest

from bc250_llm_mode import health as H
from bc250_llm_mode import home as home_mod
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.home import HomeQueryService, HOME_SNAPSHOT_SCHEMA_VERSION
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repositories import SettingsRepository, ThermalStateRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

FIXED_NOW = "2026-02-01T12:00:00+00:00"

CARD_NAMES = {
    "identity", "runtime", "model", "inference", "thermal",
    "operations", "storage", "integrations", "backup", "host",
}


class HomeWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.service = HomeQueryService(
            self.units, self.paths, clock=lambda: FIXED_NOW
        )

    def set_settings(self, **values) -> None:
        with self.units.begin() as conn:
            SettingsRepository(conn).set_many(values)

    def snapshot(self):
        return self.service.snapshot()


@pytest.fixture()
def world(tmp_path):
    return HomeWorld(tmp_path)


def test_home_module_is_query_only_by_construction():
    """AST guard: the home snapshot may only open READ units and may
    never import process/HTTP machinery."""
    source = Path(home_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"subprocess", "httpx"}, alias.name
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"subprocess", "httpx"}, node.module
        if isinstance(node, ast.Attribute):
            # units.read() only — never units.begin()
            assert node.attr != "begin", "home snapshot must be query-only"
    assert "elevated(" not in source


def test_every_card_has_timestamp_or_reason(world):
    snap = world.snapshot()
    assert set(snap.cards) == CARD_NAMES
    for name, card in snap.cards.items():
        assert card["as_of"] is not None or card["stale_reason"], (
            f"card {name} has neither a timestamp nor a reason"
        )
        assert "health" in card
        assert card["health"]["state"] in H.HEALTH_STATES


def test_fresh_profile_reports_honest_non_green_states(world):
    snap = world.snapshot()
    states = {n: c["health"]["effective_state"] for n, c in snap.cards.items()}
    assert states["identity"] == "UNAVAILABLE"      # setup not complete
    assert states["runtime"] == "UNAVAILABLE"       # nothing published
    assert states["model"] == "UNAVAILABLE"         # nothing selected
    assert states["inference"] == "UNVERIFIED"      # never probed
    assert states["thermal"] == "READY"             # latch nominal
    assert states["operations"] == "READY"          # nothing active
    assert states["integrations"] == "UNAVAILABLE"  # no credential
    assert states["backup"] == "UNAVAILABLE"        # none recorded
    assert states["host"] == "UNVERIFIED"           # never probed
    # Overall is dominated by the UNAVAILABLE conditions, never green.
    assert snap.overall.state == "UNAVAILABLE"
    assert snap.overall.state != "READY"


def test_green_overall_requires_evidence_on_every_ready_dimension(world):
    # Seed a fully verified appliance.
    world.set_settings(
        setup_complete=True,
        current_model="tiny",
        backup_last_completed_at="2026-02-01T02:00:00+00:00",
        host_capability_summary={"vulkan": True},
        host_capability_observed_at="2026-02-01T02:00:00+00:00",
    )
    with world.units.begin() as conn:
        build_id = "llamacpp:sha256:" + "a1" * 32  # 80 chars per CHECK
        digest = "c3" * 32  # 64 hex chars
        conn.execute(
            "INSERT INTO model_artifacts (id, content_digest, byte_size,"
            " canonical_path, storage_state, trust_state, format,"
            " created_at, validated_at) VALUES"
            " ('art-1', ?, 100,"
            " '/models/.bc250-artifacts/sha256/tiny.gguf',"
            " 'MANAGED', 'VERIFIED', 'gguf',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (digest,),
        )
        conn.execute(
            "INSERT INTO model_installations (alias, path, quant,"
            " display_name, sampling_json, provenance, validation_status,"
            " imported_at, artifact_id) VALUES ('tiny', '/models/tiny.gguf',"
            " 'Q4_K_M', 'Tiny', '{}', 'test', 'validated',"
            " '2026-01-01T00:00:00Z', 'art-1')"
        )
        conn.execute(
            "INSERT INTO runtime_builds (build_id, component,"
            " manifest_version, manifest_json, manifest_digest,"
            " source_commit, requested_ref, recipe_version,"
            " provenance_class, created_at) VALUES"
            " (?, 'llamacpp', 1, '{}', ?, NULL, 'v0.0.1', 1,"
            " 'IMMUTABLE_SOURCE', '2026-01-01T00:00:00Z')",
            (build_id, digest),
        )
        conn.execute(
            "INSERT INTO runtime_component_state (component,"
            " promoted_build_id, generation, updated_at) VALUES"
            " ('llamacpp', ?, 1, '2026-01-15T00:00:00Z')",
            (build_id,),
        )
        conn.execute(
            "INSERT INTO known_good_runtime (id, model_alias, context,"
            " slots, profile_id, runtime_json, runtime_fingerprint,"
            " runtime_component_identity, verified_at) VALUES"
            " (1, 'tiny', 8192, 1, 'p', '{}', 'fp', ?,"
            " '2026-01-15T00:00:00Z')",
            (build_id,),
        )
        # A fresh durable inference probe: green requires bounded evidence.
        conn.execute(
            "INSERT INTO runtime_observations (key, payload_json,"
            " observed_at, stale) VALUES ('last_inference_probe', ?,"
            " ?, 0)",
            (json.dumps({"ok": True}), FIXED_NOW),
        )
    # Gateway provisioned with its secret file present.
    (world.paths.app_dir / "gateway-credential").write_text("s" * 48)
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO gateway_credentials (id, fingerprint, scopes,"
            " created_at, revision) VALUES (1, ?, 'inference:read',"
            " '2026-01-20T00:00:00Z', 1)",
            ("a" * 64,),
        )

    snap = world.snapshot()
    ready_cards = {
        name for name, card in snap.cards.items()
        if card["health"]["effective_state"] == "READY"
    }
    # Every green claim carries bounded evidence + timestamp + non-inferred
    # basis (enforced again by HealthDimension construction).
    for name in ready_cards:
        h = snap.cards[name]["health"]
        assert h["evidence"], name
        assert h["as_of"], name
        assert h["basis"] in {"verified", "observed"}, name
    assert snap.overall.state == "READY"
    assert snap.overall.basis in {"verified", "observed"}


def test_stale_inference_probe_is_never_green(world):
    old = (
        datetime.datetime.fromisoformat(FIXED_NOW)
        - datetime.timedelta(days=3)
    ).isoformat()
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO runtime_observations (key, payload_json,"
            " observed_at, stale) VALUES ('last_inference_probe', ?,"
            " ?, 0)",
            (json.dumps({"ok": True}), old),
        )
    snap = world.snapshot()
    inference = snap.cards["inference"]
    assert inference["stale"] is True
    assert inference["stale_reason"]
    assert inference["health"]["state"] == "UNVERIFIED"
    assert inference["health"]["effective_state"] == "UNVERIFIED"
    assert inference["ready"] is False


def test_fresh_inference_probe_is_green(world):
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO runtime_observations (key, payload_json,"
            " observed_at, stale) VALUES ('last_inference_probe', ?,"
            " ?, 0)",
            (json.dumps({"ok": True}), FIXED_NOW),
        )
    snap = world.snapshot()
    assert snap.cards["inference"]["health"]["effective_state"] == "READY"
    assert snap.cards["inference"]["ready"] is True


def test_thermal_latch_blocks_the_appliance(world):
    with world.units.begin() as conn:
        ThermalStateRepository(conn).set("stopped", {"gpu": 95})
    snap = world.snapshot()
    assert snap.cards["thermal"]["health"]["effective_state"] == "BLOCKED"
    assert snap.cards["thermal"]["safe_reset_eligible"] is True
    assert "thermals reset" in snap.cards["thermal"]["reset_guidance"]
    assert snap.overall.state == "BLOCKED"


def test_recovery_required_operation_dominates(world):
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version,"
            " request_json, state, surface, created_at, updated_at) VALUES"
            " ('op-stuck', 'MODEL_IMPORT', 1, '{}', 'RECOVERY_REQUIRED',"
            " 'test', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
    snap = world.snapshot()
    assert snap.cards["operations"]["health"]["effective_state"] == (
        "RECOVERY_REQUIRED")
    assert snap.overall.state == "RECOVERY_REQUIRED"


def test_revoked_gateway_credential_blocks_integrations(world):
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO gateway_credentials (id, fingerprint, scopes,"
            " created_at, revoked_at, revision) VALUES (1, ?, 'x',"
            " '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', 2)",
            ("b" * 64,),
        )
    snap = world.snapshot()
    integrations = snap.cards["integrations"]
    assert integrations["gateway"]["revoked"] is True
    assert integrations["health"]["effective_state"] == "BLOCKED"
    assert snap.overall.state == "BLOCKED"


def test_stale_backup_is_degraded_and_labeled(world):
    world.set_settings(
        backup_last_completed_at="2026-01-01T00:00:00+00:00",  # 31 days old
    )
    snap = world.snapshot()
    backup = snap.cards["backup"]
    assert backup["stale"] is True
    assert backup["stale_reason"]
    assert backup["health"]["effective_state"] == "UNVERIFIED"

    world.set_settings(
        backup_last_completed_at="2026-02-01T02:00:00+00:00",  # fresh
    )
    backup = world.snapshot().cards["backup"]
    assert backup["stale"] is False
    assert backup["health"]["effective_state"] == "READY"


def test_storage_pressure_degrades_storage_card(world, monkeypatch):
    class _Usage:
        total = 100
        free = 4  # 96% used

    monkeypatch.setattr(
        home_mod.shutil, "disk_usage", lambda _p: _Usage())
    snap = world.snapshot()
    storage = snap.cards["storage"]
    assert storage["health"]["effective_state"] == "DEGRADED"
    assert storage["pressure_fraction"] >= 0.90


def test_snapshot_is_side_effect_free(world):
    before = world.snapshot().to_dict()
    with world.units.read() as conn:
        revision_before = conn.execute(
            "SELECT value_json FROM settings WHERE key = '__revision'"
        ).fetchone()
        rows_before = conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_observations"
        ).fetchone()["n"]
    again = world.snapshot().to_dict()
    assert before == again
    with world.units.read() as conn:
        revision_after = conn.execute(
            "SELECT value_json FROM settings WHERE key = '__revision'"
        ).fetchone()
        rows_after = conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_observations"
        ).fetchone()["n"]
    assert revision_before == revision_after
    assert rows_before == rows_after


def test_to_dict_contract(world):
    payload = world.snapshot().to_dict()
    assert payload["schema_version"] == HOME_SNAPSHOT_SCHEMA_VERSION
    assert payload["generated_at"] == FIXED_NOW
    assert payload["overall"]["name"] == "overall"
    assert set(payload["cards"]) == CARD_NAMES
    json.dumps(payload)  # fully JSON-serializable


def test_home_parser_surface():
    from bc250_llm_mode import __main__ as entry

    assert entry._parser().parse_args(("home",)).command == "home"


def test_home_cli_prints_the_composed_snapshot(tmp_path, monkeypatch, capsys):
    """Exit gate: the CLI surface reads the SAME composed home service."""
    from bc250_llm_mode import __main__ as entry
    from bc250_llm_mode.app import Application

    world = HomeWorld(tmp_path)
    application = Application.compose(world.paths)
    monkeypatch.setattr(
        Application, "compose",
        classmethod(lambda cls, *a, **k: application),
    )
    assert entry.cli(("home",)) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["schema_version"] == HOME_SNAPSHOT_SCHEMA_VERSION
    assert set(payload["cards"]) == CARD_NAMES
    assert payload["overall"]["state"] in H.HEALTH_STATES
