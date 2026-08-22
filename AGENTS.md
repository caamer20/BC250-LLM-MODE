# Continuation guide for BC250 LLM MODE

## Current state

Python 3.11+ project for an AMD BC-250 running Bazzite: configures a local
`llama.cpp` Vulkan server behind a single systemd service, with a resumable
native tkinter wizard/dashboard and a terminal chat client. `main` sits at
`v0.7.0` (`46bedc6`) with a large reviewed feature pass on top: 24-model
catalog with fit/recommendation logic, chat + benchmark features, thermal
watchdog and autotune, llama.cpp pin/update/rollback lifecycle, schema v5,
production hardening (secret-free downloads, rotating logs), and a split
`bc250_llm_mode/gui/` package whose public surface is frozen by a headless
contract test.

## Layout highlights

| Area | Files |
| --- | --- |
| GUI package | `gui/app.py` (shell/threading/nav), `gui/steps.py`, `gui/dashboard.py`, `gui/forms.py` (pure helpers `fit_message`, `optimization_settings_from_values`); `Wizard` composed in `gui/__init__.py` alongside `run_gui` |
| Safety runtime | `thermals.py` (hysteresis + latching stop + profile baseline; `reset_latch`), `optimize.py` (`apply_gpu_clock_limit`, `restore_gpu_profile`) |
| llama.cpp lifecycle | `env.py` (`llamacpp_status/update/rollback`; staged source clone in `llama.cpp-staging`, atomic swap with `llama.cpp-backup`, history capped to what exists on disk) |
| Server | `server.py` launcher (portable CFG loop — no `readarray`; threads/cache-reuse/defrag flags), memory guards, self-healing `ensure_server` |
| State | `state.py` schema v5 with declared telemetry/build keys and tested migrations |
| Tests | `tests/test_gui_contract.py` (headless Wizard construction via `_gui_stubs.py`), `test_phase0*.py` (behavioral launcher exec, llm CLI branch, run_gui, dashboard deferred imports), `test_round4*.py`, `test_round5*.py`, `test_production.py` (token/env-file safety, version sync, interrupt exit code) |

## Invariants (do not break)

- One service owner: only `server.py` touches `bc250-llm.service`.
- Fit gate: model/context/slot changes pass `calculate_fit`; NO-FIT never runs.
- Reboot safety: next boot is always the desktop; nothing auto-starts.
- Reversibility: host tuning records prior state; uninstall reverts it.
- Secrets never appear in argv or logs (HF token rides a 0600 env-file).
- llama.cpp updates leave the active checkout untouched until a staged build
  passes smoke checks; failed health restarts restore the previous tree.
- Thermal stops latch until an explicit safe-temperature `thermals reset`.

## Known deferred work (agreed plan, not defects)

State transactions/operation journal (Phase 1 full scope), watchdog systemd
unit, versioned llama.cpp build dirs beyond one backup, CLI handler refactor,
CI workflow, and on-hardware validation of compatibility-candidate catalog
entries and the `KNOWN_GOOD_LLAMACPP` pin value.

## Verification

```bash
PYTHONPATH=. .venv/bin/pytest -q        # full suite
python -m compileall -q bc250_llm_mode tests
```

A plain `.venv/bin/pytest` can resolve a stale installed copy; prefer
`PYTHONPATH=.` or refresh with `.venv/bin/pip install -e '.[test]'`. The
behavioral launcher test needs only bash ≥3.2 and python3 on PATH.

## Development conventions

Keep changes small and test-first where practical; extend fakes rather than
invoking system services; keep command construction inspectable (no shell
interpolation for user/model paths); preserve atomic state writes, rollback
behavior, and the README/ARCHITECTURE documentation contract.
