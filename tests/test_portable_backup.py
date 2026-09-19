"""Portable recovery covers content preservation, hostile archives and retry."""
import hashlib
import io
import json
import os
import tarfile

import pytest

from bc250_llm_mode.app import Application
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.chat_preferences import ConversationOptions
from bc250_llm_mode.portable_backup import inspect_portable


def app_at(path):
    return Application.compose(AppPaths.temporary(path))


def populate(app):
    a = app.conversations.save("source", title="Private title", messages=[
        {"role": "user", "content": "private prompt canary"}, {"role": "assistant", "content": "private answer"}],
        draft="unsent draft", options=ConversationOptions("saved instructions", 0.3, 4096))
    app.conversations.branch(a.conversation_id, 0, "branch draft", expected_revision=a.revision)
    app.chat_templates.save_template("Writing", "Personal template")
    app.preferences.apply({"appearance": "dark", "ui_scale_percent": 150, "notifications_enabled": True})


def test_portable_roundtrip_contains_work_but_not_machine_authority(tmp_path):
    source, target = app_at(tmp_path / "source"), app_at(tmp_path / "target")
    populate(source)
    archive = tmp_path / "backup.tar"
    preview = source.portable_backup.export(archive, preference_keys=("appearance",), include_templates=True)
    assert len(preview.conversations) == 2 and preview.preferences == {"appearance": "dark"}
    assert archive.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive) as tar:
        assert not {"state.db", "credentials.json"} & set(tar.getnames())
        assert all(not member.issym() for member in tar)
    before = target.read_model()
    result = target.portable_backup.import_archive(archive, preview.digest,
        preference_keys=("appearance",), include_templates=True, expected_preferences=target.preferences.current())
    assert result.complete and result.imported == 2
    imported = [target.conversations.load(row["conversation_id"]) for row in target.conversations.list()]
    original = next(row for row in imported if row.title == "Private title")
    assert original.messages[0]["content"] == "private prompt canary"
    assert original.draft == "unsent draft" and original.options.instructions == "saved instructions"
    branch = next(row for row in imported if row.parent_conversation_id)
    assert branch.parent_conversation_id == original.conversation_id
    assert target.preferences.current()["appearance"] == "dark"
    assert target.preferences.current()["ui_scale_percent"] == 100
    assert target.preferences.current()["notifications_enabled"] is False
    assert target.read_model().get("current_model") == before.get("current_model")
    assert "Personal template" in target.chat_templates.list().values()


def test_retry_after_interruption_is_idempotent_and_preserves_later_edits(tmp_path, monkeypatch):
    source, target = app_at(tmp_path / "source"), app_at(tmp_path / "target")
    populate(source)
    archive = tmp_path / "backup.tar"
    preview = source.portable_backup.export(archive)
    real_link = os.link
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk interruption")
        return real_link(*args, **kwargs)
    monkeypatch.setattr(os, "link", fail_second)
    partial = target.portable_backup.import_archive(archive, preview.digest)
    assert not partial.complete and partial.imported == 1 and partial.remaining == 1
    row = target.conversations.list()[0]
    target.conversations.rename(row["conversation_id"], "User edited this after interruption")
    monkeypatch.setattr(os, "link", real_link)
    complete = target.portable_backup.import_archive(archive, preview.digest)
    assert complete.complete and complete.imported == 1 and complete.already_present == 1
    assert target.conversations.load(row["conversation_id"]).title == "User edited this after interruption"
    repeated = target.portable_backup.import_archive(archive, preview.digest)
    assert repeated.complete and repeated.imported == 0 and repeated.already_present == 2
    assert len(target.conversations.list()) == 2


def test_modified_archive_refused_before_import_and_source_protected(tmp_path):
    source, target = app_at(tmp_path / "source"), app_at(tmp_path / "target")
    populate(source)
    archive = tmp_path / "backup.tar"
    preview = source.portable_backup.export(archive)
    data = archive.read_bytes().replace(b"private prompt canary", b"tampered text canary!")
    archive.write_bytes(data)
    with pytest.raises(ValueError):
        target.portable_backup.import_archive(archive, preview.digest)
    assert target.conversations.list() == ()
    with pytest.raises(ValueError, match="outside"):
        source.portable_backup.export(source.paths.database_path)


@pytest.mark.parametrize("kind", ["path", "link", "duplicate", "settings", "huge"])
def test_hostile_portable_members_fail_closed(tmp_path, kind):
    archive = tmp_path / "hostile.tar"
    name = "preferences.json" if kind == "settings" else "conversation-0000.json"
    payload = b'{"notifications_enabled":true}' if kind == "settings" else b"{}"
    if kind == "path":
        name = "../escape.json"
    files = [{"name": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}]
    if kind == "duplicate":
        files *= 2
    manifest = json.dumps({"format": "bc250-portable", "version": 1, "created_at": "now", "files": files}).encode()
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as tar:
        first = tarfile.TarInfo("manifest.json")
        first.size = len(manifest)
        tar.addfile(first, io.BytesIO(manifest))
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        if kind == "link":
            member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(member, io.BytesIO(payload))
    if kind == "huge":
        data = bytearray(archive.read_bytes())
        # A bounded manifest cannot nominate a multi-gigabyte member.
        first_member_offset = 512 + ((len(manifest) + 511) // 512) * 512
        data[first_member_offset + 124:first_member_offset + 136] = b"77777777777\0"
        archive.write_bytes(data)
    with pytest.raises(ValueError):
        inspect_portable(archive)
    assert not (tmp_path.parent / "escape.json").exists()


def test_stale_preference_preview_never_overwrites_current_choice(tmp_path):
    app = app_at(tmp_path)
    baseline = app.preferences.current()
    app.preferences.apply({"appearance": "dark", "notifications_enabled": True})
    with pytest.raises(ValueError, match="changed"):
        app.preferences.apply_portable({"appearance": "light"}, expected=baseline)
    assert app.preferences.current()["appearance"] == "dark"
    assert app.preferences.current()["notifications_enabled"] is True
