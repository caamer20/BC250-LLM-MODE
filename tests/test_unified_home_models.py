from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.catalog import ADVERTISED_CATALOG  # noqa: E402
from bc250_llm_mode.gui.home_page import HomePage, build_home_view  # noqa: E402
from bc250_llm_mode.gui.models_page import (  # noqa: E402
    MODEL_PRESENTATION_STATES,
    ModelsPage,
    build_install_progress_view,
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


def test_home_refresh_reuses_shortcut_widgets_instead_of_recreating_them():
    class Shell:
        def request_observation(self, _work, _apply):
            return False

        def navigate(self, _target):
            return None

    page = HomePage(None, Shell(), SimpleNamespace())
    shortcuts = tuple(page._shortcut_buttons)

    page._apply_snapshot(_home())
    page._apply_snapshot(_home(inference="UNVERIFIED"))

    assert tuple(page._shortcut_buttons) == shortcuts
    assert [button is original for button, original in zip(page._shortcut_buttons, shortcuts)] == [True] * 4


def test_activity_shelf_does_not_anchor_after_a_hidden_notice_bar():
    source = Path("bc250_llm_mode/gui/shell.py").read_text(encoding="utf-8")

    assert "after=self.notice_bar" not in source
    assert "def _show_activity_shelf" in source


def test_gui_pages_do_not_overwrite_tk_widget_identity_fields():
    reserved = {"_name", "_w", "children", "master", "tk"}
    violations = []
    for path in Path("bc250_llm_mode/gui").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr in reserved
                ):
                    violations.append(f"{path.name}:{node.lineno}: self.{target.attr}")
    assert violations == []


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

    assert len(items) == len(ADVERTISED_CATALOG)
    gemma = next(item for item in items if item.catalog_id == "gemma-2-9b-it")
    assert gemma.fit_verdict == "NO-FIT"
    assert "supports at most 8192 context tokens" in gemma.fit_detail
    assert filter_model_items(items, category="Recommended")


def test_conversion_sources_are_hidden_but_existing_local_ggufs_remain_visible():
    hidden = {"qwen38-9b-distill", "defiant-fable-9b"}
    items = build_model_items(
        [
            _installed(
                alias="existing-converted-gguf",
                catalog_id="qwen38-9b-distill",
                active=False,
            )
        ],
        context=8192,
        slots=1,
    )

    assert any(item.alias == "existing-converted-gguf" for item in items)
    assert not any(item.remote and item.catalog_id in hidden for item in items)

    setup_source = Path("bc250_llm_mode/gui/setup_forms.py").read_text(
        encoding="utf-8"
    )
    assert "for model in ADVERTISED_CATALOG" in setup_source
    assert "for model in CATALOG" not in setup_source


def test_install_progress_exposes_phase_bytes_and_percent_on_models_page():
    progress = build_install_progress_view(
        SimpleNamespace(
            state="RUNNING",
            progress_current=2 * 1024**3,
            progress_total=8 * 1024**3,
            progress_unit="bytes",
        ),
        model_name="Qwen test",
        current_step="transfer_source",
    )

    assert progress.mode == "determinate"
    assert progress.value == pytest.approx(25.0)
    assert progress.message == (
        "Downloading: Qwen test — 2.00 GiB of 8.00 GiB (25%)."
    )


def test_install_progress_stays_visible_during_non_byte_phases():
    progress = build_install_progress_view(
        SimpleNamespace(
            state="RUNNING",
            progress_current=0,
            progress_total=None,
            progress_unit=None,
        ),
        model_name="Qwen test",
        current_step="validate_candidate",
    )

    assert progress.mode == "indeterminate"
    assert progress.value == 0.0
    assert progress.message.startswith("Verifying model: Qwen test.")

    source = Path("bc250_llm_mode/gui/models_page.py").read_text(
        encoding="utf-8"
    )
    assert 'text="Install / start progress"' in source
    assert 'text="View installation details"' in source
    assert '"MODEL_ACQUIRE", "MODEL_IMPORT", "MODEL_ACTIVATE"' in source
    assert "def refresh_progress" in source
    shell_source = Path("bc250_llm_mode/gui/shell.py").read_text(
        encoding="utf-8"
    )
    assert 'getattr(page, "refresh_progress", None)' in shell_source


def test_models_page_shows_immediate_install_feedback_before_first_poll():
    shell = SimpleNamespace(
        reduced_motion=True,
        request_observation=lambda _observe, _apply: False,
        navigate=lambda _route: None,
    )
    application = SimpleNamespace(
        runtime_config=SimpleNamespace(
            current=lambda: {"context": 8192, "slots": 1}
        )
    )
    page = ModelsPage(None, shell, application)

    assert page.install_progress_text.get() == (
        "No model installation is currently running."
    )
    page._begin_install_progress("Qwen test")
    assert page.install_progress_text.get() == (
        "Starting installation: Qwen test."
    )
    assert page._install_model_name(
        SimpleNamespace(kind="MODEL_ACQUIRE"),
        SimpleNamespace(request={"model_id": "qwen3-8b"}),
    ) == "Qwen3 8B"

    page._finish_install_progress(
        model_name="Qwen test",
        acquisition=SimpleNamespace(ok=True, status="SUCCEEDED"),
        activation=None,
        activation_expected=True,
    )
    assert page.install_progress_text.get() == (
        "Installed Qwen test, but starting it needs attention. "
        "Open Activity for details."
    )
    assert page._install_terminal_ok is False


def test_periodic_gui_refreshes_reuse_action_button_widgets():
    models = Path("bc250_llm_mode/gui/models_page.py").read_text(encoding="utf-8")
    activity = Path("bc250_llm_mode/gui/activity.py").read_text(encoding="utf-8")
    connections = Path(
        "bc250_llm_mode/gui/connections_page.py"
    ).read_text(encoding="utf-8")
    system = Path("bc250_llm_mode/gui/system_page.py").read_text(encoding="utf-8")

    assert "action_bar.winfo_children()" not in models
    assert "action_bar.winfo_children()" not in activity
    assert "self._rendered_visible" in models
    assert "if view == previous_view:" in connections
    assert "self._card_widgets" in system


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

