# Continuation guide for BC250 LLM MODE

## Current state

Python 3.11+ project for an AMD BC-250 running Bazzite: configures a local
`llama.cpp` Vulkan server behind a single systemd service, with a resumable
native tkinter wizard/dashboard and a terminal chat client. The working tree
is at **`0129ab8`+ (post-`v0.7.0`, version `0.8.0.dev0`)** with a **clean
tree, 206+-test green baseline**, and reviewed commits above the tag covering:
24-model catalog with tiers/recommendations, chat + benchmark features,
thermal latch/baseline watchdog, autotune, llama.cpp staged update/rollback,
schema v5 + transactional store, production hardening, and the `gui/` package.

## Where we are in the master plan

Executing `MASTER_IMPLEMENTATION_PLAN.md`. **R0.x, R1.3, R2.1 are DONE**;
**R2.2 core is landed** (`db.py` PRAGMA contract/migrations/integrity +
`legacy_import.py` one-time JSON importer per ADR 001). Remaining for 0.9:
repositories/facade, compatibility-facade call-site cutover, startup repair
mode. Full status table lives in the plan's §11 handoff log — consult it
before continuing; do not duplicate it here.

## Immediate next tasks

1. **R1.1 closeout sweep**: finish path injection in download, prepare,
   environment, Open WebUI, chat conversation paths, bootstrap.
2. **R2.2 cutover**: repositories/facade → switch composition root to SQLite
   in one commit → drive compatibility-save call sites to zero.

## Layout highlights

| Area | Files |
| --- | --- |
| GUI package | `gui/app.py`, `gui/steps.py`, `gui/dashboard.py`, `gui/forms.py`; `Wizard`/`run_gui` composed in `gui/__init__.py`; surface frozen by headless contract test |
| State | `state.py` (legacy JSON, schema v5, transaction()), `paths.py` (AppPaths incl. database/legacy/migration paths), `db.py` (SQLite PRAGMA contract + migrations), `legacy_import.py` (one-time importer) |
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
PYTHONPATH=. .venv/bin/pytest -q        # full suite (editable install repaired)
python -m compileall -q bc250_llm_mode tests
```

The behavioral launcher test needs only bash ≥3.2 and python3 on PATH.

## Development conventions

Keep changes small and test-first where practical; extend fakes rather than
invoking system services; keep command construction inspectable (no shell
interpolation for user/model paths); preserve atomic state writes, rollback
behavior, and the README/ARCHITECTURE documentation contract. Cite master-plan
task IDs (e.g., R2.2) in commit messages.
