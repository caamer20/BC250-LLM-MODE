"""Session 3 architecture guards.

Frontends can no longer persist whole-state dictionaries, construct
stores, resolve home paths, import host adapters, or touch the runtime
handoff. Status refreshes never persist. These are textual/structural
guards: cheap, deterministic, and impossible to satisfy by accident.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "bc250_llm_mode"
FRONTENDS = ("__main__.py", "chat.py", "gui/app.py", "gui/dashboard.py",
             "gui/forms.py", "gui/steps.py")
PERSISTENCE = {"state.py", "legacy_import.py",
               "repositories.py", "db.py", "unit_of_work.py"}


def _read(rel: str) -> str:
    return (PACKAGE / rel).read_text(encoding="utf-8")


def test_compatibility_facade_is_gone():
    """R1/R2 exit gate: no facade file, import, or constructor remains."""
    assert not (PACKAGE / "compat_state.py").exists(), (
        "compat_state.py must be deleted; repositories/services are the API"
    )
    violations = []
    for py in sorted(PACKAGE.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        if "compat_state" in text or "CompatStateStore" in text:
            violations.append(str(py.relative_to(PACKAGE)))
    assert not violations, f"facade references remain: {violations}"


def test_application_has_no_generic_persistence():
    text = _read("app.py")
    for token in (
        "def save(", "def load(", "def transaction(",
        ".store", "StateStore(", "CompatStateStore(",
    ):
        assert token not in text, f"Application exposes generic persistence: {token!r}"


def test_no_path_home_outside_composition():
    """Path.home() may exist only in paths.py (authority) — nowhere else."""
    violations = []
    for py in sorted(PACKAGE.rglob("*.py")):
        rel = str(py.relative_to(PACKAGE))
        if rel == "paths.py":
            continue
        text = py.read_text(encoding="utf-8")
        # Docstring mentions are fine; only real calls violate.
        if "Path.home()" in text.replace('``Path.home()``', ""):
            violations.append(rel)
    assert not violations, f"Path.home() outside paths.py: {violations}"


def test_frontends_do_not_construct_stores():
    for rel in FRONTENDS:
        text = _read(rel)
        count = text.count("StateStore(")
        assert count == 0, (
            f"{rel}: frontend constructed StateStore ({count} > 0)"
        )
        # No whole-state writes from any frontend.
        assert ".save(" not in text, f"{rel}: frontend performed a whole-state save"
        assert ".transaction(" not in text, (
            f"{rel}: frontend used a raw transaction"
        )


def test_gui_modules_do_not_import_host_adapters():
    banned = (
        "import subprocess", "import sqlite3", "from ..repositories",
        "from .repositories", "elevated", "from ..privilege",
        "runtime-handoff.json",
    )
    for py in sorted((PACKAGE / "gui").rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        rel = str(py.relative_to(PACKAGE))
        for token in banned:
            assert token not in text, f"{rel}: GUI imported/used {token!r}"


def test_status_refresh_never_persists():
    text = _read("gui/dashboard.py")
    start = text.index("def _refresh_dashboard")
    end = text.index("\n    def ", start + 1)
    body = text[start:end]
    assert ".save(" not in body
    assert "commit_narrow" not in body
    assert "persist_state_changes" not in body


def test_runtime_handoff_written_only_by_its_service():
    allowed_files = {
        "services.py",           # lifecycle services publish on commit
        "server.py",             # regenerate_for_app_state at daemon start
        "runtime_handoff.py",    # the writer itself
        "paths.py",              # derived artifact path authority
    }
    token = "runtime-handoff.json"
    violations = []
    for py in sorted(PACKAGE.rglob("*.py")):
        rel = str(py.relative_to(PACKAGE))
        if rel in allowed_files or rel.startswith("tests"):
            continue
        if token in py.read_text(encoding="utf-8"):
            violations.append(rel)
    assert not violations, f"handoff path literal outside its service: {violations}"


# --- Session 5C: one durable activation path ---------------------------------


def test_synchronous_activation_orchestrator_is_gone():
    """R3.3 cutover: the legacy orchestrator/fallback may never return."""
    banned_symbols = (
        "ModelActivationService",
        "ActivationRequest",
        "ActivationResult",
        "RuntimeController",
        "_apply_legacy_or_raise",
        "restart_with_rollback",
    )
    for py in sorted(PACKAGE.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for symbol in banned_symbols:
            assert symbol not in text, (
                f"{py.relative_to(PACKAGE)} references deleted legacy "
                f"activation symbol {symbol!r}"
            )


def test_model_manager_never_touches_the_service_or_http_directly():
    text = _read("model_manager.py")
    for token in ("systemctl", "subprocess", "urllib", "requests",
                  "restart_service", "health_check", "stop_service"):
        assert token not in text, (
            f"model_manager.py must route host effects through the "
            f"durable command; found {token!r}"
        )


def test_runtime_keys_are_not_frontend_committable():
    from bc250_llm_mode.app import FRONTEND_COMMIT_KEYS

    for key in ("current_model", "current_ctx", "optimizations"):
        assert key not in FRONTEND_COMMIT_KEYS, (
            f"{key!r} is owned by the durable MODEL_ACTIVATE workflow; a "
            "frontend generic commit would be a second activation path"
        )


def test_only_server_py_controls_the_llm_service_unit():
    """One service owner: the durable activation path never constructs
    service commands — every effect routes through server.py via the
    injected port. Boot-policy/desktop modules keep their own legitimate
    systemctl usage for system targets."""
    guarded = [
        "model_manager.py",
        "activation_adapter.py",
        "activation_command.py",
    ]
    for rel in guarded:
        text = _read(rel)
        for token in ("systemctl", "bc250-llm.service"):
            assert token not in text, (
                f"{rel}: activation path must route host effects through "
                f"server.py; found {token!r}"
            )
    for py in sorted((PACKAGE / "operations").rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for token in ("systemctl", "bc250-llm.service"):
            assert token not in text, (
                f"operations/{py.name}: host command in generic engine: "
                f"{token!r}"
            )


def test_gateway_is_the_only_bridge_to_the_backend():
    """ADR 005 D2/D4: the raw llama backend is never a serve/publication
    target for remote/container traffic. sharing/openwebui route through
    the gateway; the raw 127.0.0.1:8080 backend address must not appear as
    a publish/proxy target in those integration modules."""
    _read = lambda rel: (PACKAGE / rel).read_text(encoding="utf-8")
    for rel in ("sharing.py", "openwebui.py"):
        text = _read(rel)
        # no raw backend as a proxy/publish target in integration modules
        assert "127.0.0.1:8080" not in text, (
            f"{rel}: raw backend address must not be a serve/publication "
            "target; route remote/container traffic through the gateway"
        )
    # the gateway routes to the backend (allowed, internal): gateway.py
    # is the ONLY module that bridges to the backend proxy surface.
    gateway_text = _read("gateway.py")
    assert "backend_base" in gateway_text