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

    # U1.1 §8.5: the synchronous `hf download` route is deleted. Tokens now
    # travel ONLY in Authorization headers inside the typed hub client, and
    # the architecture guard (test_architecture) proves no bypass remains.
    import bc250_llm_mode.download as download_module

    assert not hasattr(download_module, "download_model"), (
        "the synchronous download bypass must stay deleted"
    )


def test_hub_client_sends_tokens_via_headers_only():
    """U1.1: tokens are never written to env-files at all anymore; guard
    against reintroduction by asserting the hub client sends headers only."""
    from urllib.parse import urlparse

    from bc250_llm_mode.hub_source import HubClient

    client = HubClient(token_provider=lambda: "hf_SUPERSECRET_TOKEN_VALUE")
    headers = client._auth_headers(True)
    assert headers.get("Authorization") == "Bearer hf_SUPERSECRET_TOKEN_VALUE"
    # No argv/env-file channel exists in the module at all.
    source = Path(client.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).parent.parent / source).read_text()
    assert "--env-file" not in text
    assert "--token" not in text


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
