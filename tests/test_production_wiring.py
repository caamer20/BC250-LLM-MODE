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


def test_component_lifecycle_provisions_and_routes_runtime_to_durable_command(
    tmp_path, monkeypatch,
):
    """U1.2 cutover: provisioning reaches env; runtime goes to the ONE
    durable command (no synchronous update/rollback methods exist)."""
    import bc250_llm_mode.env as env

    application = _composed(tmp_path)
    view = dict(application.read_model())
    runner = QuietRunner()

    monkeypatch.setattr(env, "setup_environment",
                        lambda st, rn: {"ok": True})
    assert application.component.provision_environment(view, runner) == {
        "ok": True
    }
    # The legacy synchronous routes are deleted at the type level.
    assert not hasattr(application.component, "update_llamacpp")
    assert not hasattr(application.component, "rollback_llamacpp")
    assert application.runtime_lifecycle is not None


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
    """F0.2 regression, Session 5C form: CLI/chat/GUI activation must use
    the composed durable command exactly once — one restart, no second
    restart, no generic frontend commit."""
    from bc250_llm_mode.model_manager import switch_model

    application = _composed(tmp_path)
    import struct

    def _s(value: bytes) -> bytes:
        return struct.pack("<Q", len(value)) + value

    model_path = tmp_path / "lfm.gguf"
    model_path.write_bytes(
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<Q", 1)
        + struct.pack("<Q", 1)
        + _s(b"general.architecture")
        + struct.pack("<I", 8)
        + _s(b"llama")
    )
    model = {
        "id": "lfm25-26b", "path": str(model_path), "quant": "Q5_K_M",
        "display_name": "LFM 2.5 2.6B",
    }
    _seed_model(application, model)

    # Intercept at the server-module boundary: the composed adapter's port
    # delegates ALL service control and HTTP to bc250_llm_mode.server.
    import bc250_llm_mode.server as server

    restarts = []
    monkeypatch.setattr(
        server, "restart_service",
        lambda st, rn: restarts.append("restart"),
    )
    monkeypatch.setattr(
        server, "health_check",
        lambda st, rn=None, timeout=120, monotonic=None, sleep=None: {
            "healthy": True,
            "model_id": st.get("current_model"),
            "n_ctx": int(st.get("current_ctx", 8192))
            * int((st.get("optimizations") or {}).get("parallel_slots", 4)),
            "parallel_slots": int(
                (st.get("optimizations") or {}).get("parallel_slots", 4)
            ),
        },
    )
    monkeypatch.setattr(server, "minimal_inference_probe", lambda st, timeout=20.0: {"ok": True})

    class _ActiveRunner(QuietRunner):
        def run(self, *command, **_kwargs):
            if command[:2] == ("systemctl", "is-active") or list(command)[:2] == [
                "systemctl",
                "is-active",
            ]:
                return SimpleNamespace(returncode=0, stdout="active", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    # The adapter's runner factory is the composed application runner;
    # point logging at our active-reporting runner for observation calls.
    application.runner = lambda callback=None: _ActiveRunner()

    state = application.read_model()
    returned = switch_model(application, state, model["id"], _ActiveRunner())

    # Exactly one restart for the whole switch; the draft was refreshed.
    assert restarts == ["restart"]
    assert returned["current_model"] == model["id"]
