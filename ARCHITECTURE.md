# Architecture

BC250 LLM MODE turns an AMD BC-250 running Bazzite into a dedicated local
`llama.cpp`/Vulkan inference station, then operates it. This document maps the
module layout and the invariants that keep the hardware safe.

## Module map

| Module | Responsibility |
| --- | --- |
| `constants.py` | Paths, VRAM budgets, the shipped `KNOWN_GOOD_LLAMACPP` pin, tag validation |
| `hardware.py` / `memory_profile.py` | DRM/GPU discovery, host RAM, BIOS UMA-split inference; card numbers are never cached |
| `state.py` | Atomic (`0600`) JSON state with tested schema migrations (currently v5); every key is declared |
| `disclaimer.py` | The mandatory safety gate; privileged or destructive paths call `require_acknowledgment` |
| `llmmode.py` / `desktop.py` | Reboot safety: LLM mode is per-boot only; the next boot always returns to the desktop |
| `catalog.py` | Curated model metadata, forbidden-artifact rejection, VRAM fit math (`calculate_fit`), `best_quant`, `search_catalog`, `recommend_models` |
| `local_models.py` | Bounded GGUF discovery and catalog matching for files already on disk |
| `download.py` / `prepare.py` | Space-checked resumable downloads (SHA-256 manifest support), guarded GGUF verification/repair, local safetensors→GGUF conversion |
| `env.py` | Containerized llama.cpp/Vulkan build, plus the pinned update flow (`llamacpp status/update/rollback`) with staging-dir atomic swap |
| `server.py` | Launcher + `bc250-llm.service` generation, health checks, self-healing `ensure_server`, log diagnosis |
| `model_manager.py` | Transactional model/context/slot activation with automatic rollback |
| `optimize.py` | Bounded, reversible host/runtime tuning; governor profiles; `apply_gpu_clock_limit` for the watchdog |
| `thermals.py` | Thermal watchdog: pure hysteresis state machine + thin host side effects (clock cap, service stop) |
| `tune.py` | `autotune`: fit-checked benchmark sweep with per-combo rollback |
| `chat.py` | Streaming terminal chat (prompt caching, timings, think-filter, trim/export/persistence), benchmark helpers |
| `gui/` | tkinter package: `app.py` (shell/threading/navigation), `steps.py` (wizard screens), `dashboard.py` (operations dashboard + catalog browser + llama.cpp card), `forms.py` (forms with pure, unit-tested helpers) |
| `sharing.py` / `tailscale.py` / `openwebui.py` | Optional tailnet HTTPS sharing stack |

## Invariants

1. **One owner per concern.** `server.py` owns the launcher and service; GUI
   and chat code never start processes directly.
2. **Fit math is the gate.** Model, context, and slot changes must pass
   `calculate_fit`; `NO-FIT` is never overridden at runtime.
3. **Transactional activation.** Every change that restarts the server saves a
   candidate, health-checks it, and restores the previous configuration on
   failure (`restart_with_rollback`).
4. **Staged builds, atomic swaps.** llama.cpp updates build in a separate
   staging clone (`llama.cpp-staging`) without touching the active checkout,
   then swap source+binaries as one unit (`llama.cpp ↔ llama.cpp-backup`) and
   revert on unhealthy restart. Recorded history never claims more rollback
   targets than physically exist on disk.
5. **Thermal safety is latching and restorative.** The watchdog saves the
   user's GPU profile before its first throttle, restores that exact profile
   on recovery, and a thermal stop latches until an explicit, safe-temperature
   `thermals reset` — repeated polls never re-stop the service.
5. **Reboot safety.** The current boot may run LLM mode; the next boot is
   always the normal desktop with no auto-start.
6. **Reversibility.** Host optimizations record their previous state and are
   reverted by `revert-optimizations` and uninstall.
7. **No secrets in argv or state.** Credentials stay in the environment; state
   files are mode `0600`.

## Test strategy

- Pure logic (catalog fit math, thermal hysteresis, form decisions, chat
  helpers) is unit-tested without I/O.
- Host interactions run through `CommandRunner` with fake runners asserting
  exact command sequences and rollback ordering.
- The GUI has a headless contract test (`tests/test_gui_contract.py`): tkinter
  is stubbed so the real `Wizard` constructs without a display, and the frozen
  method surface must survive refactors.
- Schema migrations are tested from real legacy JSON shapes.

## Update policy

llama.cpp follows a **pinned known-good channel**: the pin ships with each
application release, drift is reported by `doctor` and `llamacpp status`, and
updates are explicit user actions with automatic rollback. The model catalog
follows the same philosophy — new entries are compatibility candidates until
they pass an on-card Vulkan load test.
