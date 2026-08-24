"""Session 4.1 §3.3: production-wiring contract tests.

F0.1 proved that green surface tests can hide broken production imports:
``HostModeService`` imported ``.desktop_mode``/``.llm_mode``, which do not
exist, and no test executed those seams. These guards resolve every lazy
production import statically and invoke every composed service method with
fakes at the adapter boundary (the target module function), never by
monkeypatching away the service's own imports.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from _gui_stubs import install  # noqa: E402

install()  # __main__ lazily imports .gui; headless environments need stubs

PACKAGE = Path(__file__).parent.parent / "bc250_llm_mode"

# Every lazy ``from .module import name`` in production code must resolve.
PRODUCTION_MODULES = (
    "services.py", "server.py", "chat.py", "model_manager.py", "tune.py",
    "thermals.py", "__main__.py", "bootstrap.py", "queries.py",
)


@pytest.mark.parametrize("rel", PRODUCTION_MODULES)
def test_every_lazy_production_import_resolves(rel):
    text = (PACKAGE / rel).read_text(encoding="utf-8")
    for mod, attr in re.findall(r"from \.(\w+) import (\w+)", text):
        resolved = importlib.import_module(f"bc250_llm_mode.{mod}")
        assert hasattr(resolved, attr), f"{rel}: .{mod}.{attr} does not exist"


class QuietRunner:
    def emit(self, *_lines):
        pass

    def run(self, *command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _composed(tmp_path):
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.paths import AppPaths

    paths = AppPaths.temporary(tmp_path / "root")
    application = Application.compose(paths)
    state = application.read_model()
    application.commit_settings_changes(
        state, {**state, "disclaimer_ack": True}
    )
    return application


def _seed_model(application, model):
    from bc250_llm_mode.repositories import ModelInstallationsRepository, SettingsRepository

    with application.units.begin() as conn:
        ModelInstallationsRepository(conn).replace_all([model])
    with application.units.begin() as conn:
        settings = SettingsRepository(conn)
        settings.set_many({"current_model": model["id"]})
        settings.set_revision(settings.revision() + 1)


def test_host_mode_service_methods_reach_real_modules(tmp_path, monkeypatch):
    """F0.1 regression: these imports raised ModuleNotFoundError in production."""
    import bc250_llm_mode.desktop as desktop
    import bc250_llm_mode.llmmode as llmmode

    application = _composed(tmp_path)
    view = dict(application.read_model())
    runner = QuietRunner()
    calls = []

    monkeypatch.setattr(
        llmmode, "stage_desktop_boot",
        lambda st, rn: calls.append("stage") or st.update(boot_policy="desktop"),
    )
    monkeypatch.setattr(
        llmmode, "apply_llm_mode",
        lambda st, rn: calls.append("apply") or st.update(system_mode="llm-session"),
    )
    monkeypatch.setattr(
        desktop, "switch_to_desktop_mode",
        lambda st, rn, *, activate_now=False: calls.append("switch"),
    )

    result = application.host_mode.enforce_desktop_next_boot(view, runner)
    assert result["boot_policy"] == "desktop"
    result = application.host_mode.enter_llm_mode(view, runner)
    assert result["system_mode"] == "llm-session"
    result = application.host_mode.return_to_desktop(view, runner)
    assert result["boot_policy"] == "desktop"
    assert calls == ["stage", "apply", "switch"]


def test_component_lifecycle_methods_reach_env_adapters(tmp_path, monkeypatch):
    import bc250_llm_mode.env as env

    application = _composed(tmp_path)
    view = dict(application.read_model())
    runner = QuietRunner()

    monkeypatch.setattr(env, "setup_environment", lambda st, rn: {"ok": True})
    monkeypatch.setattr(env, "update_llamacpp", lambda st, rn, *, tag=None: {"ok": True})
    monkeypatch.setattr(env, "rollback_llamacpp", lambda st, rn: {"ok": True})

    assert application.component.setup_environment(view, runner) == {"ok": True}
    assert application.component.update_llamacpp(view, runner, tag="b9000")
    assert application.component.rollback_llamacpp(view, runner) == {"ok": True}


def test_openwebui_and_sharing_methods_reach_adapters(tmp_path, monkeypatch):
    import bc250_llm_mode.openwebui as openwebui
    import bc250_llm_mode.sharing as sharing

    application = _composed(tmp_path)
    view = dict(application.read_model())
    runner = QuietRunner()

    monkeypatch.setattr(openwebui, "install_open_webui", lambda st, rn: {"installed": True})
    monkeypatch.setattr(openwebui, "start_open_webui", lambda st, rn: {"running": True})
    monkeypatch.setattr(openwebui, "stop_open_webui", lambda st, rn: {"running": False})
    monkeypatch.setattr(openwebui, "restart_open_webui", lambda st, rn: {"running": True})
    monkeypatch.setattr(openwebui, "open_webui_status", lambda st, rn: {"running": False})

    assert application.openwebui.install(view, runner) == {"installed": True}
    assert application.openwebui.start(view, runner) == {"running": True}
    assert application.openwebui.restart(view, runner) == {"running": True}
    assert application.openwebui.stop(view, runner) == {"running": False}
    assert application.openwebui.status(view, runner) == {"running": False}

    monkeypatch.setattr(sharing, "start_https_sharing", lambda st, rn: {"available": True})
    monkeypatch.setattr(sharing, "stop_https_sharing", lambda st, rn: {"available": False})
    assert application.sharing.start(view, runner) == {"available": True}
    assert application.sharing.stop(view, runner) == {"available": False}


def test_uninstall_reaches_maintenance_adapter(tmp_path, monkeypatch):
    import bc250_llm_mode.uninstall as uninstall_module

    application = _composed(tmp_path)
    view = dict(application.read_model())
    seen = {}

    def fake_uninstall(st, rn, *, remove_container=False, remove_models=False):
        seen.update(container=remove_container, models=remove_models)
        return {"removed": True}

    monkeypatch.setattr(uninstall_module, "uninstall", fake_uninstall)
    result = application.maintenance.uninstall(
        view, QuietRunner(), remove_container=True, remove_models=False
    )
    assert result == {"removed": True}
    assert seen == {"container": True, "models": False}


def test_activation_via_composed_application_single_restart(tmp_path, monkeypatch):
    """F0.2 regression: CLI/chat/GUI activation must use the composed
    service exactly once — no second restart, no deleted-API refresh."""
    from bc250_llm_mode.model_manager import switch_model

    application = _composed(tmp_path)
    model = {
        "id": "lfm25-26b", "path": "/models/lfm.gguf", "quant": "Q5_K_M",
        "display_name": "LFM 2.5 2.6B",
    }
    _seed_model(application, model)

    # Intercept at the adapter boundary: the server seams the composed
    # controller delegates to (Application._wire_services).
    import bc250_llm_mode.server as server

    restarts = []
    monkeypatch.setattr(
        server, "restart_service",
        lambda st, rn: restarts.append("restart"),
    )
    monkeypatch.setattr(server, "health_check", lambda st, rn=None: {"healthy": True})
    monkeypatch.setattr(server, "minimal_inference_probe", lambda st: {"tokens": 1})

    state = application.read_model()
    returned = switch_model(application, state, model["id"], QuietRunner())

    # Exactly one restart for the whole switch; the draft was refreshed.
    assert restarts == ["restart"]
    assert returned["current_model"] == model["id"]
