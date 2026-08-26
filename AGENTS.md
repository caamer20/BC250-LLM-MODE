# Continuation guide for BC250 LLM MODE

## Current state

**P3 IN PROGRESS — DEF-003 core closed (`f1c0e48`): CommandRunner is
bounded. All 28 production call sites (server/systemd, GUI, chat /llm,
maintenance) inherit watchdog-enforced timeouts (default 600 s), 8 MiB
output caps with truncation markers, and whole-process-group TERM→KILL;
signatures unchanged so no feature shifts. Real-child gates prove the
silent-hang stop, group-grandchild reaping, single truncation marker,
and env-secret canary absence. Inventory census stays green.**

P3 remaining: bounded HTTP transport for chat/hub_source/__main__
(§9.4 — drive chat's two pinned `timeout=None` calls to zero),
app-terminal-launcher policy note, §9.5 exit gates (half-open socket,
20-cycle stress, secret canaries across SQLite/events/bundles).

P2 landed:

- `gui/activity.py`: Activity Center reachable from a dashboard button
  (`_open_activity_center`, Toplevel + bounded polling that never blocks
  the GUI thread and never cancels work on close). The §8.2 presentation
  contract is PURE: `headline/message_copy/progress_text/severity_of/
  severity_rank/action_plan/support_text` — plain-language labels for
  every durable state, progress clamped to 99% until terminal
  verification, recovery-required rendered as prominent attention with
  "nothing deleted" safety copy, actions derived ONLY from
  OperationSummary flags, support text reusing view redaction.
- Widget layer is thin (Treeview + labels + action bar) over
  `operation_query`/`operation_commands` from composition; status strip
  shows working/paused/recovery counts and worker-lock ownership.
- Headless gates: the full state matrix (QUEUED/RUNNING/PAUSED/
  SUCCEEDED/FAILED_SAFE/RECOVERY_REQUIRED) rendered over REAL durable
  rows; action availability per state; routing through operation_commands
  verified by mutating durable state from a frame action; AST guard
  forbids sqlite/subprocess/repository/engine/worker imports in the
  module forever. Existing frozen Wizard surface untouched.

Verification: authoritative collection **722**; default suite green
across nine deterministic chunks: 721 passed + 1 Linux-gated skip = 722
reconciled; compileall + diff-check clean. (Slow gates unchanged from P1:
runtime 6/6, acquisition 41/41, clean-wheel 4/4.)

Next: **P3 — one bounded process port (`ProcessCommandSpec` v2) and a
bounded HTTP transport policy; migrate every production caller of raw
subprocess/HTTP; AST guards against regressions; secret canaries.**

---

Previous checkpoint: **P1 (Operation command/query API, U1.4)
COMPLETE on top of P0**

P1 landed:

- **Views** (`operations/views.py`): frozen `OperationSummary/Detail/
  StepView/EventView/LeaseView/WaitResult/Page/ActiveSummary` with
  schema version 1 serialization; absolute paths redacted to
  `file:<basename>` labels; closed event codes degrade to UNKNOWN.
- **Query** (`operations/query_service.py`): list/show/steps/events/
  leases/wait/active_summary; every method one READ unit; windowed SQL
  (no N+1); pagination bounds (page_size ≤ 200, events ≤ 500) refused
  with ValueError; stale leases reported expired while work stays
  recoverable; wait is bounded with an injectable condition waiter
  (production default: bounded-interval poller, never timeout=0).
- **Commands** (`operations/command_service.py`): cancel/resume/retry/
  recover/dismiss/detach, every mutation revision-fenced (CAS) and
  audit-evented. Retry creates a NEW operation from the immutable
  request with `parent_operation_id` lineage; recover takes over ONLY
  interrupted work whose every lease has expired behind `--confirm`
  and REFUSES RECOVERY_REQUIRED barriers with kind-specific guidance
  (exit 78 per plan §7.4); dismiss flips durable `dismissed_at`
  (migration 007) without touching history.
- **CLI** (`operations_cli.py`, wired in `__main__.py`): the full §7.4
  verb set; `--json` emits one schema-versioned document to stdout;
  human output compact with next-action lines; exit codes 0/1/2/78/130.
- **Generic detach (§7.5)**: `OperationCommandService.detach` hands a
  QUEUED operation to THE ONE worker entry point via the ONE spawn
  helper, now profile-bound (`--profile APP_DIR` so the child serves
  the same database) and marked detachable per kind. Exit gate: a real
  detached child completes a production MODEL_IMPORT exactly once
  THROUGH this API.
- **Migration 007**: `operations.dismissed_at` + default-view partial
  index; DATABASE_SCHEMA_VERSION now **7**.

Verification: authoritative collection **715**
(`pytest tests --collect-only -q`); default suite green across nine
deterministic alphabetical chunks: 714 passed + 1 Linux-gated skip =
715 reconciled; slow gates explicit: runtime stress **6/6**, acquisition
stress **41/41**, clean-wheel incl. runtime workflows AND worker-module
and CLI wheel gates **4/4**; compileall + `git diff --check` clean.

Next: **P2 Activity Center v1** — GUI over `operation_query` +
`operation_commands` only (no sqlite/subprocess imports), full
state/action matrix headless-tested, then P3 bounded execution platform.

---

Previous checkpoint: **P0 (foundation correction) COMPLETE**

P0 landed three boundaries:

- **P0.2** — the process-wide `faulthandler.dump_traceback_later(20,
  exit=True)` import-time watchdog in `tests/test_operation_worker.py`
  is DELETED. `tests/support_diagnostics.py` provides
  `ScopedTracebackDiagnostics` (dumps stacks for ONE block/wait without
  exiting, always cancels) and `wait_with_diagnostics` (bounded child
  wait → structured `(returncode, timed_out)`, kills the child's whole
  process group). Guards: AST scan over `tests/` forbids
  `exit=True`/`os._exit`; a child-interpreter probe proves importing the
  previously poisoned module arms nothing.
- **P0.1** — DEF-001 closed: `bc250_llm_mode/worker_main.py` is a REAL
  thin entry (`main(argv)->int`, argparse `--profile/--quiet-period/
  --lease-ttl` with bounded ranges, absolute-path + symlink refusal,
  missing-database → exit 4 with stable codes; 0 idle-exit / 2 usage /
  3 already-running / 4 repair / 5 run-failed / 130 interrupted).
  `worker_service.run_worker_main` now delegates (dead `json_safe`
  removed). Mandatory gates: a session-detached child completes a REAL
  production MODEL_IMPORT v1 of a tiny valid GGUF exactly once after
  parent handoff (artifact+alias exactly once, staging cleaned, boot
  policy untouched); no-work/paused/cancelled/poisoned/lock-conflict/
  malformed-policy cases covered; slow clean-wheel gate runs
  `python -m bc250_llm_mode.worker_main --help` and the repair path from
  an installed wheel with repo root off sys.path.
- **P0 findings fixed in production code**: (a) engine failure
  classification is now exception-safe — a step's classification probe
  that itself raises classifies that step UNCERTAIN so durable
  compensation still decides (previously the exception escaped
  `execute_one`, leaving operations RUNNING under live leases);
  regression tests added for both fail-safe and compensate branches;
  (b) `app.py _wire_services` bound `ThermalStateRepository` (latent
  NameError on the composed runtime thermal barrier), pinned by a new
  symtable composition-hygiene guard (`tests/test_composition_hygiene.py`)
  proving every referenced name in `app.py` resolves through some
  enclosing scope.
- **P0.3** — baseline reconciled (see Verification); CHANGELOG P0 section
  added; user-owned untracked files preserved untouched.

Verification (this sandbox still requires chunked execution):
authoritative collection **689** (`pytest tests --collect-only -q`);
default suite green across nine deterministic alphabetical chunks:
688 passed + 1 Linux-gated skip = 689 reconciled; slow gates explicit:
runtime stress/canaries **6/6**, acquisition stress **41/41**,
clean-wheel incl. runtime workflow execution **2/2** plus the NEW
worker-module clean-wheel gate (**3/3** in `-m slow tests/test_packaging.py
tests/test_worker_main_entry.py::test_installed_wheel_runs_worker_module_without_repository_root`);
compileall + `git diff --check` clean.

Next: **P1 Operation command/query API (U1.4)** — typed view models,
`OperationQueryService`, fenced `OperationCommandService`,
`bc250 operations …` CLI, generic detach contract; then P2 Activity
Center.

Previous checkpoint (U1.3): explicit worker lifecycle landed on top of
the closed Session 6B / U1.2 durable llama.cpp runtime lifecycle gate.

- One durable runtime path: CLI (`llamacpp update|rollback|resume|
  status`), wizard step 3, dashboard buttons, and initial setup all reach
  the composed `RuntimeLifecycleCommandService`
  (`runtime_lifecycle_command.py`), which enqueues through the shared
  `EnqueueService` and drives ONE operation via the shared engine factory
  alongside `MODEL_ACTIVATE v1`, `MODEL_ACQUIRE v1`, and `MODEL_IMPORT v1`.
- `RUNTIME_UPDATE v1` resolves the requested ref to a full immutable
  commit BEFORE any fetch/build mutation (moved refs refuse as
  `SOURCE_REF_MOVED`), builds an operation-owned candidate with bounded,
  cancellable typed-argv processes (no shell anywhere), freezes image +
  toolchain + recipe + per-binary sha256 into a canonical manifest, and
  derives a content build ID `llamacpp:sha256:<hex>` — tags are display
  metadata only.
- Active cutover is ONE no-gap atomic exchange via a fixed,
  digest-verified `renameat2(RENAME_EXCHANGE)` helper; unsupported
  filesystems fail safely before mutation. Initial installs publish with
  a no-replace rename instead.
- Success requires the seven-link identity chain: active manifest → live
  binary digest → handoff schema v2 binding → launcher start receipt →
  NEW systemd invocation marker → expected model/context/slots → bounded
  inference. Promotion is one generation-CAS database unit of work that
  also advances known-good identity.
- Any unproven state becomes `RECOVERY_REQUIRED`: both trees retained,
  both leases held as the barrier, remediation data persisted; cleanup
  never touches protected/uncertain paths.
- Rollback selects the repository's current rollback target, revalidates
  identities, and toggles lineage so an accidental rollback is itself
  reversible without rebuilding.
- Phase-scoped leases (ADR 002 §17): builds hold only
  `runtime-installation`; `runtime-active` joins at the activation
  boundary through promotion. Conflicts refuse/pause BEFORE any work.
- Handoff schema v2 + launcher start receipt (0600) bind configuration to
  the exact component; stale receipts and swapped binaries fail closed.
- Legacy routes DELETED with hard AST guards: `env.update_llamacpp`,
  `env.rollback_llamacpp`, `record_llamacpp_build`, `llamacpp_status`,
  mutable `llamacpp_history`, fixed `-staging/-backup/-rolled` paths,
  `ComponentLifecycleService.update/rollback`; setup cannot clone/build
  llama.cpp; frontends import no runtime infrastructure.
- Operations survive frontend closure: `llamacpp update --detach` hands
  the queued operation to ONE profile-scoped `WorkerHost`
  (`operations/worker_host.py`, spawned via `worker_service.py`) that
  resumes abandoned work exactly once, idles out after a bounded quiet
  period, pauses poisoned operations after bounded failures, and never
  changes reboot policy. Composition/boot/frontends never auto-start it
  (hard guards). Foreground remains the default; second Ctrl-C pauses
  durably with exit 130 and resume instructions.

Verification record for the U1.3 checkpoint (superseded by the P0 record
above; kept for count provenance): authoritative collection **662**;
chunked default execution green across eight deterministic alphabetical
chunks, 1 Linux-gated skip; slow gates explicit: runtime stress/canaries
**6/6**, acquisition stress **41/41**, clean-wheel incl. runtime workflow
execution **2/2**; compile/diff-check clean.

~~Next: U1.4 Operation command/query API~~ → now **P1** of
`FINAL_PRODUCTION_READINESS_IMPLEMENTATION_PLAN.md`.

The application is a `llama.cpp` Vulkan server behind a single systemd
service, with a resumable native tkinter wizard/dashboard and a terminal
chat client. The working tree is at **`0.9.0.dev0`** on reviewed commits
covering: 24-model catalog, chat/benchmark, thermal latch watchdog,
autotune, ordered atomic migrations to **schema v6** (runtime builds/
verifications/trees/component state), production hardening, the `gui/`
package, SQLite cutover with facade removed, R1/R2 exit gate, Session 5C
durable `MODEL_ACTIVATE v1`, Session 6A durable `MODEL_ACQUIRE/MODEL_IMPORT
v1`, and Session 6B durable `RUNTIME_UPDATE/RUNTIME_ROLLBACK v1`
(ADR 004).

## Where we are in the master plan

**Sequencing authority is now `FINAL_PRODUCTION_READINESS_IMPLEMENTATION_PLAN.md`
(U1.3 checkpoint → defensible 1.0.0). P0 foundation correction is DONE;
next boundary P1 Operation command/query API (U1.4), then P2 Activity
Center v1.** Historical context: `POST_R2_PRODUCTION_IMPLEMENTATION_PLAN.md`
drove Sessions 4–6B; the master plan remains requirements authority.
**Phase 0 (Session 4.1) is
DONE**: one SQLite connection policy (`db.open_database`) with test-proven
FK/query-only contracts and deterministic composition close; production
wiring repaired (host-mode imports, composed-activation single sequence,
rollback inference verification); launcher is handoff-only with strict
fail-closed validation; legacy canonicalization is pure (`legacy_schema.py`)
and the writable JSON store exists only as test support; duplicate
post-service commits removed with owners recorded; docs truth pass complete.
~~**Session 5A**~~ **DONE**; ~~**Session 5B**~~ **DONE**;
~~**Session 5C**~~ **DONE**; ~~**Phase U0**~~ **DONE**;
~~**Session 6A / U1.1** durable acquisition/import~~ **DONE**
(`SESSION_6A_DURABLE_MODEL_ACQUISITION_IMPLEMENTATION_PLAN.md` is the
completed authority); ~~**Session 6B / U1.2** durable llama.cpp runtime
lifecycle~~ **DONE** (`SESSION_6B_DURABLE_RUNTIME_LIFECYCLE_IMPLEMENTATION_PLAN.md`
is the completed authority; ADR 004 accepted; schema v6 with
worker locks).
`ULTIMATE_BC250_APPLIANCE_IMPLEMENTATION_PLAN.md` remains the sequencing
authority for U1+. ~~**U1.3 explicit worker lifecycle**~~ **DONE**.
Next: **U1.4 Operation command/query API**, then **U1.5 Activity Center
v1** toward the R3-complete exit gate (`0.9.0` tag candidate).

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
6. ~~**Session 6A / U1.1**: durable model acquisition & import~~ **DONE**.
7. ~~**Session 6B / U1.2**: durable llama.cpp runtime lifecycle~~ **DONE**
   (ADR 004 `docs/adr/004-immutable-runtime-lifecycle.md`; migrations 005/006
   add `runtime_builds`/`runtime_build_verifications`/`runtime_trees`/
   `runtime_component_state`; `operations/runtime_lifecycle.py` pure
   workflows; `runtime_lifecycle_adapter.py` ONE production host;
   `runtime_lifecycle_command.py` composed command; `runtime_process.py`
   bounded typed-argv execution; `runtime_exchange_helper.py` fixed
   digest-checked renameat2 exchange; handoff schema v2 + start receipt;
   phase-scoped leases per ADR 002 §17; mandatory exchange-death test
   green in both fake world and crash matrix; legacy routes deleted with
   hard guards).
8. **U1.3: explicit worker lifecycle** **DONE**
   (`operations/worker_host.py`, `worker_service.py`, migration 006
   `worker_locks`). Mandatory abandoned-frontend resume test proves ONE
   supervised worker finishes an operation exactly once without touching
   reboot policy; single-instance via heartbeated profile lock; idle exit
   on injected clocks; bounded restart policy pauses poisoned operations;
   condition-backed bounded waiting; graceful shutdown checkpoints;
   `llamacpp update --detach` spawns exactly one typed helper process.
   Composition/boot/frontends never auto-start workers (hard guards).
   Foreground remains the default path.

## Layout highlights

| Area | Files |
| --- | --- |
| GUI package | `gui/app.py`, `gui/steps.py`, `gui/dashboard.py`, `gui/forms.py`; `Wizard`/`run_gui` composed in `gui/__init__.py`; surface frozen by headless contract test |
| State | `state.py` (legacy JSON defaults only), `repositories.py` + `runtime_builds.py` (typed SQL access), `paths.py` (AppPaths incl. database/migration paths), `db.py` (SQLite PRAGMA contract + ordered migrations to v6 incl. worker locks), `legacy_import.py` (one-time importer) |
| Safety runtime | `thermals.py` (hysteresis/latch/baseline/reset_latch), `optimize.py` (`apply_gpu_clock_limit`, `restore_gpu_profile`) |
| Durable activation (5C) | `operations/activation.py` (request v1 + evidence + typed port + eight steps), `activation_adapter.py` (one production host), `activation_command.py` (foreground enqueue/execute/terminal), `model_artifact.py` (bounded GGUF/digest identity); `runtime_handoff.py` strict `observe()`; `server.py` `service_observation` |
| Runtime lifecycle (6B) | `operations/runtime_lifecycle.py` (requests/evidence/port/steps), `runtime_lifecycle_adapter.py` (ONE production host), `runtime_lifecycle_command.py` (composed command/status), `runtime_builds.py` (immutable identities + repositories), `runtime_process.py` (bounded typed-argv runner), `runtime_exchange_helper.py` (digest-pinned RENAME_EXCHANGE); `env.py` is provisioning-only |
| Durable ops engine | `operations/engine.py` (fenced executor with phase-scoped leases), `operations/workflow.py` (registry/enqueue), `operations/repositories.py` (leases incl. `acquire_many`) |
| Composition | `app.py` (`Application.compose`; ONE frozen registry + enqueue + engine factory serve activation/acquisition/import/runtime update/runtime rollback via five command services) |

## Invariants (do not break)

- One service owner: only `server.py` touches `bc250-llm.service`.
- Fit gate: model/context/slot changes pass `calculate_fit`; NO-FIT never runs.
- Reboot safety: next boot is always the desktop; nothing auto-starts.
- Reversibility: host tuning records prior state; uninstall reverts it.
- Secrets never appear in argv or logs (HF token rides a 0600 env-file).
- Runtime updates never touch the active checkout until a smoke-checked,
  identity-bound candidate is atomically exchanged (RENAME_EXCHANGE);
  promotion happens only after the seven-link live verification chain,
  and any unproven state becomes RECOVERY_REQUIRED retaining every tree.
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
python3 -m compileall -q bc250_llm_mode tests
# Session verification battery additionally runs the slow gates explicitly:
.venv/bin/pytest -m slow tests/test_runtime_security_stress.py   # U1.2 canaries+stress
.venv/bin/pytest -m slow tests/test_acquisition_security_stress.py
.venv/bin/pytest -m slow tests/test_packaging.py   # clean-wheel incl. runtime v1 execution
```

On constrained sandboxes that kill long CPU-bound processes (~20 s), run
the same suite as deterministic alphabetical chunks and reconcile their
pass counts against `--collect-only` (see Current state). The behavioral
launcher tests need only bash ≥3.2 and python3 on PATH.

### Test-count reconciliation record (Session 4.1 §3.1)

- Handoff at `7672e7d` claimed **313**; the audited checkout collected **301**
  — the earlier figure was stale because Session 4C deleted the facade-only
  cutover tests without updating the handoff.
- Session 4.1 added connection-contract, production-wiring, canonicalizer,
  and launcher fail-closed tests; the reconciled baseline is now **330**,
  printed automatically by `tests/conftest.py` in every run's summary.
- Source (`PYTHONPATH=.`) and editable-install invocation collect identically.
- Session 6B closeout (+follow-through, +U1.3): collection is **662**
  (default executed green + slow-marked gates). This sandbox's ~20 s CPU
  kill prevents single-shot full runs; evidence comes from eight
  alphabetical chunk runs plus explicit slow-gate runs (runtime 6/6,
  acquisition 41/41, packaging 2/2). Never quote a count without naming
  how it was produced.

## Development conventions

Keep changes small and test-first where practical; extend fakes rather than
invoking system services; keep command construction inspectable (no shell
interpolation for user/model paths); preserve atomic state writes, rollback
behavior, and the README/ARCHITECTURE documentation contract. Cite master-plan
task IDs (e.g., R2.2) in commit messages.
