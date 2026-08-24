"""R2 hardening P1-7: the runtime handoff is rendered by a dedicated
service, only after committed runtime/model/profile changes, with its
publication failure reported separately from the database commit.

Includes the behavioral launcher contract: the launcher consumes the
rendered handoff artifact, never a whole-state dictionary.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from _native import NativeApp
from bc250_llm_mode.runtime_handoff import (
    HANDOFF_FILENAME,
    HandoffPublicationError,
    RuntimeHandoffRenderer,
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
