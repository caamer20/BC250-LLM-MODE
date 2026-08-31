from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.catalog import CATALOG  # noqa: E402
from bc250_llm_mode.gui.home_page import build_home_view  # noqa: E402
from bc250_llm_mode.gui.models_page import (  # noqa: E402
    MODEL_PRESENTATION_STATES,
    build_model_items,
    filter_model_items,
    model_action,
)


def _health(state: str, evidence: str = "evidence") -> dict:
    return {"state": state, "effective_state": state, "evidence": evidence}


def _home(
    *, thermal="READY", operations="READY", setup=True, model="READY",
    runtime="READY", inference="READY", installed=1, stale_inference=False,
    active_count=0,
):
    return {
        "cards": {
            "identity": {"setup_complete": setup, "health": _health("READY" if setup else "UNAVAILABLE")},
            "runtime": {"health": _health(runtime)},
            "model": {
                "desired": "demo", "installed_count": installed,
                "health": _health(model),
            },
            "inference": {
                "health": _health(inference), "stale": stale_inference,
            },
            "thermal": {"health": _health(thermal)},
            "operations": {
                "summary": {"active_count": active_count},
                "health": _health(operations),
            },
            "storage": {"available_bytes": 20 * 1024**3, "health": _health("READY")},
            "integrations": {"gateway": {"verified": False}, "health": _health("UNAVAILABLE")},
        }
    }


def test_home_decision_priority_is_safety_then_recovery_then_work():
    assert build_home_view(_home(thermal="BLOCKED", operations="RECOVERY_REQUIRED")).primary.code == "thermal"
    assert build_home_view(_home(operations="RECOVERY_REQUIRED")).primary.code == "recovery"
    assert build_home_view(_home(setup=False)).primary.code == "setup"
    assert build_home_view(_home(operations="BUSY", active_count=1)).primary.code == "activity"
    assert build_home_view(_home()).primary.code == "chat"
    assert build_home_view(_home(inference="UNVERIFIED")).primary.code == "start"
    assert build_home_view(_home(model="UNAVAILABLE", runtime="UNAVAILABLE", inference="UNVERIFIED", installed=0)).primary.code == "models"


def test_home_stale_evidence_never_renders_ready_and_is_compact():
    view = build_home_view(_home(stale_inference=True))
    assert view.primary.code != "chat"
    assert len(view.cards) == 5
    assert len(view.shortcuts) <= 4
    model_card = next(card for card in view.cards if card.key == "model")
    assert model_card.stale is True
    assert model_card.state == "STALE"


def _installed(**changes):
    base = {
        "alias": "qwen-installed",
        "display_name": "Qwen installed",
        "catalog_id": "qwen35-9b",
        "architecture": "qwen35",
        "byte_size": 6 * 1024**3,
        "active": True,
        "known_good": True,
        "trust_state": "VERIFIED",
        "validation_status": "verified",
        "fit_verdict": "FITS",
        "fit_detail": "6.0 GiB weights + KV + overhead",
        "source_repo": "example/repo",
        "quant": "Q5_K_M",
        "deletion_eligible": False,
        "deletion_blockers": ("active-model",),
        "format": "GGUF",
    }
    base.update(changes)
    return SimpleNamespace(**base)


def test_model_library_merges_installed_and_catalog_without_duplicates():
    items = build_model_items(
        [_installed()], context=8192, slots=1, inference_verified=True
    )
    installed = next(item for item in items if item.alias == "qwen-installed")
    assert installed.state == "VERIFIED"
    assert model_action(installed).code == "chat"
    assert sum(item.catalog_id == "qwen35-9b" for item in items) == 1
    remote = next(item for item in items if item.remote)
    action = model_action(remote)
    assert (action.code, action.secondary_code) == ("install-start", "install")
    assert set(item.state for item in items) <= MODEL_PRESENTATION_STATES


def test_model_actions_fail_closed_for_busy_quarantine_and_recovery():
    busy = build_model_items(
        [_installed(active=False)], context=8192, slots=1,
        operations_active=True,
    )[0]
    assert model_action(busy).code == "activity"
    quarantine = build_model_items(
        [_installed(active=False, trust_state="QUARANTINED")],
        context=8192, slots=1,
    )[0]
    assert model_action(quarantine).code == "validation"
    recovery = build_model_items(
        [_installed(active=False)], context=8192, slots=1,
        recovery_required=True,
    )[0]
    assert recovery.state == "RECOVERY_REQUIRED"
    assert model_action(recovery).code == "activity"


def test_model_filters_are_closed_and_searchable():
    items = build_model_items([], context=8192, slots=1)
    assert filter_model_items(items, category="Installed") == ()
    assert filter_model_items(items, query="LFM", category="All")
    assert all("long-context" in item.tags for item in filter_model_items(items, category="Long context"))
    assert all("multi-user" in item.tags for item in filter_model_items(items, category="Multi-user"))
    with pytest.raises(ValueError):
        filter_model_items(items, category="Execute anything")


def test_model_library_keeps_all_rows_when_context_exceeds_one_model_limit():
    items = build_model_items([], context=16384, slots=4)

    assert len(items) == len(CATALOG)
    gemma = next(item for item in items if item.catalog_id == "gemma-2-9b-it")
    assert gemma.fit_verdict == "NO-FIT"
    assert "supports at most 8192 context tokens" in gemma.fit_detail
    assert filter_model_items(items, category="Recommended")


def test_gui4_pages_import_no_host_infrastructure_and_replace_home_mount():
    banned = (
        "server", "openwebui", "tailscale", "sharing", "llmmode",
        "desktop", "optimize", "model_manager", "repositories", "subprocess", "sqlite3",
    )
    for relative in ("home_page.py", "models_page.py"):
        source = Path("bc250_llm_mode/gui", relative).read_text(encoding="utf-8")
        for module in banned:
            assert f"import {module}" not in source
            assert f"from ..{module}" not in source
    shell = Path("bc250_llm_mode/gui/shell.py").read_text(encoding="utf-8")
    assert "DashboardMixin._complete(self)" not in shell
    assert "HomePage" in shell and "ModelsPage" in shell

