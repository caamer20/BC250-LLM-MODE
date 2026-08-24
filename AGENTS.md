# Continuation guide for BC250 LLM MODE

## Current state

Python 3.11+ project for an AMD BC-250 running Bazzite: configures a local
`llama.cpp` Vulkan server behind a single systemd service, with a resumable
native tkinter wizard/dashboard and a terminal chat client. The working tree
is at **`0.9.0.dev0`** with a **clean tree, 330-test green baseline**, and
reviewed commits above `v0.7.0` (the pre-SQLite tree is tagged
`v0.8.0-pre-sqlite` at `2126d61`) covering: 24-model catalog with
tiers/recommendations, chat + benchmark features, thermal latch/baseline
watchdog, autotune, llama.cpp staged update/rollback, schema v5 + ordered
atomic migrations, production hardening, the `gui/` package, the **SQLite
cutover with the compatibility facade removed** (repositories, unit of work,
query layer, typed services, repair gate, durable publication, runtime
handoff renderer/service), and the closed R1/R2 exit gate.

## Where we are in the master plan

Executing `POST_R2_PRODUCTION_IMPLEMENTATION_PLAN.md` (sequencing authority;
the master plan remains requirements authority). **Phase 0 (Session 4.1) is
DONE**: one SQLite connection policy (`db.open_database`) with test-proven
FK/query-only contracts and deterministic composition close; production
wiring repaired (host-mode imports, composed-activation single sequence,
rollback inference verification); launcher is handoff-only with strict
fail-closed validation; legacy canonicalization is pure (`legacy_schema.py`)
and the writable JSON store exists only as test support; duplicate
post-service commits removed with owners recorded; docs truth pass complete.
Next: **Session 5A — ADR 002, migration 003, operation state machine and
repositories** (per the predecessor plan Part II §§7–8), then 5B/5C.

1. ~~Sessions 1–4: sweeps, facade removal, R1/R2 exit gate~~ **DONE**.
2. ~~Session 4.1: post-R2 production wiring stabilization~~ **DONE**.
3. **Session 5A–5C (R3 operation engine)**: ADR 002 + migration 003 +
   operation state machine/repository; executor, leases, events,
   cancellation, recovery with crash-injection foundation; convert model
   activation to durable steps. Then Session 6 (acquisition, runtime update,
   Activity view), R4 typed adapters/timeouts, and the later phases of the
   post-R2 plan.

## Layout highlights

| Area | Files |
| --- | --- |
| GUI package | `gui/app.py`, `gui/steps.py`, `gui/dashboard.py`, `gui/forms.py`; `Wizard`/`run_gui` composed in `gui/__init__.py`; surface frozen by headless contract test |
| State | `state.py` (legacy JSON, schema v5, transaction()), `compat_state.py` (SQLite compatibility facade), `repositories.py` (typed SQL access), `paths.py` (AppPaths incl. database/legacy/migration paths), `db.py` (SQLite PRAGMA contract + migrations), `legacy_import.py` (one-time importer) |
| Safety runtime | `thermals.py` (hysteresis/latch/baseline/reset_latch), `optimize.py` (`apply_gpu_clock_limit`, `restore_gpu_profile`) |
| llama.cpp lifecycle | `env.py` (`llamacpp_status/update/rollback`; staged source clone, atomic swap) |
| Composition | `app.py` (`Application.compose`, `load_state_with_paths`) |

## Invariants (do not break)

- One service owner: only `server.py` touches `bc250-llm.service`.
- Fit gate: model/context/slot changes pass `calculate_fit`; NO-FIT never runs.
- Reboot safety: next boot is always the desktop; nothing auto-starts.
- Reversibility: host tuning records prior state; uninstall reverts it.
- Secrets never appear in argv or logs (HF token rides a 0600 env-file).
- llama.cpp updates leave the active checkout untouched until a staged build
  passes smoke checks; failed health restarts restore the previous tree.
- Thermal stops latch until an explicit safe-temperature `thermals reset`.
- After SQLite cutover: no dual writes; JSON stays a read-only backup;
  derived paths come from injected `AppPaths`.

## Verification

```bash
PYTHONPATH=. .venv/bin/pytest -q        # full suite; the terminal summary
                                        # prints the authoritative collected
                                        # test count (never infer from dots)
.venv/bin/pytest tests --collect-only -q
python -m compileall -q bc250_llm_mode tests
```

The behavioral launcher test needs only bash ≥3.2 and python3 on PATH.

### Test-count reconciliation record (Session 4.1 §3.1)

- Handoff at `7672e7d` claimed **313**; the audited checkout collected **301**
  — the earlier figure was stale because Session 4C deleted the facade-only
  cutover tests without updating the handoff.
- Session 4.1 added connection-contract, production-wiring, canonicalizer,
  and launcher fail-closed tests; the reconciled baseline is now **330**,
  printed automatically by `tests/conftest.py` in every run's summary.
- Source (`PYTHONPATH=.`) and editable-install invocation collect identically.

## Development conventions

Keep changes small and test-first where practical; extend fakes rather than
invoking system services; keep command construction inspectable (no shell
interpolation for user/model paths); preserve atomic state writes, rollback
behavior, and the README/ARCHITECTURE documentation contract. Cite master-plan
task IDs (e.g., R2.2) in commit messages.
