"""R1/R2 exit (plan §6.2): --state is an import-only deprecated alias and
'import-state PATH' is the one-time publication path for legacy JSON.

Required matrix:
- --state rejected for normal commands with no files changed;
- valid import publishes one database and leaves the source byte-identical;
- corrupt import leaves the target absent and the source untouched;
- a second identical import does not overwrite the published database;
- help text describes import-only behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bc250_llm_mode.__main__ import _parser, main


def _isolated_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_help_describes_import_only_behavior(capsys):
    with pytest.raises(SystemExit):
        _parser().parse_args(["--help"])
    text = capsys.readouterr().out
    assert "import-state" in text
    assert "Deprecated" in text or "deprecated" in text


def test_state_flag_rejected_for_setup_and_status(tmp_path, monkeypatch, capsys):
    home = _isolated_home(tmp_path, monkeypatch)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"disclaimer_ack": True}), encoding="utf-8")

    assert main(["--state", str(source), "setup"]) == 2
    assert main(["--state", str(source), "status"]) == 2
    # Rejection precedes composition: no database anywhere.
    assert not list(home.rglob("state.db"))
    # The source was never touched.
    assert json.loads(source.read_text(encoding="utf-8")) == {"disclaimer_ack": True}


def test_import_state_publishes_once_and_keeps_source_identical(
    tmp_path, monkeypatch, capsys
):
    home = _isolated_home(tmp_path, monkeypatch)
    source = tmp_path / "source.json"
    original = {"disclaimer_ack": True, "current_model": "lfm25-26b"}
    payload = json.dumps(original, indent=2)
    source.write_text(payload, encoding="utf-8")

    assert main(["import-state", str(source)]) == 0
    database = home / ".bc250-llm-mode" / "state.db"
    assert database.exists(), "a valid import publishes exactly one database"
    assert source.read_text(encoding="utf-8") == payload

    # Second identical import is idempotent: never overwrites the database.
    before = database.read_bytes()
    assert main(["import-state", str(source)]) == 0
    assert database.read_bytes() == before


def test_corrupt_import_publishes_nothing_and_keeps_source(tmp_path, monkeypatch, capsys):
    home = _isolated_home(tmp_path, monkeypatch)
    source = tmp_path / "broken.json"
    source.write_text("{definitely not json", encoding="utf-8")

    assert main(["import-state", str(source)]) == 78
    report = json.loads(capsys.readouterr().out)
    assert report["imported"] is False
    assert report["reason"]
    assert not list(home.rglob("state.db"))
    assert source.read_text(encoding="utf-8") == "{definitely not json"
