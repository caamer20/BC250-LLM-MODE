"""R2 hardening P1-7: the runtime handoff is rendered by a dedicated
service, only after committed runtime/model/profile changes, with its
publication failure reported separately from the database commit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.compat_state import CompatStateStore
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.runtime_handoff import (
    HandoffPublicationError,
    RuntimeHandoffRenderer,
    regenerate_for_app_state,
)


def _store(tmp_path, **kw):
    return CompatStateStore(AppPaths.temporary(tmp_path / "root"), **kw)


def test_payload_carries_config_revision_and_model_identity(tmp_path):
    store = _store(tmp_path)
    state = store.load()
    state.update(current_model="lfm25-26b", current_ctx=16384)
    state["installed_models"] = [{
        "id": "lfm25-26b", "path": "/models/lfm.gguf", "display_name": "LFM",
    }]
    store.save(state)

    payload = json.loads(store.renderer.path.read_text(encoding="utf-8"))
    assert payload["config_revision"] == state["revision"]
    assert payload["model_id"] == "lfm25-26b"
    assert payload["model_path"] == "/models/lfm.gguf"
    assert (os_stat_mode(store.renderer.path)) == 0o600


def os_stat_mode(path: Path) -> int:
    import os

    return os.stat(path).st_mode & 0o777


def test_generic_settings_write_does_not_republish(tmp_path):
    store = _store(tmp_path)
    state = store.load()
    state["optimizations"]["parallel_slots"] = 2  # runtime-relevant: renders once
    store.save(state)
    assert store.renderer.path.exists()

    calls = []
    original = store.renderer.publish

    def counting_publish(s, *, config_revision):
        calls.append(1)
        return original(s, config_revision=config_revision)

    store.renderer.publish = counting_publish  # type: ignore[method-assign]

    # Generic settings write: no re-render.
    generic = store.load()
    generic["disclaimer_ack"] = True
    store.save(generic)
    assert calls == []

    # Runtime-relevant change: re-render.
    relevant = store.load()
    relevant["server_port"] = 9191
    store.save(relevant)
    assert len(calls) == 1
    payload = json.loads(store.renderer.path.read_text(encoding="utf-8"))
    assert payload["port"] == 9191


def test_missing_or_stale_handoff_regenerated_at_start(tmp_path):
    store = _store(tmp_path)
    state = store.load()
    state["server_port"] = 9292
    state["optimizations"] = {**state["optimizations"], "parallel_slots": 2}
    store.save(state)
    store.renderer.path.unlink()

    assert regenerate_for_app_state(state) is True
    payload = json.loads(store.renderer.path.read_text(encoding="utf-8"))
    assert payload["port"] == 9292
    assert payload["config_revision"] == state["revision"]

    # Idempotent: an up-to-date artifact is left alone.
    assert regenerate_for_app_state(state) is False


def test_publication_failure_reported_separately_from_commit(tmp_path):
    store = _store(tmp_path)

    def broken_publish(_state, *, config_revision):
        raise HandoffPublicationError("app_dir unwritable")

    store.renderer.publish = broken_publish  # type: ignore[method-assign]

    state = store.load()
    state["server_port"] = 9393
    state["optimizations"] = {**state["optimizations"], "parallel_slots": 2}
    store.save(state)  # must NOT raise: the commit succeeded

    reloaded = CompatStateStore(AppPaths.temporary(tmp_path / "root")).load()
    assert reloaded["server_port"] == 9393
    assert store.handoff_publication_error is not None
    assert "app_dir unwritable" in store.handoff_publication_error


def test_renderer_rejects_unwritable_target(tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where app_dir should be", encoding="utf-8")
    renderer = RuntimeHandoffRenderer(blocked)
    with pytest.raises(HandoffPublicationError):
        renderer.publish({"revision": 1})