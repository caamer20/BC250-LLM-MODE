"""R2 hardening P1-7: the runtime handoff is rendered by a dedicated
service, only after committed runtime/model/profile changes, with its
publication failure reported separately from the database commit.

Includes the behavioral launcher contract: the launcher consumes the
rendered handoff artifact, never a whole-state dictionary.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from _native import NativeApp
from bc250_llm_mode.runtime_handoff import (
    HANDOFF_FILENAME,
    HandoffPublicationError,
    RuntimeHandoffRenderer,
    RuntimeIdentityV2,
    build_payload,
    regenerate_for_app_state,
)
from bc250_llm_mode.server import generate_launcher
from bc250_llm_mode.services import RuntimeConfigurationService
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

MODEL = {
    "id": "lfm25-26b",
    "path": "/models/lfm.gguf",
    "quant": "Q5_K_M",
    "display_name": "LFM 2.5\n2.6B",  # newline must be sanitized
}


def os_stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def _seed(store: NativeApp):
    from bc250_llm_mode.optimize import normalized_settings, validate_settings
    from bc250_llm_mode.repositories import ModelInstallationsRepository

    with store.units.begin() as conn:
        ModelInstallationsRepository(conn).replace_all([dict(MODEL)])
    store.set_settings({
        "current_model": MODEL["id"],
        "optimizations": validate_settings(normalized_settings({"parallel_slots": 1})),
    })
    return RuntimeConfigurationService(
        UnitOfWorkFactory(store.paths.database_path),
        app_dir=store.paths.app_dir,
        state_supplier=store.load,
    )


def test_payload_carries_config_revision_and_model_identity(tmp_path):
    store = NativeApp(tmp_path)
    runtime = _seed(store)

    result = runtime.apply({"context": 16384}, expected_revision=store.revision())

    payload = json.loads(
        (store.paths.app_dir / HANDOFF_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["config_revision"] == result.revision
    assert payload["model_id"] == MODEL["id"]
    assert payload["model_path"] == MODEL["path"]
    assert os_stat_mode(store.paths.app_dir / HANDOFF_FILENAME) == 0o600


def test_settings_only_commits_do_not_republish(tmp_path):
    store = NativeApp(tmp_path)
    runtime = _seed(store)
    runtime.apply({"context": 16384}, expected_revision=store.revision())
    handoff = store.paths.app_dir / HANDOFF_FILENAME
    assert handoff.exists()
    before = handoff.read_text(encoding="utf-8")

    # A settings-scoped frontend commit never touches the handoff.
    state = store.load()
    changed = dict(state)
    changed["disclaimer_ack"] = True
    store.application.commit_settings_changes(state, changed)
    assert handoff.read_text(encoding="utf-8") == before

    # A committed runtime-relevant change re-renders.
    runtime.apply({"context": 8192}, expected_revision=store.revision())
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["ctx_total"] == 8192


def test_missing_or_stale_handoff_regenerated_at_start(tmp_path):
    store = NativeApp(tmp_path)
    runtime = _seed(store)
    revision = store.revision()
    runtime.apply({"context": 16384}, expected_revision=revision)
    store.paths.app_dir.joinpath(HANDOFF_FILENAME).unlink()

    state = store.load()
    assert regenerate_for_app_state(state) is True
    payload = json.loads(
        (store.paths.app_dir / HANDOFF_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["config_revision"] == store.revision()

    # Idempotent: an up-to-date artifact is left alone.
    assert regenerate_for_app_state(state) is False


def test_publication_failure_reported_separately_from_commit(tmp_path, monkeypatch):
    store = NativeApp(tmp_path)
    runtime = _seed(store)
    rev = store.revision()

    def broken_publish(self, _state, *, config_revision):
        raise HandoffPublicationError("app_dir unwritable")

    monkeypatch.setattr(RuntimeHandoffRenderer, "publish", broken_publish)

    # The commit succeeds; only the publication reports failure.
    result = runtime.apply({"context": 4096}, expected_revision=rev)
    assert result.status == "committed_handoff_regeneration_required"
    assert runtime.current()["context"] == 4096
    assert "app_dir unwritable" in str(result.handoff_error)


def test_renderer_rejects_unwritable_target(tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where app_dir should be", encoding="utf-8")
    renderer = RuntimeHandoffRenderer(blocked)
    with pytest.raises(HandoffPublicationError):
        renderer.publish({"revision": 1})


def test_handoff_rendered_and_consumed_by_launcher(tmp_path):
    """Behavioral: the launcher consumes the rendered handoff artifact."""
    store = NativeApp(tmp_path)
    runtime = _seed(store)
    store.set_settings({"llama_cpp_path": str(tmp_path / "llama.cpp")})
    runtime.apply({"context": 16384}, expected_revision=store.revision())

    handoff = store.paths.app_dir / HANDOFF_FILENAME
    assert os_stat_mode(handoff) == 0o600
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["ctx_total"] == 16384
    assert payload["alias"] == "LFM 2.5 2.6B"  # newline sanitized

    bin_dir = tmp_path / "llama.cpp" / "build" / "bin"
    bin_dir.mkdir(parents=True)
    record = tmp_path / "argv.txt"
    stub = bin_dir / "llama-server"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$@" >> "$BC250_RECORD"\n'
        'printf "FORCE_SYNC=%s\\n" "${GGML_VK_FORCE_SYNC-UNSET}" >> "$BC250_RECORD"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    launcher = generate_launcher(store.load())
    result = subprocess.run(
        ["bash", str(launcher)],
        env={**os.environ, "BC250_RECORD": str(record)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    recorded = record.read_text(encoding="utf-8")
    assert "--alias" in recorded
    assert "LFM 2.5 2.6B" in recorded
    assert "FORCE_SYNC=" in recorded


def test_launcher_fails_closed_on_missing_handoff(tmp_path):
    store = NativeApp(tmp_path)
    _seed(store)
    # No handoff published. The launcher must refuse, not fall back.
    launcher = generate_launcher(store.load())
    result = subprocess.run(
        ["bash", str(launcher)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 78
    assert "handoff missing" in result.stderr


def test_launcher_fails_closed_on_invalid_handoff(tmp_path):
    store = NativeApp(tmp_path)
    _seed(store)
    runtime = RuntimeConfigurationService(
        UnitOfWorkFactory(store.paths.database_path),
        app_dir=store.paths.app_dir,
        state_supplier=store.load,
    )
    runtime.apply({"context": 16384}, expected_revision=store.revision())
    handoff = store.paths.app_dir / HANDOFF_FILENAME

    payload = json.loads(handoff.read_text(encoding="utf-8"))
    payload["port"] = 1  # out of the documented range
    handoff.write_text(json.dumps(payload), encoding="utf-8")

    launcher = generate_launcher(store.load())
    result = subprocess.run(
        ["bash", str(launcher)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "handoff invalid" in (result.stderr + result.stdout)


# --- U1.2 §14: handoff schema v2 binds the runtime component identity --------


def _v2_base_state(**overrides):
    state = {
        "current_model": "demo",
        "current_ctx": 8192,
        "server_port": 8080,
        "llama_cpp_path": "/root/llama.cpp",
        "installed_models": [
            {"id": "demo", "display_name": "Demo", "path": "/m/d.gguf"}
        ],
        "optimizations": {"parallel_slots": 4},
        "revision": 7,
    }
    state.update(overrides)
    return state


def _v2_identity(component="llamacpp:sha256:" + "a" * 64,
                 server="b" * 64, manifest="c" * 64):
    from bc250_llm_mode.runtime_handoff import RuntimeIdentityV2

    return RuntimeIdentityV2(
        component_id=component,
        source_commit="d" * 40,
        server_sha256=server,
        manifest_digest=manifest,
        operation_id="op-1",
    )


def test_v2_payload_binds_component_identity_and_fingerprint_changes():
    from bc250_llm_mode.runtime_handoff import runtime_fingerprint

    without = build_payload(_v2_base_state(), config_revision=7)
    with_identity = build_payload(
        _v2_base_state(), config_revision=7,
        runtime_identity=_v2_identity(),
    )
    assert without["schema_version"] == 1
    assert with_identity["schema_version"] == 2
    assert with_identity["runtime_component_id"] == _v2_identity().component_id
    # Same path + changed component identity -> different fingerprint
    # (F6B.1.4): swapping content at one path regenerates the artifact.
    other = runtime_fingerprint({
        **_v2_base_state(),
        "runtime_component_id": "llamacpp:sha256:" + "f" * 64,
    })
    same = runtime_fingerprint({
        **_v2_base_state(),
        "runtime_component_id": _v2_identity().component_id,
    })
    assert other != same


def test_renderer_v2_observation_rules(tmp_path):
    renderer = RuntimeHandoffRenderer(tmp_path)
    renderer.publish(
        _v2_base_state(), config_revision=7,
        runtime_identity=_v2_identity(),
    )
    payload = renderer.observe(require_v2=True)
    assert payload is not None
    assert payload["runtime_operation_id"] == "op-1"
    # A legacy v1 artifact is rejected for MANAGED starts but stays a
    # valid legacy observation.
    legacy = json.loads((tmp_path / HANDOFF_FILENAME).read_text())
    legacy["schema_version"] = 1
    for key in ("runtime_component_id", "runtime_source_commit",
                "runtime_server_sha256", "runtime_manifest_digest",
                "runtime_operation_id"):
        legacy.pop(key, None)
    (tmp_path / HANDOFF_FILENAME).write_text(json.dumps(legacy))
    assert renderer.observe(require_v2=True) is None
    assert renderer.observe() is not None


def test_v2_observation_rejects_malformed_digests_or_missing_fields(tmp_path):
    renderer = RuntimeHandoffRenderer(tmp_path)
    good = build_payload(
        _v2_base_state(), config_revision=7,
        runtime_identity=_v2_identity(),
    )
    (tmp_path / HANDOFF_FILENAME).write_text(json.dumps(good))
    assert renderer.observe(require_v2=True) is not None

    bad = dict(good)
    bad["runtime_server_sha256"] = "nothex"
    (tmp_path / HANDOFF_FILENAME).write_text(json.dumps(bad))
    assert renderer.observe(require_v2=True) is None

    missing = dict(good)
    missing.pop("runtime_operation_id")
    (tmp_path / HANDOFF_FILENAME).write_text(json.dumps(missing))
    assert renderer.observe(require_v2=True) is None


# --- U1.2 §14.2: behavioral start-receipt contract ----------------------------


def _materialize_active_tree(root: Path):
    bin_dir = root / "build" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    binary = bin_dir / "llama-server"
    body = "#!/usr/bin/env python3\nprint('STUB-SERVER-RAN')\n"
    binary.write_text(body, encoding="utf-8")
    binary.chmod(0o755)
    server_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest = {
        "build_id": "llamacpp:sha256:" + "a" * 64,
        "manifest_digest": "c" * 64,
        "manifest": {
            "binaries": [
                {"path": "build/bin/llama-server", "sha256": server_sha},
            ],
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


@pytest.fixture()
def receipt_world(tmp_path):
    app_dir = tmp_path / "app"
    active_root = tmp_path / "runtime" / "llama.cpp"
    manifest = _materialize_active_tree(active_root)
    identity = RuntimeIdentityV2(
        component_id=manifest["build_id"],
        source_commit="d" * 40,
        server_sha256=manifest["manifest"]["binaries"][0]["sha256"],
        manifest_digest=manifest["manifest_digest"],
        operation_id="op-launch",
    )
    handoff = app_dir / HANDOFF_FILENAME
    app_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(
        _v2_base_state(llama_cpp_path=str(active_root)),
        config_revision=7,
        runtime_identity=identity,
    )
    handoff.write_text(json.dumps(payload, indent=2))
    launcher = generate_launcher({
        "app_dir": str(app_dir),
        "current_model": "demo",
        "logs_dir": str(tmp_path / "logs"),
    })
    return {"launcher": Path(launcher), "handoff": handoff,
            "app_dir": app_dir, "active_root": active_root}


def _bash_launcher(launcher: Path, handoff: Path):
    env = dict(os.environ)
    env["BC250_HANDOFF_PATH"] = str(handoff)
    return subprocess.run(["bash", str(launcher)], capture_output=True,
                          text=True, env=env, timeout=30)


def test_launcher_writes_0600_start_receipt_before_exec(receipt_world):
    result = _bash_launcher(receipt_world["launcher"], receipt_world["handoff"])
    assert result.returncode == 0, result.stderr
    assert "STUB-SERVER-RAN" in result.stdout
    receipt_path = receipt_world["app_dir"] / "start-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["operation_id"] == "op-launch"
    assert receipt.get("nonce")
    assert os_stat_mode(receipt_path) == 0o600


def test_launcher_refuses_swapped_binary_without_receipt(receipt_world):
    binary = receipt_world["active_root"] / "build" / "bin" / "llama-server"
    binary.write_text("#!/usr/bin/env python3\nprint('TAMPERED')\n")
    result = _bash_launcher(receipt_world["launcher"], receipt_world["handoff"])
    assert result.returncode == 78
    assert "digest mismatch" in result.stderr
    assert not (receipt_world["app_dir"] / "start-receipt.json").exists()


def test_launcher_refuses_stale_manifest_build_id(receipt_world):
    manifest_path = receipt_world["active_root"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["build_id"] = "llamacpp:sha256:" + "e" * 64
    manifest_path.write_text(json.dumps(manifest))
    result = _bash_launcher(receipt_world["launcher"], receipt_world["handoff"])
    assert result.returncode == 78
    assert "build id" in result.stderr
