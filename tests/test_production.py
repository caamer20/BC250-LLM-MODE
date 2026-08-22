"""Production-hardening guarantees: secrets, logging, CLI boundary, packaging."""

import logging
import logging.handlers
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bc250_llm_mode import __version__, download
from bc250_llm_mode.logging_utils import configure_logging


class RecordingRunner:
    def __init__(self):
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        self.commands.append([str(c) for c in command])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def emit(self, message):
        # setup.log records every argv; the token must never survive here.
        self.messages.append(message)


def test_hf_token_never_reaches_argv_or_logs(tmp_path, monkeypatch):
    from bc250_llm_mode.catalog import model_by_id

    secret = "hf_SUPERSECRET_TOKEN_VALUE"
    monkeypatch.setenv("HF_TOKEN", secret)
    state = {
        "models_dir": str(tmp_path / "models"),
        "venv_path": "/root/.venvs/hf",
        "container_name": "llm",
        "app_dir": str(tmp_path),
        "setup_phase": 0,
    }
    runner = RecordingRunner()
    model = model_by_id("lfm25-26b")
    with pytest.raises(Exception):
        # No artifact exists in tmp; the command capture is what matters.
        download.download_model(state, model, "Q5_K_M", runner)
    flat = [item for command in runner.commands for item in command]
    assert not any("--token" == item for item in flat), "--token must never be used"
    for command in runner.commands:
        joined = " ".join(command)
        assert secret not in joined, "token leaked into podman argv"
    for message in runner.messages:
        assert secret not in message, "token leaked into setup.log"
    env_uses = [c for c in runner.commands if "--env-file" in c]
    assert env_uses, "token must be delivered via --env-file"


def test_token_env_file_is_cleaned_up(tmp_path, monkeypatch):
    import os

    from bc250_llm_mode.catalog import model_by_id

    monkeypatch.setenv("HF_TOKEN", "hf_ephemeral")
    leftovers = []
    real_unlink = os.unlink

    def tracking_unlink(path, *a, **k):
        if "bc250-hf-" in str(path):
            leftovers.append(str(path))
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(download.os, "unlink", tracking_unlink)
    state = {
        "models_dir": str(tmp_path / "models"),
        "venv_path": "/root/.venvs/hf",
        "container_name": "llm",
        "app_dir": str(tmp_path),
        "setup_phase": 0,
    }
    with pytest.raises(Exception):
        download.download_model(state, model_by_id("lfm25-26b"), "Q5_K_M", RecordingRunner())
    assert leftovers, "the private env-file must be removed after the run"


def test_setup_log_is_rotating():
    logger = configure_logging("/tmp/bc250-audit-logs")
    handlers = [
        h for h in logger.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert handlers, "setup.log must use RotatingFileHandler"
    assert all(h.maxBytes <= 5 * 1024 * 1024 for h in handlers)


def test_version_is_consistent_across_packaging():
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text


def test_cli_reports_version(capsys):
    from bc250_llm_mode.__main__ import cli

    with pytest.raises(SystemExit) as excinfo:
        cli(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_cli_interrupt_exits_130(monkeypatch):
    from bc250_llm_mode.__main__ import cli as main_fn
    from bc250_llm_mode import __main__ as module

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(module, "main", interrupted)
    assert main_fn([]) == 130
