# Changelog

All notable changes to BC250 LLM MODE. Format follows Keep a Changelog;
versions are tagged in git.

## [0.9.0.dev0] — unreleased development line

### Added (Session 6A / U1.1: durable model acquisition & import)

- Durable `MODEL_ACQUIRE v1` / `MODEL_IMPORT v1`: immutable hub revision
  pinning, bounded range-resumable transfer with credential stripping on
  cross-origin redirects, descriptor-stable local import that never
  modifies the source, no-replace content-addressed publication,
  quarantine for invalid candidates (no alias), digest deduplication,
  lease-fenced logical reservations, forward-only recovery, and
  cancellation that retains a labeled resumable partial.
- One composed `ModelAcquisitionCommandService`; install-and-use surfaces
  run acquisition then the existing durable activation as separate
  operations.

### Removed

- The synchronous download/prepare route: `download_model`,
  `prepare_model`, `prepare_local_model`, conversion cleanup helpers from
  production, and `ModelInstallationService`. Frontends may not import
  download/prepare/hub/storage/repository modules (AST guards).

### Changed (Road to 1.0 — Phase A: Session 3 frontend sweep)

- **Frontends can no longer persist whole-state dictionaries.** All ten
  `__main__` saves, ten gui/steps saves, seven dashboard saves, three
  gui/app saves, one forms save, and four chat saves are gone — routed
  through SetupService / HostModeService / ComponentLifecycleService /
  OpenWebUIService / SharingService / ModelInstallationService /
  MaintenanceService / ModelActivationService, or the application's narrow
  diff-persistence primitive. The exact-count guard is now zero across the
  entire frontend surface.
- **Repository-native query layer**: `ApplicationQueryService.snapshot()`
  assembles state from repositories + AppPaths (never wrapping the facade),
  exposing disposable drafts that carry their source revision. Status
  refreshes are pure queries and never bump revisions.
- **Composition expansion**: `Application.compose()` now wires paths,
  logger, unit-of-work factory, query service, setup/safety/runtime/
  activation/host-mode/component/OpenWebUI/sharing/model-install/maintenance
  services, and a typed systemd runtime controller. `run_gui(application)`
  / `run_chat(application)` receive the composition; constructor fallback
  stores (`StateStore()`) are removed from GUI/chat paths.
- **Safety**: `thermals --force-reset` removed from the normal CLI — a
  missing sensor now denies latch reset, and no normal flag can bypass the
  safe-temperature requirement.
- **Architecture guards** enforce: zero `.save(` outside persistence
  implementations; transitional transaction allowlist only; no
  `StateStore(` in frontends (the `--state` legacy branch excepted);
  no `Path.home()` outside paths.py; GUI modules import neither subprocess
  nor sqlite nor repositories nor privilege helpers; runtime-handoff path
  literal confined to its service; status refresh never persists.

### Changed (Road to 1.0 — Phase A: A3–A5)

- **Per-command unit of work**: services no longer share the facade
  connection; `UnitOfWorkFactory.begin()` gives every command its own
  connection with `BEGIN IMMEDIATE`, commit-on-success, rollback-on-error.
  Histories became append-only (the facade can no longer clobber narrowly
  appended records), proven by a four-worker concurrency test.
- **Named setup workflow** (A3): `SetupService` owns canonical stages
  (WELCOME…COMPLETE) with expected-stage transitions — stale/skipped
  transitions raise `SetupConflict`, repeats are idempotent, evidence is
  recorded, and repair never rewinds the safety acknowledgement. Bootstrap
  persistence now goes through the service (3 saves removed).
- **Typed runtime preview/apply** (A5): migration 002 adds the
  `known_good_runtime` row; `RuntimeConfigurationService.preview()` is a
  pure projection sharing apply's exact validation; `apply()` commits in
  one unit with revision checks and publishes the handoff from the
  committed revision. Autotune trials/winners route through it.
- **Model activation service** (A4): thermal-latch gate, fit/artifact
  policy, candidate commit → handoff → restart → health → bounded
  inference probe → known-good promotion, with verified rollback and
  durable `RECOVERY_REQUIRED` when rollback itself fails. All
  `model_manager` mutations route through it (3 saves removed).
- Whole-state-save guard exact counts: **model_manager 3→0, tune 2→0**
  (plus bootstrap 3→0 earlier in the phase). Remaining inventory is
  frontend-only: `__main__` 10, gui/steps 10, dashboard 7, gui/app 3,
  chat 4, forms 1.

### Changed (Road to 1.0 — Phase A progress)

- **Thermal latch is service-persisted** (A1): `ThermalStateService` is the
  sole writer of the safety-authoritative thermal state; whole-state saves
  can no longer clear or downgrade a latched stop; stop intent is durable
  *before* the server stops; a missing sensor refuses latch reset; failed
  GPU-profile restoration keeps durable recovery evidence.
- **Narrow history appends** (A2): benchmark and autotune records go through
  capped repository appends with transactional retention — no prompts or
  generated content are stored (canary-tested).
- Whole-state-save guard moved to **exact expected counts**: thermals 2→0,
  chat 5→4, tune 3→2.

### Fixed (R2 hardening)

- **Failed legacy import no longer publishes an empty database**: compose
  returns an explicit repair-required application; only `repair-status`
  and `repair-retry` are permitted; every other command exits 78 until
  migration succeeds.
- **Stale drafts can no longer overwrite newer state**: whole-state saves
  validate against the revision carried by the saved mapping, not a
  store-level cache.
- **Schema migrations are atomic**: statement-by-statement execution inside
  an explicit transaction (no `executescript`); a mid-migration failure
  leaves neither partial tables nor a recorded version.
- **Durability is real**: all durable artifacts publish through fsynced
  six-step atomic writes; new databases are 0600 from first connect;
  app-owned sensitive directories are enforced 0700.

### Changed

- Compatibility `transaction()` now matches the legacy contract exactly
  (replacement mappings persisted, `None` cancels, other types rejected).
- Shared SQLite connections are serialized by a process-local reentrant
  lock; cross-process writers remain flock-serialized.
- `runtime-handoff.json` is rendered by a dedicated renderer/service —
  only after committed runtime/model/profile changes, carrying
  `config_revision` and model identity, regenerated when missing or stale
  at daemon start, with publication failures reported separately from
  database commits.
- Two guard tests now drive transitional persistence toward zero:
  direct `StateStore(` construction sites and per-file whole-state
  save/transaction counts.

## [Unreleased] — R2.2 cutover

### Added

- **SQLite is the source of truth** (ADR 001 cutover): the composition root
  opens/initializes `state.db`, auto-imports a legacy `state.json` once on
  first run, and serves every surface through the compatibility facade
  (`compat_state.CompatStateStore` — same `load/save/transaction` contract).
  JSON remains a read-only backup; explicit `--state <json>` opts into
  transitional legacy mode.
- Typed repositories (`repositories.py`) over all migration-001 tables; raw
  SQL no longer appears outside them.
- Runtime handoff artifact: every committed save renders
  `<app_dir>/runtime-handoff.json` (0600); launcher v2 execs argv built from
  it (legacy `state.json` fallback retained for pre-cutover installs).
- Optimistic revision checks on whole-state saves (`StaleStateError`);
  transactions remain flock-serialized and lost-update safe across threads
  and processes (`check_same_thread=False` + busy timeout).
- Cutover guard test freezing direct `StateStore(` construction in
  production at its four transitional call sites.

### Changed

- Generated launchers no longer use positional `CFG[…]` arrays; both handoff
  and legacy paths emit one argument per line into a single `exec`.
- `--state <file>` semantics narrowed to transitional legacy mode (no
  database is created or imported in that invocation).

## [0.8.0.dev0] — unreleased development line

The 0.8 line targets the production-readiness plan: stabilized beta
(0.8), transactional core and safety supervisor (0.9), secure lifecycle
(0.10), operations/DR UX (0.11), and the 1.0 stable gate.

### Added

- State schema **v5**: declared telemetry keys (`bench_history`,
  `autotune_history`, `thermal_watchdog_state`) and llama.cpp build
  provenance (`llamacpp_build`, `llamacpp_history`); tested migration from
  v4; monotonic `revision` counter.
- `StateStore.transaction()`: advisory-file-locked read-modify-write with
  revision increments, preventing lost updates between GUI, CLI, watchdog,
  and benchmark writers. Benchmark history recording uses it.
- `paths.AppPaths`: explicit application path profile with test isolation
  (`AppPaths.temporary`), symlink rejection for app-owned directories, and
  no import-time `Path.home()` evaluation.
- llama.cpp pinned lifecycle: shipped known-good tag pin, staged source
  clone builds (active checkout untouched until smoke tests pass), atomic
  source+build swap with health-checked automatic rollback,
  `llamacpp status|update|rollback`.
- Thermal watchdog hardening: preserved GPU-profile baseline across
  throttle/recover cycles, idempotent latched stop, prominent degraded-sensor
  status, explicit safe-temperature `thermals reset [--force-reset]`.
- Self-healing `llm ensure`; live tokens/second in chat; `/bench` with
  repeat aggregation; `/save`, `/load`, `/export`, `/system`, `/temp`,
  `/retry`, `/recommend`; prompt caching; conversation persistence.
- Catalog expanded to 24 models with fit-aware search/recommendation and
  release-tier metadata (`supported` / `preview`).
- Production hardening: Hugging Face token delivered via private 0600
  env-file (never argv/logs), rotating setup logs, Open WebUI pinned image +
  hardened container flags, systemd memory guards under safeguards,
  `--version`, clean Ctrl-C (exit 130), behavioral launcher argv test,
  headless GUI contract test, dashboard deferred-import coverage.

### Changed

- GUI split into the `bc250_llm_mode/gui/` package (app/steps/dashboard/
  forms) with pure form helpers unit-tested without tkinter.
- `llm status` is read-only (no acknowledgment required); mutating llm
  actions print JSON envelopes and persist through the store.
- Setup log rotation: 5 MB × 3 backups.

### Security

- Hugging Face credentials can no longer leak into `/proc/<pid>/cmdline`
  or `setup.log`.
- Open WebUI container runs with `no-new-privileges`, dropped capabilities,
  bounded memory (2g) and PID limit (256), on an immutable version-pinned
  image reference instead of `:main`.

## [0.7.0] — public beta

Initial public beta: resumable setup wizard, single systemd-owned model
server, 13-model catalog, fit-gated activation with rollback, desktop/LLM
boot safety, reversible host optimizations, optional Open WebUI and tailnet
HTTPS sharing, terminal chat.
