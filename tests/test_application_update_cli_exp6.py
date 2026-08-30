from __future__ import annotations

import json


def test_update_cli_is_honestly_unavailable_and_cleanup_is_dry_run(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from bc250_llm_mode.__main__ import main

    assert main(["update", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["reason_code"] == "SIGNED_UPDATE_CHANNEL_UNAVAILABLE"
    assert status["installed"]["provenance"] == "UNVERIFIED_LEGACY_INSTALL"

    assert main(["update", "check"]) == 1
    checked = json.loads(capsys.readouterr().out)
    assert checked["release"] is None
    assert checked["reason_code"] == "SIGNED_UPDATE_CHANNEL_UNAVAILABLE"

    assert main(["update", "cleanup", "--dry-run"]) == 0
    cleanup = json.loads(capsys.readouterr().out)
    assert cleanup["dry_run"] is True
    assert cleanup["deleted_anything"] is False


def test_update_cli_never_treats_an_arbitrary_local_archive_as_trusted(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    archive = tmp_path / "unsigned.tar"
    archive.write_bytes(b"not a signed release")
    from bc250_llm_mode.__main__ import main

    assert main(["update", "import-bundle", str(archive)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["reason_code"] == "SIGNED_UPDATE_CHANNEL_UNAVAILABLE"
    assert result["release"] is None
