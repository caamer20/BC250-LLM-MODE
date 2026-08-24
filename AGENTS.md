# Continuation guide for BC250 LLM MODE

## Current state

**SESSION 6A IN PROGRESS — STOPPED MID-PLAN (Commits 1–5a of 8).** Do NOT
assume the U1.1 exit gate is closed. Landed so far, all green on a 528-test
default suite:

- Commit 1 (`ac2a675`): ADR 003 + ADR 002 §16 amendment + red policy tests
  (`tests/test_session6a_policy.py`).
- Commit 2 (`54c0ab9`): migration 004 (`model_artifacts`, linked
  `model_installations.artifact_id` with deterministic `legacy:<id>`
  backfill, `operation_storage_reservations`), SCHEMA_VERSION=4,
  `ModelArtifactRepository`, `ModelInstallationsRepository.install_alias`,
  `StorageReservationRepository` + `tests/test_migration_004.py`.
- Commit 3+4 (`e9c367e`): engine §8.1 typed `TerminalDecision`
  (closed to SUCCEEDED/FAILED_SAFE; activation outcome parity kept),
  §8.2 `CancellationObserved` at cancellation-safe pulses (heartbeat
  unconditional), §8.3 ProgressPolicy-throttled pulse writes;
  `operations/acquisition.py` (requests/evidence/port/eight-step
  MODEL_ACQUIRE v1 + MODEL_IMPORT v1 workflows, closed terminal resolver)
  + fake world (`tests/operations/acquisition_world.py`) +
  `tests/test_operation_acquisition.py` including the mandatory
  publication-death/pre-checkpoint red test (transfer/conversion/
  publication/registration each exactly one), quarantine terminal,
  duplicate reuse, safe-chunk cancellation, partial resume.
- Commit 5a (`a705cc4`): `artifact_storage.py` (bounded streaming hash,
  no-replace O_EXCL publication with parent fsync, receipts, quarantine
  move, containment check) + `AppPaths.model_staging_dir` /
  `model_quarantine_dir` / `model_artifacts_dir` +
  `tests/test_artifact_storage.py`.

REMAINING for Session 6A: production `AcquisitionHostAdapter` +
`hub_source.py` bounded HTTP/range transfer + acquisition-only process
adapter (Commit 6); `acquisition_command.py`, composition wiring, and the
CLI/GUI cutover deleting `download_model`/`prepare_model`/
`prepare_local_model`/`ModelInstallationService.download_and_prepare` plus
architecture guards (Commit 7); crash/adverse/stress matrices, security
canaries, slow clean-wheel extension, README/ARCHITECTURE/CHANGELOG truth
(Commit 8). The old synchronous download/prepare path still exists and is
still the only production route until Commit 7 lands.

Python 3.11+ project for an AMD BC-250 running Bazzite: configures a local
`llama.cpp` Vulkan server behind a single systemd service, with a resumable
native tkinter wizard/dashboard and a terminal chat client. The working tree
is at **`0.9.0.dev0`** with a **clean tree, 505-test green baseline**
(504 default + 1 slow-marked clean-wheel gate), and
reviewed commits above `v0.7.0` (the pre-SQLite tree is tagged
`v0.8.0-pre-sqlite` at `2126d61`) covering: 24-model catalog with
tiers/recommendations, chat + benchmark features, thermal latch/baseline
watchdog, autotune, llama.cpp staged update/rollback, schema v5 + ordered
atomic migrations, production hardening, the `gui/` package, the **SQLite
cutover with the compatibility facade removed** (repositories, unit of work,
query layer, typed services, repair gate, durable publication, runtime
handoff renderer/service), the closed R1/R2 exit gate, and **Session 5C:
durable MODEL_ACTIVATE v1** (one production activation path; the synchronous
orchestrator and every legacy fallback are deleted).

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
Next: ~~**Session 5A**~~ **DONE**; ~~**Session 5B**~~ **DONE**;
~~**Session 5C**~~ **DONE**; ~~**Phase U0** (appliance-plan closeout:
identity/GGUF hardening tests, package topology + clean-wheel gate,
loopback-only Open WebUI)~~ **DONE**, then **Session 6A — convert model
acquisition/import to durable operations** (first red test: crash after
final artifact publication but before checkpoint; the next executor must
recognize the exact content digest without downloading, copying, or
publishing twice). `ULTIMATE_BC250_APPLIANCE_IMPLEMENTATION_PLAN.md` is now
the sequencing authority for U1+; `SESSION_5C_DURABLE_ACTIVATION_IMPLEMENTATION_PLAN.md`
is the completed R3.3 authority; `SESSION_5B_EXECUTOR_IMPLEMENTATION_PLAN.md`
remains the completed R3.2 authority and evidence handoff.

1. ~~Sessions 1–4: sweeps, facade removal, R1/R2 exit gate~~ **DONE**.
2. ~~Session 4.1: post-R2 production wiring stabilization~~ **DONE**.
3. ~~Session 5A: ADR 002 + migration 003 + operation state machine +
   repositories~~ **DONE** (`docs/adr/002-durable-operations.md`;
   `bc250_llm_mode/operations/` model/validation/repositories; schema v3
   with operations/operation_steps/operation_events/operation_leases;
   FAILED_SAFE terminal added per ADR 002; CAS transitions against state +
   revision; leases with owner+revision ownership and expired takeover;
   secret/bounds validation before persistence). **No executor, worker,
   host adapter, CLI command, or Activity UI exists yet** — that is 5B+.
4. ~~Session 5B: executor, leases, cancellation, recovery~~ **DONE**
   (`operations/workflow.py` typed registry + EnqueueService;
   `engine.py` fenced intent/effect/probe/checkpoint/verify protocol with
   deterministic `execute_one`; `recovery.py` closed classification
   vocabulary; `worker.py` bounded claim/run/shutdown loop — never
   auto-started by composition; lease `assert_owned` fencing and
   `list_expired`; durable cancel timestamps; RECOVERY_REQUIRED acquisition
   barrier; death-after-effect-before-checkpoint test proves effect count
   stays exactly 1 across takeover; full named crash-point matrix;
   20/20 focused stress iterations, no sleeps). **Still no real host
   adapter, CLI operation command, or Activity UI** — 5C/6C.
5. ~~**Session 5C**: durable model activation~~ **DONE**
   (`02b7e72` plan freeze → `dbacbdd` entry corrections → `b87e87f`
   workflow → `3ff497f` adapter → `ee8a5fe` cutover → Commit 6 evidence).
   Entry corrections landed first (ADR 002 §15: `COMMITTING → VERIFYING`
   cycle + durable compensation resume; intent reuse; per-step versions;
   fenced pulse). Production shape: `operations/activation.py` (request v1,
   evidence, typed port, eight steps), `activation_adapter.py` (ONE
   production host), `activation_command.py` (foreground enqueue/execute/
   terminal mapping; resumes interrupted activations, RECOVERY_REQUIRED
   barrier), strict handoff observation, bounded GGUF identity
   (`model_artifact.py`). Frontends (`__main__`, chat, GUI, setup) all
   reach `switch_model`/`change_context`/`change_parallel_slots` → the one
   command; `_apply_legacy_or_raise`, `restart_with_rollback`, and the
   synchronous orchestrator are deleted with AST guards. Mandatory
   handoff-death test passes BOTH branches (candidate-complete succeeds
   without a second restart; prior-still-active rolls back with exactly
   one restoration); 18-case crash matrix converges under takeover;
   20/20 focused stress iterations, no sleeps. **No operations CLI,
   Activity UI, detach, background worker, or auto-start** — Session 6C.
   Then **Session 6A** (acquisition), runtime update, R4 typed
   adapters/timeouts, and the later phases of the post-R2 plan.

## Layout highlights

| Area | Files |
| --- | --- |
| GUI package | `gui/app.py`, `gui/steps.py`, `gui/dashboard.py`, `gui/forms.py`; `Wizard`/`run_gui` composed in `gui/__init__.py`; surface frozen by headless contract test |
| State | `state.py` (legacy JSON, schema v5, transaction()), `compat_state.py` (SQLite compatibility facade), `repositories.py` (typed SQL access), `paths.py` (AppPaths incl. database/legacy/migration paths), `db.py` (SQLite PRAGMA contract + migrations), `legacy_import.py` (one-time importer) |
| Safety runtime | `thermals.py` (hysteresis/latch/baseline/reset_latch), `optimize.py` (`apply_gpu_clock_limit`, `restore_gpu_profile`) |
| Durable activation (5C) | `operations/activation.py` (request v1 + evidence + typed port + eight steps), `activation_adapter.py` (one production host), `activation_command.py` (foreground enqueue/execute/terminal), `model_artifact.py` (bounded GGUF/digest identity); `runtime_handoff.py` strict `observe()`; `server.py` `service_observation` |
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
PYTHONPATH=. .venv/bin/pytest -q        # default suite (slow-marked gates
                                        # excluded); the terminal summary
                                        # prints the authoritative collected
                                        # count (never infer from dots)
.venv/bin/pytest tests --collect-only -q
python -m compileall -q bc250_llm_mode tests
# Session verification battery additionally runs the slow gates explicitly:
.venv/bin/pytest -q -m slow tests/test_packaging.py   # U0.5 clean-wheel smoke
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
