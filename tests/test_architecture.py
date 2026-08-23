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