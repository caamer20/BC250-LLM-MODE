# Changelog

All notable changes to BC250 LLM MODE. Format follows Keep a Changelog;
versions are tagged in git.

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
