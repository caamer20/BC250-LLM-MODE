"""Packaging smoke checks: the installed/source artifact is complete."""

from pathlib import Path

import bc250_llm_mode


def test_pyproject_declares_entry_point_and_metadata():
    text = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'bc250-llm-mode = "bc250_llm_mode.__main__:cli"' in text
    assert "requires-python" in text
    assert 'readme = "README.md"' in text
    for dependency in ("gguf", "httpx", "prompt-toolkit", "rich"):
        assert dependency in text, f"missing declared dependency: {dependency}"


def test_public_import_surface_is_stable():
    assert bc250_llm_mode.__version__
    from bc250_llm_mode.__main__ import cli  # noqa: F401
    from bc250_llm_mode.gui import Wizard, run_gui  # noqa: F401
    from bc250_llm_mode.paths import AppPaths  # noqa: F401
    from bc250_llm_mode.state import StateStore  # noqa: F401


def test_documented_docs_exist():
    root = Path(__file__).parent.parent
    for name in ("README.md", "ARCHITECTURE.md", "CHANGELOG.md", "AGENTS.md"):
        assert (root / name).is_file(), name
