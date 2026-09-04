"""Production-path reproductions for the September 4 review defects."""
import io
import json
import sqlite3
import tarfile
from types import SimpleNamespace

import pytest

from _native import NativeApp
from bc250_llm_mode import server, thermals
from bc250_llm_mode.backup_archive import inspect_archive
from bc250_llm_mode.chat_lifecycle import ChatDeadline, ChatResultClassification
from bc250_llm_mode.chat_service import ChatSessionService
from bc250_llm_mode.conversation_service import ConversationService
from bc250_llm_mode.gateway import RateLimiter, MAX_CONCURRENT
from bc250_llm_mode.operations.validation import OperationValidationError
from bc250_llm_mode.services import ThermalLatchProtected
from test_backup_command import _compose, _fake_exchange
from test_native_chat import SequenceHttp, _messages, _state


def test_stale_start_cannot_bypass_new_thermal_latch(tmp_path):
    world = NativeApp(tmp_path)
    stale = world.load()
    world.application.safety.mark_stopped()
    with pytest.raises(ThermalLatchProtected):
        server.require_safe_start(stale)


def test_failed_stop_is_retried_until_inactivity_is_observed(tmp_path, monkeypatch):
    world = NativeApp(tmp_path)
    world.application.safety.mark_stopped()
    active = [True]
    attempts = []
    monkeypatch.setattr(server, "service_status", lambda *_: {"active": active[0], "active_state": "active" if active[0] else "inactive"})
    def stop(*_):
        attempts.append(1)
        active[0] = len(attempts) < 2
        return {"active": active[0]}
    monkeypatch.setattr(server, "stop_service", stop)
    runner = SimpleNamespace(emit=lambda *_: None)
    first = thermals.run_watchdog_once(world, world.load(), runner)
    second = thermals.run_watchdog_once(world, world.load(), runner)
    third = thermals.run_watchdog_once(world, world.load(), runner)
    assert first["stop_outcome"] == "pending"
    assert second["inactive_confirmed"] and third["inactive_confirmed"]
    assert len(attempts) == 2
    assert world.application.safety.current()["latch_state"] == "stopped"


@pytest.mark.parametrize("flag", ["include_models", "include_runtime"])
def test_unsupported_backup_inclusion_has_no_effect(tmp_path, flag):
    paths, app = _compose(tmp_path)
    result = app.backup.create_backup("never.tar", **{flag: True})
    assert result.status == "INCLUSION_UNAVAILABLE"
    assert not (paths.backups_dir / "never.tar").exists()
    assert app.backup.list_backups() == []


def test_tampered_database_fails_verify_preview_and_restore(tmp_path):
    paths, app = _compose(tmp_path)
    assert app.backup.create_backup("test.tar").ok
    backup = app.backup.list_backups()[0]
    path = paths.backups_dir / "test.tar"
    with tarfile.open(path) as archive:
        manifest = archive.extractfile("backup-manifest.json").read()
        database = archive.extractfile("state.db").read()
    changed = tmp_path / "changed.db"
    changed.write_bytes(database)
    with sqlite3.connect(changed) as conn:
        conn.execute("CREATE TABLE tampered (id INTEGER)")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in (("backup-manifest.json", manifest), ("state.db", changed.read_bytes())):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    assert not app.backup.verify_backup(backup["backup_id"])["valid"]
    assert not app.backup.restore_inspect(backup["backup_id"])["restorable"]
    assert app.backup.restore_start(backup["backup_id"], backup["manifest_digest"]).status == "BLOCKED"


@pytest.mark.parametrize("name,kind", [("../escape", tarfile.REGTYPE), ("state.db", tarfile.SYMTYPE), ("extra", tarfile.REGTYPE)])
def test_archive_refuses_noncanonical_members(tmp_path, name, kind):
    path = tmp_path / "bad.tar"
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "/tmp/target" if kind == tarfile.SYMTYPE else ""
        archive.addfile(member)
    with pytest.raises(OperationValidationError):
        inspect_archive(path)


def test_restore_preserves_local_assets_and_monotonic_safety(tmp_path):
    paths, app = _compose(tmp_path)
    assert app.backup.create_backup("test.tar").ok
    backup = app.backup.list_backups()[0]
    for relative in ("app-venv/marker", "conversations/retained.txt", "connection-secrets/retained.txt", "models/retained.gguf"):
        target = paths.app_dir / relative
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text("retained local data")
    app.safety.mark_stopped()
    app.backup._adapter._exchange = _fake_exchange
    result = app.backup.restore_start(backup["backup_id"], backup["manifest_digest"])
    assert result.status == "RESTORED"
    for relative in ("app-venv/marker", "conversations/retained.txt", "connection-secrets/retained.txt", "models/retained.gguf"):
        assert (paths.app_dir / relative).read_text() == "retained local data"
    assert app.safety.current()["latch_state"] == "stopped"
    assert app.backup.verify_backup(backup["backup_id"])["valid"]


def test_rate_window_does_not_replace_inflight_reservations():
    now = [0.0]
    limiter = RateLimiter(now=lambda: now[0])
    for _ in range(MAX_CONCURRENT):
        assert limiter.check("client")[0]
    now[0] = 61
    assert not limiter.check("client")[0]
    limiter.release("client")
    assert limiter.check("client")[0]
    assert not limiter.check("client")[0]


@pytest.mark.parametrize("lines", [[], ['data: {"choices":[{"delta":{"content":"partial"}}]}']])
def test_chat_requires_explicit_sse_completion(lines):
    result = ChatSessionService(http_client=SequenceHttp([lines])).stream(_state(), _messages(), lambda _: None)
    assert result.classification is ChatResultClassification.MALFORMED_RESPONSE


def test_chat_accepts_only_the_selected_models_public_alias():
    state = {**_state(), "current_model": "internal", "installed_models": [{"id": "internal", "display_name": "Public model"}]}
    lines = ['data: {"model":"Public model","choices":[{"delta":{"content":"ok"}}]}', 'data: [DONE]']
    result = ChatSessionService(http_client=SequenceHttp([lines])).stream(state, _messages(), lambda _: None)
    assert result.ok and result.text == "ok"


def test_prompt_preflight_returns_closed_result():
    result = ChatSessionService(http_client=SequenceHttp([])).stream(_state(), _messages("a" * 25000), lambda _: None)
    assert result.error_code == "REQUEST_INVALID" and not result.ok


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 100000])
def test_chat_deadline_is_finite_and_bounded(value):
    with pytest.raises(ValueError):
        ChatDeadline(total_s=value)


def test_conversation_stale_save_cannot_erase_another_clients_work(tmp_path):
    first, second = ConversationService(tmp_path / "conversations"), ConversationService(tmp_path / "conversations")
    record = first.create()
    second.load(record.conversation_id)
    first.save(record.conversation_id, title="Updated", messages=[{"role": "user", "content": "keep"}])
    with pytest.raises(ValueError, match="changed in another client"):
        second.save(record.conversation_id, title="Stale", messages=[])
    assert first.load(record.conversation_id).messages[0]["content"] == "keep"


def test_legacy_over_limit_conversation_remains_searchable(tmp_path):
    service = ConversationService(tmp_path / "conversations")
    original = service.save("first", title="First", messages=[])
    # Simulate a pre-existing over-limit history without evading the new create cap.
    for index in range(201):
        identifier = f"c{index:03d}"
        payload = {**original.to_dict(), "conversation_id": identifier, "title": f"Chat {index}"}
        (tmp_path / "conversations" / f"{identifier}.json").write_text(json.dumps(payload))
    assert service.list(query="Chat 200")[0]["conversation_id"] == "c200"
    with pytest.raises(ValueError, match="full"):
        service.create()
