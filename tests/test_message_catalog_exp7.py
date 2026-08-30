"""EXP-7 stable-copy and internet-independent glossary contracts."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bc250_llm_mode.message_catalog import (
    GLOSSARY,
    MAX_GLOSSARY_RESULTS,
    MESSAGE_CATALOG,
    MESSAGE_CATEGORIES,
    REQUIRED_MESSAGE_CATEGORIES,
    glossary_entries,
    message_for,
    safe_exception_message,
)


PACKAGE = Path(__file__).parent.parent / "bc250_llm_mode"


def test_catalog_covers_every_consistency_critical_category():
    assert REQUIRED_MESSAGE_CATEGORIES <= MESSAGE_CATEGORIES
    assert len(MESSAGE_CATALOG) == len(set(MESSAGE_CATALOG))
    assert all(item.code == code for code, item in MESSAGE_CATALOG.items())
    assert all(len(item.title) <= 96 and len(item.body) <= 768 for item in MESSAGE_CATALOG.values())


def test_unknown_message_code_is_safe_and_never_renders_untrusted_text():
    fallback = message_for("NOT_A_REAL_CODE")
    assert fallback.code == "NOT_A_REAL_CODE"
    assert "NOT_A_REAL_CODE" in fallback.body
    secret = "hf_FAKE-SHOULD-NEVER-RENDER"
    hostile = message_for(secret)
    assert hostile.code == "UNKNOWN_CODE"
    assert secret not in hostile.title + hostile.body


def test_exception_copy_does_not_expose_exception_class_or_message():
    canary = "Bearer secret-privacy-canary"
    mapped = safe_exception_message(RuntimeError(canary))
    assert canary not in mapped.title + mapped.body
    assert "RuntimeError" not in mapped.title + mapped.body


def test_glossary_contains_every_required_appliance_term_and_distinction():
    required = {
        "model", "quantization", "gguf", "context", "kv-cache", "slots",
        "vram", "gtt", "ram", "uma", "cu", "vulkan", "open-webui",
        "base-url", "gateway", "tailscale-serve", "funnel", "installed",
        "verified", "active", "known-good", "recovery",
    }
    assert required <= set(GLOSSARY)
    assert "does not start" in GLOSSARY["model"].definition
    assert "does not mean verified or running" in GLOSSARY["installed"].definition
    assert "keeps Funnel off" in GLOSSARY["funnel"].definition


def test_glossary_matching_is_bounded_deterministic_and_token_based():
    assert glossary_entries() == glossary_entries()
    assert glossary_entries("GPU memory")
    assert len(glossary_entries(limit=MAX_GLOSSARY_RESULTS)) <= MAX_GLOSSARY_RESULTS
    assert glossary_entries("no-such-term") == ()
    with pytest.raises(ValueError, match="1..64"):
        glossary_entries(limit=65)


def test_catalog_and_help_have_no_network_or_infrastructure_imports():
    for relative in ("message_catalog.py", "gui/help_page.py"):
        tree = ast.parse((PACKAGE / relative).read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and not (node.level or 0)
        )
        assert not ({"httpx", "urllib", "requests", "socket", "subprocess"} & imports)


def test_gui_validation_boundaries_use_stable_catalog_not_raw_exception_text():
    settings = (PACKAGE / "gui/settings_page.py").read_text(encoding="utf-8")
    chat = (PACKAGE / "gui/chat_page.py").read_text(encoding="utf-8")
    assert "safe_exception_message" in settings
    assert "safe_exception_message" in chat
    assert 'str(exc)' not in settings
    assert 'str(exc)' not in chat
