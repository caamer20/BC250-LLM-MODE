"""R2.2 cutover: composition root serves SQLite; JSON is a read-only backup.

Covers the ADR 001 cutover sequence end to end:
- compose returns the compatibility facade over an initialized database
- one-time automatic legacy import on first run
- round-trip fidelity of the compatibility view
- optimistic revision checks and lost-update-safe transactions
- runtime handoff rendering consumed by the launcher (behavioral)
- guard freezing direct StateStore instantiation in production
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from bc250_llm_mode.app import Application, load_state_with_paths
from bc250_llm_mode.compat_state import CompatStateStore, StaleStateError
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.server import generate_launcher

FIXTURES = Path(__file__).parent / "fixtures"


def _compose(tmp_path):
    return Application.compose(AppPaths.temporary(tmp_path / "root"))


def test_compose_returns_sqlite_backed_store(tmp_path):
    app = _compose(tmp_path)
    assert isinstance(app.store, CompatStateStore)
    # Database initialized with migration 001 applied.
    applied = {
        row["version"]
        for row in app.store.conn.execute("SELECT version FROM schema_migrations")
    }
    assert 1 in applied


def test_compose_auto_imports_legacy_state_once(tmp_path):
    root = tmp_path / "root"
    paths = AppPaths.temporary(root)
    paths.ensure_directories()
    fixture = (FIXTURES / "state_v5.json").read_text(encoding="utf-8")
    original = json.loads(fixture)
    paths.legacy_state_path.write_text(json.dumps(original), encoding="utf-8")

    app = Application.compose(paths)
    state = app.store.load()
    assert state["disclaimer_ack"] is True or isinstance(state.get("setup_phase"), int)
    # Import published a database next to the read-only backup.
    assert paths.database_path.exists()

    # Mutate through the facade; the backup stays byte-identical.
    before = paths.legacy_state_path.read_bytes()
    fresh = app.store.load()
    fresh["server_port"] = 9999
    app.store.save(fresh)
    assert paths.legacy_state_path.read_bytes() == before

    # Second compose must not re-import over newer SQLite data.
    app2 = Application.compose(paths)
    reloaded = app2.store.load()
    assert reloaded["server_port"] == 9999


def test_compat_round_trip_preserves_everything(tmp_path):
    store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    state = store.load()
    state.update(
        setup_complete=True,
        setup_phase=5,
        disclaimer_ack=True,
        server_port=8123,
        current_model="lfm25-26b",
        current_ctx=16384,
        selected_model="lfm25-26b",
        selected_quant="Q4_K_M",
        thermal_watchdog_state="nominal",  # stale draft claims nominal...
        thermal_watchdog_baseline=None,
        llamacpp_build={"describe": "b6000-abc", "commit": "abc123"},
        installed_models=[{
            "id": "lfm25-26b",
            "path": "/models/lfm25.gguf",
            "quant": "Q4_K_M",
            "display_name": "LFM2.5 2.6B",
            "temperature": 0.4,
        }],
        bench_history=[{"timestamp": "2026-08-21T00:00:00Z", "tps": 41.5}],
        autotune_history=[{"ctx": 16384, "tps": 40.0}],
        some_future_key={"nested": [1, 2, 3]},
    )
    state["optimizations"] = {
        **state["optimizations"],
        "runtime_enabled": True,
        "parallel_slots": 2,
        "threads": 6,
        "fast_sync": True,
    }
    store.save(state)

    reloaded = CompatStateStore(AppPaths.temporary(tmp_path / "root")).load()
    assert reloaded["server_port"] == 8123
    assert reloaded["current_model"] == "lfm25-26b"
    assert reloaded["current_ctx"] == 16384
    assert reloaded["selected_quant"] == "Q4_K_M"
    # The latch is safety-authoritative: the stale draft claimed "nominal",
    # but the durable default (nominal, no baseline) is all a whole-state
    # save can ever see — it cannot manufacture or clear a latched stop.
    assert reloaded["thermal_watchdog_state"] == "nominal"
    assert reloaded["thermal_watchdog_baseline"] is None
    assert reloaded["llamacpp_build"]["describe"] == "b6000-abc"
    assert len(reloaded["installed_models"]) == 1
    assert reloaded["installed_models"][0]["temperature"] == 0.4
    assert reloaded["bench_history"][0]["tps"] == 41.5
    assert reloaded["autotune_history"][0]["ctx"] == 16384
    assert reloaded["some_future_key"] == {"nested": [1, 2, 3]}
    assert reloaded["optimizations"]["threads"] == 6
    assert reloaded["optimizations"]["parallel_slots"] == 2
    # Derived paths recomputed from the profile, never persisted.
    assert reloaded["app_dir"] == str(AppPaths.temporary(tmp_path / "root").app_dir)
    assert "app_dir" not in store.settings.all()
    assert "logs_dir" not in store.settings.all()


def test_compat_save_uses_optimistic_revision(tmp_path):
    store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    state = store.load()
    other = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    other.save(other.load())  # bumps revision behind `store`'s back

    state["server_port"] = 7000
    with pytest.raises(StaleStateError):
        store.save(state)

    fresh = store.load()
    fresh["server_port"] = 7000
    store.save(fresh)
    assert store.load()["server_port"] == 7000


def test_compat_transaction_is_lost_update_safe(tmp_path):
    paths = AppPaths.temporary(tmp_path / "root")
    store_a = CompatStateStore(paths)
    store_b = CompatStateStore(paths)

    def bump(state):
        state["optimizations"]["threads"] = (
            (state["optimizations"].get("threads") or 0) + 1
        )
        return state

    def run_bump(store, times):
        for _ in range(times):
            store.transaction(bump)

    threads = [
        threading.Thread(target=run_bump, args=(s, 10)) for s in (store_a, store_b)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = CompatStateStore(paths).load()
    assert final["optimizations"]["threads"] == 20


def test_same_store_concurrent_operations_are_serialized(tmp_path):
    """P1-6: one shared store used from several threads stays consistent.

    Transaction workers hold the io lock for whole read-modify-write
    cycles; plain save workers reload-and-retry on StaleStateError, which
    is the expected optimistic-convergence pattern. No update may be lost.
    """
    store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    errors: list[BaseException] = []

    def bump(state):
        state["optimizations"]["threads"] = (
            (state["optimizations"].get("threads") or 0) + 1
        )
        return state

    def tx_worker():
        try:
            for _ in range(10):
                store.transaction(bump)
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    def save_worker():
        try:
            for _ in range(10):
                s = store.load()
                s["optimizations"]["fast_sync"] = not bool(
                    s["optimizations"].get("fast_sync")
                )
                while True:
                    try:
                        store.save(s)
                        break
                    except StaleStateError:
                        s = store.load()  # converge and retry
        except BaseException as exc:
            errors.append(exc)

    workers = [
        threading.Thread(target=tx_worker),
        threading.Thread(target=tx_worker),
        threading.Thread(target=save_worker),
        threading.Thread(target=save_worker),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert not errors, errors
    final = CompatStateStore(AppPaths.temporary(tmp_path / "root")).load()
    assert final["optimizations"]["threads"] == 20


def test_stale_draft_detected_after_same_store_refresh(tmp_path):
    """P0-2: the revision carried by the saved state is what matters.

    A background reload on the same store must not let an older draft
    overwrite newer data.
    """
    store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    draft = store.load()  # revision R

    other = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    other.save(other.load())  # database moves to R+1

    # Same store refreshes its view for a status read — the draft is still R.
    store.load()
    draft["server_port"] = 1234
    with pytest.raises(StaleStateError):
        store.save(draft)

    # Reloading produces a current draft; saving then works and writes the
    # new revision back into the mapping (long-lived GUI drafts stay usable).
    fresh = store.load()
    fresh["server_port"] = 1234
    store.save(fresh)
    assert fresh["revision"] == store.load()["revision"]
    assert store.load()["server_port"] == 1234


def test_transaction_accepts_replacement_mapping(tmp_path):
    """P1-5: pure mutators returning a new mapping must be persisted."""
    store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))

    def pure(state):
        replacement = dict(state)
        replacement["server_port"] = 8181
        return replacement

    result = store.transaction(pure)
    assert result["server_port"] == 8181
    assert store.load()["server_port"] == 8181


def test_transaction_none_cancels_the_write(tmp_path):
    store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    before = store.settings.revision()

    def cancel(_state):
        return None

    result = store.transaction(cancel)
    assert result is not None
    assert store.settings.revision() == before


def test_transaction_rejects_non_dict_return(tmp_path):
    store = CompatStateStore(AppPaths.temporary(tmp_path / "root"))
    with pytest.raises(TypeError):
        store.transaction(lambda _state: "not-a-mapping")


def test_handoff_rendered_on_save_and_consumed_by_launcher(tmp_path):
    paths = AppPaths.temporary(tmp_path / "root")
    store = CompatStateStore(paths)
    state = store.load()
    state.update(
        current_model="lfm25-26b",
        current_ctx=16384,
        llama_cpp_path=str(tmp_path / "llama.cpp"),
        server_port=9091,
    )
    state["installed_models"] = [{
        "id": "lfm25-26b",
        "path": "/models/lfm.gguf",
        "display_name": "LFM 2.5\n2.6B",  # newline must be sanitized
        "temperature": 0.35,
    }]
    state["optimizations"] = {
        **state["optimizations"],
        "runtime_enabled": True,
        "parallel_slots": 2,
        "threads": 6,
        "fast_sync": True,
    }
    store.save(state)

    handoff = paths.app_dir / "runtime-handoff.json"
    assert handoff.exists()
    mode = os.stat(handoff).st_mode & 0o777
    assert mode == 0o600
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["ctx_total"] == 32768  # 16384 x 2 slots
    assert payload["port"] == 9091
    assert payload["alias"] == "LFM 2.5 2.6B"
    assert payload["fast_sync"] is True

    # Behavioral: the launcher consumes the handoff, not the legacy JSON.
    bin_dir = tmp_path / "llama.cpp" / "build" / "bin"
    bin_dir.mkdir(parents=True)
    record = tmp_path / "argv.txt"
    stub = bin_dir / "llama-server"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$@" >> "$BC250_RECORD"\n'
        'printf "FORCE_SYNC=%s\\n" "${GGML_VK_FORCE_SYNC-UNSET}" >> "$BC250_RECORD"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    launcher = generate_launcher(store.load())
    result = subprocess.run(
        ["bash", str(launcher)],
        env={**os.environ, "BC250_RECORD": str(record)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    argv = record.read_text(encoding="utf-8").splitlines()
    pairs = dict(zip(argv, argv[1:] + [""]))
    assert pairs["--threads"] == "6"
    assert pairs["--ctx-size"] == "32768"
    assert pairs["--port"] == "9091"
    assert pairs["--alias"] == "LFM 2.5 2.6B"
    # fast_sync=True must NOT force sync.
    assert "FORCE_SYNC=UNSET" in argv


def test_guard_whole_state_saves_are_frozen():
    """P1-8 / Phase A: the exact whole-state save inventory, per file.

    This is a migration checklist, not an allowance: the number for every
    file is asserted EXACTLY. Each sweep slice must reduce its counts in
    the same commit; zero across the board is the only R2 exit value.
    Reduced so far: thermals (2 -> 0 via ThermalStateService), chat
    benchmark history (narrow repository append), tune autotune history.

    state.py/compat_state.py are the persistence implementations themselves,
    legacy_import.py's staging canonicalize is importer-specific legacy
    usage — all three are exempt until Session 4 removes the facade.
    """
    import re

    package = Path(__file__).parent.parent / "bc250_llm_mode"
    allowed_saves = {
        "__main__.py": 10,
        "bootstrap.py": 3,
        "chat.py": 4,
        "model_manager.py": 3,
        "tune.py": 2,
        "gui/app.py": 3,
        "gui/dashboard.py": 7,
        "gui/forms.py": 1,
        "gui/steps.py": 10,
    }
    allowed_transactions = {"chat.py": 1, "thermals.py": 3, "tune.py": 1}
    exempt = {"state.py", "compat_state.py", "legacy_import.py"}

    for py in sorted(package.rglob("*.py")):
        rel = str(py.relative_to(package))
        if rel in exempt:
            continue
        text = py.read_text(encoding="utf-8")
        saves = text.count(".save(")
        transactions = len(re.findall(r"\.transaction\(", text))

        expected_saves = allowed_saves.get(rel, 0)
        assert saves == expected_saves, (
            f"{rel}: whole-state save count drifted ({saves} != {expected_saves}); "
            "reduce it in the same commit as the narrow-persistence change"
        )
        expected_transactions = allowed_transactions.get(rel, 0)
        assert transactions == expected_transactions, (
            f"{rel}: transaction count drifted "
            f"({transactions} != {expected_transactions})"
        )


def test_guard_direct_statestore_instantiation_is_frozen():
    """Compatibility saves are driven toward zero; this freezes the count.

    Allowed today (transitional): the --state legacy branch, the two
    no-store fallbacks in chat/GUI constructors, and the importer's staging
    canonicalize step. Each removal is a commit; new call sites are banned.
    """
    package = Path(__file__).parent.parent / "bc250_llm_mode"
    allowed = {
        "__main__.py": 1,
        "chat.py": 2,  # fallback chain on one line; collapses to 0 when migrated
        "gui/app.py": 1,
        "legacy_import.py": 1,
    }
    for py in sorted(package.rglob("*.py")):
        rel = str(py.relative_to(package))
        count = py.read_text(encoding="utf-8").count("StateStore(")
        if rel in allowed:
            assert count <= allowed[rel], (
                f"{rel}: StateStore( usage grew ({count} > {allowed[rel]})"
            )
        elif rel == "app.py":
            assert "CompatStateStore(resolved)" in py.read_text(encoding="utf-8")
        else:
            assert count == 0, f"{rel}: new direct StateStore( usage"