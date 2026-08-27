"""P5 §11.3: the read-only doctor — stable finding IDs, bounded severity,
evidence, and recommended commands. The P5 exit gate requires the doctor
to catch these seeded failures: DB corruption, stale lease, mismatched
handoff, bad digest, thermal latch, low disk, insecure topology, and stale
backup. Each is pinned here against a REAL temporary database.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bc250_llm_mode import doctor as doctor_mod
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.doctor import (
    DB_INTEGRITY,
    DoctorService,
    DOCTOR_SCHEMA_VERSION,
    HANDOFF_MISMATCH,
    INFERENCE,
    INSECURE_TOPOLOGY,
    LOW_DISK,
    MODEL_DIGEST,
    SECRET_PERMS,
    SEVERITY_FAIL,
    SEVERITY_PASS,
    SEVERITY_WARN,
    STALE_BACKUP,
    STALE_LEASE,
    THERMAL_LATCH,
    severity_rank,
)
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repositories import SettingsRepository, ThermalStateRepository
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

FIXED_NOW = "2026-02-01T12:00:00+00:00"


class DoctorWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.service = DoctorService(
            self.units, self.paths, clock=lambda: FIXED_NOW
        )

    def set_settings(self, **values) -> None:
        with self.units.begin() as conn:
            SettingsRepository(conn).set_many(values)

    def run(self):
        return self.service.run()

    def by_id(self, report, finding_id):
        return [f for f in report.findings if f.id == finding_id]


@pytest.fixture()
def world(tmp_path):
    return DoctorWorld(tmp_path)


def test_doctor_module_is_read_only_by_construction():
    """AST guard: the doctor opens only READ units and imports no
    process/HTTP machinery; it never deletes anything."""
    import ast

    source = Path(doctor_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"subprocess", "httpx"}, alias.name
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"subprocess", "httpx"}, node.module
        if isinstance(node, ast.Attribute):
            assert node.attr != "begin", "doctor must be query-only"
    assert "elevated(" not in source
    assert "os.remove" not in source and "shutil.rmtree" not in source


def test_severity_ordering_is_deterministic():
    assert severity_rank(SEVERITY_FAIL) < severity_rank(SEVERITY_WARN)
    assert severity_rank(SEVERITY_WARN) < severity_rank("INFO")
    assert severity_rank("INFO") < severity_rank(SEVERITY_PASS)
    with pytest.raises(ValueError):
        severity_rank("MOSTLY_OK")


def test_unknown_finding_id_and_severity_are_rejected():
    from bc250_llm_mode.doctor import Finding

    with pytest.raises(ValueError, match="finding id"):
        Finding(id="NOT_A_FINDING", severity=SEVERITY_PASS,
                title="t", evidence="e")
    with pytest.raises(ValueError, match="severity"):
        Finding(id=DB_INTEGRITY, severity="GREEN", title="t", evidence="e")


def test_clean_appliance_reports_no_failures(world):
    report = world.run()
    assert report.schema_version == DOCTOR_SCHEMA_VERSION
    assert report.generated_at == FIXED_NOW
    assert report.overall != SEVERITY_FAIL
    ids = {f.id for f in report.findings}
    # The durable checks always report their stable IDs.
    for expected in (DB_INTEGRITY, STALE_LEASE, THERMAL_LATCH,
                     INSECURE_TOPOLOGY, LOW_DISK):
        assert expected in ids
    json.dumps(report.to_dict())  # fully serializable


def test_doctor_catches_seeded_db_corruption(world):
    # Corrupt the middle of the SQLite file so the read unit cannot open.
    data = bytearray(world.paths.database_path.read_bytes())
    mid = len(data) // 2
    for i in range(mid, mid + 64):
        data[i] = 0xFF
    world.paths.database_path.write_bytes(bytes(data))

    report = world.run()
    findings = world.by_id(report, DB_INTEGRITY)
    assert findings and findings[0].severity == SEVERITY_FAIL
    assert findings[0].recommended_command
    assert report.overall == SEVERITY_FAIL


def test_doctor_catches_stale_lease(world):
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version,"
            " request_json, state, surface, created_at, updated_at) VALUES"
            " ('op-1', 'MODEL_IMPORT', 1, '{}', 'RUNNING', 'test',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO operation_leases (resource_key, operation_id,"
            " owner, lease_revision, acquired_at, heartbeat_at, expires_at)"
            " VALUES ('model-acquisition', 'op-1', 'dead-worker', 1,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',"
            " '2026-01-01T00:05:00Z')"  # long expired vs FIXED_NOW
        )
    report = world.run()
    findings = world.by_id(report, STALE_LEASE)
    assert findings and findings[0].severity == SEVERITY_WARN
    assert "dead-worker" in findings[0].evidence or \
           "model-acquisition" in findings[0].evidence
    assert findings[0].recommended_command


def test_doctor_catches_mismatched_handoff(world):
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO known_good_runtime (id, model_alias, context,"
            " slots, profile_id, runtime_json, runtime_fingerprint,"
            " runtime_component_identity, verified_at) VALUES"
            " (1, 'tiny', 8192, 1, 'p', '{}', 'fp-known-good',"
            " 'llamacpp:sha256:abc', '2026-01-15T00:00:00Z')"
        )
    # Handoff file carries a DIFFERENT fingerprint.
    (world.paths.app_dir / "runtime-handoff.json").write_text(
        json.dumps({"runtime_fingerprint": "fp-something-else"}))
    report = world.run()
    findings = world.by_id(report, HANDOFF_MISMATCH)
    assert findings and findings[0].severity == SEVERITY_FAIL
    assert findings[0].recommended_command


def test_doctor_catches_bad_model_digest(world):
    world.paths.models_dir.mkdir(parents=True, exist_ok=True)
    model_file = world.paths.models_dir / "tiny.gguf"
    model_file.write_bytes(b"original-bytes")
    good_digest = hashlib.sha256(b"original-bytes").hexdigest()
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO model_artifacts (id, content_digest, byte_size,"
            " canonical_path, storage_state, trust_state, format,"
            " created_at, validated_at) VALUES"
            " ('art-1', ?, 14, ?, 'MANAGED', 'VERIFIED', 'gguf',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (good_digest, str(model_file)),
        )
        conn.execute(
            "INSERT INTO model_installations (alias, path, quant,"
            " display_name, sampling_json, provenance, validation_status,"
            " imported_at, artifact_id) VALUES ('tiny', ?, 'Q4_K_M',"
            " 'Tiny', '{}', 'test', 'validated',"
            " '2026-01-01T00:00:00Z', 'art-1')",
            (str(model_file),),
        )
    # Corrupt the file AFTER the digest was recorded.
    model_file.write_bytes(b"corrupted-bytes")
    report = world.run()
    findings = world.by_id(report, MODEL_DIGEST)
    assert findings and findings[0].severity == SEVERITY_FAIL
    assert "tiny" in findings[0].title
    assert findings[0].recommended_command


def test_doctor_catches_thermal_latch(world):
    with world.units.begin() as conn:
        ThermalStateRepository(conn).set("stopped", {"gpu": 95})
    report = world.run()
    findings = world.by_id(report, THERMAL_LATCH)
    assert findings and findings[0].severity == SEVERITY_FAIL
    assert "thermals reset" in findings[0].recommended_command
    assert report.overall == SEVERITY_FAIL


def test_doctor_catches_low_disk(world, monkeypatch):
    class _Usage:
        total = 100
        free = 2  # far below the 1 GiB floor

    monkeypatch.setattr(doctor_mod.shutil, "disk_usage", lambda _p: _Usage())
    report = world.run()
    findings = world.by_id(report, LOW_DISK)
    assert findings and findings[0].severity == SEVERITY_WARN
    assert findings[0].recommended_command


def test_doctor_catches_insecure_topology(world):
    world.set_settings(funnel_enabled=True)
    report = world.run()
    findings = world.by_id(report, INSECURE_TOPOLOGY)
    assert findings and findings[0].severity == SEVERITY_FAIL
    assert "funnel" in findings[0].evidence.lower()
    assert findings[0].recommended_command


def test_doctor_catches_stale_backup(world):
    # No backup recorded at all.
    report = world.run()
    findings = world.by_id(report, STALE_BACKUP)
    assert findings and findings[0].severity == SEVERITY_WARN

    # A backup far older than the freshness bound.
    world.set_settings(backup_last_completed_at="2026-01-01T00:00:00+00:00")
    report = world.run()
    findings = world.by_id(report, STALE_BACKUP)
    assert findings and findings[0].severity == SEVERITY_WARN
    assert "backup run" in findings[0].recommended_command


def test_doctor_flags_loose_secret_file_perms(world):
    secret = world.paths.app_dir / "gateway-credential"
    secret.write_text("s" * 48)
    secret.chmod(0o644)  # too open
    report = world.run()
    findings = world.by_id(report, SECRET_PERMS)
    assert findings and findings[0].severity == SEVERITY_FAIL
    secret.chmod(0o600)
    report = world.run()
    findings = world.by_id(report, SECRET_PERMS)
    assert findings and findings[0].severity == SEVERITY_PASS


def test_doctor_inference_probe_is_injectable(world):
    # No probe supplied -> INFO, never a green claim.
    report = world.run()
    findings = world.by_id(report, INFERENCE)
    assert findings and findings[0].severity == "INFO"

    ok_service = DoctorService(world.units, world.paths,
                               clock=lambda: FIXED_NOW,
                               inference_probe=lambda: {"ok": True})
    findings = [f for f in ok_service.run().findings if f.id == INFERENCE]
    assert findings and findings[0].severity == SEVERITY_PASS

    bad_service = DoctorService(world.units, world.paths,
                                clock=lambda: FIXED_NOW,
                                inference_probe=lambda: {"ok": False})
    findings = [f for f in bad_service.run().findings if f.id == INFERENCE]
    assert findings and findings[0].severity == SEVERITY_FAIL


def test_doctor_report_contract_and_worst_ids(world):
    with world.units.begin() as conn:
        ThermalStateRepository(conn).set("stopped", None)
    report = world.run()
    payload = report.to_dict()
    assert payload["overall"] == SEVERITY_FAIL
    assert THERMAL_LATCH in payload["worst_ids"]
    assert payload["schema_version"] == DOCTOR_SCHEMA_VERSION
    for f in payload["findings"]:
        assert {"id", "severity", "title", "evidence",
                "recommended_command"} <= set(f)


def test_doctor_cli_emits_structured_findings(tmp_path, monkeypatch, capsys):
    """Exit gate: the CLI doctor surface carries the stable findings."""
    from types import SimpleNamespace

    from bc250_llm_mode import __main__ as cli

    monkeypatch.setattr(cli, "configure_logging", lambda *_args: None)
    runner = SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""),
        emit=lambda *_line: None,
    )
    monkeypatch.setattr(cli, "CommandRunner", lambda *_args, **_kwargs: runner)
    monkeypatch.setattr(cli, "detect_hardware", lambda *_a, **_k: SimpleNamespace(
        to_dict=lambda: {"valid": True}, valid=True))
    monkeypatch.setattr(cli, "analyze_memory_profile",
                        lambda *_a: SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr(cli, "service_status",
                        lambda *_a: {"active": False, "enabled": False})
    monkeypatch.setattr(cli, "open_webui_status", lambda *_a: {"running": False})
    monkeypatch.setattr(cli, "tailscale_status", lambda *_a: {"running": False})
    monkeypatch.setattr(cli, "https_sharing_status",
                        lambda *_a: {"enabled": False})
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert cli.main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "findings" in report and "overall" in report and "doctor" in report
    assert report["doctor"]["schema_version"] == DOCTOR_SCHEMA_VERSION
    ids = {f["id"] for f in report["findings"]}
    assert DB_INTEGRITY in ids
    for f in report["findings"]:
        assert {"id", "severity", "title", "evidence",
                "recommended_command"} <= set(f)
