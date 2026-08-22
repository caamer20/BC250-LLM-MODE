# BC250 LLM MODE — Master Production Implementation Plan

**Document purpose:** Turn the existing architecture and production-readiness strategy into an ordered, testable implementation backlog that another engineer or coding agent can execute without having to reconstruct priorities or hidden dependencies.

**Planning baseline:** `main` at `v0.7.0` (`46bedc6`) plus the intentionally uncommitted `0.8.0.dev0` feature and hardening work currently in the working tree.

**Target:** A production-ready `1.0.0` release for the supported AMD BC-250 / Bazzite appliance profile, with safe recovery, authenticated remote access, transactional state changes, reproducible dependencies, signed releases, useful diagnostics, and hardware qualification evidence.

**Related documents:**

- `AGENTS.md` is the continuation and safety guide.
- `ARCHITECTURE.md` describes the current system and its invariants.
- `IMPLEMENTATION_PLAN.md` describes the original feature roadmap.
- `PRODUCTION_READINESS_PLAN.md` defines the target production architecture and launch gates.
- This file is the execution authority when the documents differ on ordering or current status. Update it as work lands.

---

## 1. Executive assessment

The project has a broad and useful feature set, but it is not yet production-ready. The current development pass has substantially improved model discovery, chat, benchmarking, tuning, thermal handling, CI scaffolding, GUI structure, and runtime update staging. Those improvements should be preserved. The remaining work is less about adding isolated commands and more about making every state-changing workflow reliable under interruption, safe under concurrent use, secure when exposed remotely, diagnosable by a non-developer, and reproducible across installations.

The most important gap is architectural: JSON state, generated shell scripts, direct `pkexec`/`sudo` command execution, and UI-owned background threads are still carrying responsibilities that now exceed their safe operating envelope. New features should pause until the state transaction layer, operation engine, path model, safety supervisor, and privilege boundary exist.

### Current verification result

The current source tree collects **198 tests**. The most recent source-tree run produced **197 passing tests and one failure**:

- `tests/test_phase0_cli.py::test_dashboard_refresh_executes_thermal_import`
- The test creates an isolated `StateStore`, but the GUI runner still resolves the default log directory under the real user application directory. In the sandbox this becomes a permission error.
- This is evidence that path dependency injection is incomplete. It must be fixed in production code and the fixture, not hidden with broader filesystem permissions.

The authoritative local command remains:

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

A plain `.venv/bin/pytest -q` can import the stale installed package and is not an acceptable verification command until the editable environment has been repaired.

### Production blockers at a glance

| Area | Current condition | Required production condition | Priority |
| --- | --- | --- | --- |
| Test baseline | 197/198 passing; editable import ambiguity | Source, editable install, wheel install, and packaged smoke tests all pass | P0 |
| Paths | `AppPaths` exists but most code still uses import-time constants/state strings | One injected, validated path authority in all entry points | P0 |
| State | Atomic JSON plus a partial file-lock transaction API | Versioned database, repositories, migrations, integrity checks, recovery | P0 |
| Operations | Long-running work is ad hoc and UI-thread-owned | Persistent operation journal with cancellation, phases, recovery, and rollback | P0 |
| Host privilege | Arbitrary command elevation through `pkexec` or `sudo` | Allowlisted helper with typed requests and policy authorization | P0 |
| Thermal safety | Better latch logic, but process-owned and not independently supervised | Boot-persistent safety supervisor with fail-safe stop behavior | P0 |
| Remote sharing | Tailscale exposes the raw unauthenticated llama API | Authenticated gateway, least exposure, rotation, audit events | P0 |
| Supply chain | Mutable image/tag references and source builds | Digests/checksums, provenance records, signed manifests, rollback | P0 |
| Model storage | Files are path-managed and partly staged | Content-addressed artifacts, validation records, quotas, garbage collection | P1 |
| GUI | Functional mixin dashboard with daemon threads and stale state saves | Service-driven tasks, cancellation, resumable status, accessibility | P1 |
| Diagnostics | Human logs and doctor commands | Structured events, redacted support bundles, bounded metrics, health model | P1 |
| Recovery | Some rollback paths | Tested backup, restore, repair, factory reset, and interrupted-update recovery | P1 |
| Release | Initial CI/build workflow | Reproducible signed artifacts, SBOM, migration matrix, VM and BC-250 evidence | P0 for 1.0 |

---

## 2. Status legend and execution rules

Use these labels in this document as implementation proceeds:

- **DONE:** Implemented, tested at the required level, documented, and no known follow-up remains for the milestone.
- **PARTIAL:** Useful code exists, but one or more production acceptance criteria remain unmet.
- **NOT STARTED:** No implementation with the required contract exists.
- **BLOCKED:** Work cannot safely continue without an external decision, hardware, credential, or upstream result.

Every task below has a stable identifier. Commit messages, pull requests, changelog entries, and follow-up notes should cite those identifiers.

### Mandatory implementation rules

1. Preserve the dirty working tree until its changes have been reviewed and split into coherent commits. Never reset it to `v0.7.0`.
2. Fix or add a failing test before changing the corresponding production behavior when practical.
3. Do not run privileged setup, model downloads, systemd mutations, Bazzite mode transitions, or BIOS changes in the macOS development workspace.
4. Keep hardware-dependent behavior behind `CommandRunner` and injectable adapters.
5. Never bypass `catalog.calculate_fit()` for model, context, slot, or profile changes.
6. Never start `llama-server` directly outside the server ownership layer.
7. Every state-changing operation must have an explicit precondition, durable phase record, success condition, failure condition, and recovery action.
8. Every irreversible action must require explicit user confirmation and list its exact targets.
9. Keep the default deployment loopback-only. Remote access is opt-in and authenticated.
10. Treat logs, support bundles, API credentials, conversation files, and model provenance as security-sensitive data.
11. A release milestone is complete only when its exit gate passes. Feature presence alone is insufficient.

---

## 3. Current implementation delta

This section prevents duplicate work and identifies where the current feature pass stops short of the production target.

### 3.1 Completed or substantially complete

These items should be verified and preserved:

- The package has moved to the `0.8.0.dev0` development version.
- `CHANGELOG.md` and GitHub Actions CI scaffolding exist.
- The CLI `llm` action scope bug has been corrected to use `args.action` and the normal output path.
- Generated launcher line continuations for repeat penalty and threads have been corrected.
- GUI code has been split from one module into the `bc250_llm_mode/gui/` package.
- Catalog search and recommendation support and an expanded model catalog exist.
- Chat benchmarking, prompt caching, persistence/export, sampling overrides, retry, trimming, reasoning filtering, and recommendation features exist.
- Thermal logic is separated from host side effects and includes hysteresis/latch improvements.
- Autotuning evaluates bounded candidates and retains history.
- llama.cpp updates use a staging location and retain a physical rollback tree.
- Open WebUI has baseline container restrictions such as dropped capabilities, `no-new-privileges`, a PID limit, and a memory limit.
- `AppPaths` exists with constructors, directory creation, and basic symlink validation.
- `StateStore.transaction()` uses a file lock and revision counter.
- Catalog validation tiers distinguish supported and preview entries.

These are not automatically **DONE** for production; several are marked **PARTIAL** below because integration, security, packaging, or recovery work remains.

### 3.2 Partial implementations

| Capability | What exists | What remains |
| --- | --- | --- |
| `AppPaths` | Dataclass and validation helpers | Inject through every CLI, GUI, service, test, and state path; eliminate import-time path authority |
| State locking | File-locked transaction method | Convert all stale load/mutate/save call sites; then migrate durable state and operations to SQLite |
| CI | Linux tests and source/wheel build smoke | Lint, types, coverage, migration matrix, GUI headless tests, security scans, SBOM, signed release artifacts, pinned actions |
| Runtime update | Staging and one backup | Immutable source identity, manifest/checksum verification, operation journal, rollback test, retention policy |
| Thermal safety | Pure decision logic and latch | Independent service, persistent sensor health, boot behavior, watchdog integration, hardware validation |
| Open WebUI | Version tag and container restrictions | Digest pin, private networking, secrets, authenticated gateway, backup/update/rollback, health and compatibility checks |
| GUI package | Better module split | Service boundary, operation model, cancellation, event subscriptions, accessibility, integration tests |
| Chat | Rich feature set | Bounded timeouts, robust stream parser, atomic conversation writes, token-aware budgeting, cancellation and privacy controls |
| Model validation | GGUF constraints and fit checks | Content digest, provenance, quarantine, staged activation, storage accounting, integrity recheck |
| Logging | Existing setup logs | Structured redacted events, correlation IDs, rotation, metrics, support bundle |

### 3.3 Not started at the production contract level

- SQLite-backed durable state with versioned migrations and integrity recovery.
- Persistent operation engine and crash recovery.
- Domain service facade shared by CLI and GUI.
- Typed, allowlisted privileged helper and policy rules.
- Independent system safety supervisor.
- Authenticated remote API gateway and credential rotation.
- Signed application update manifest and transactional application updater.
- Immutable runtime, image, and model provenance enforcement.
- Content-addressed model artifact store and storage manager.
- Named workload profiles with fit-derived safe limits.
- Structured event/metrics store and redacted support bundle.
- Backup, restore, repair, and factory reset workflows.
- Reproducible signed release process with SBOM and provenance.
- Bazzite VM qualification and real BC-250 hardware-in-the-loop release gate.

---

## 4. Non-negotiable target architecture

The production architecture must separate user intent, durable orchestration, unprivileged services, and privileged host mutations.

```text
CLI / tkinter GUI / terminal chat
              |
              v
       Domain service facade
              |
       +------+-------------------------+
       |                                |
       v                                v
Persistent operation engine       Query repositories
       |                                |
       v                                v
Unprivileged adapters          SQLite + artifact manifests
       |
       +-------------------+
       |                   |
       v                   v
Typed privileged helper    Managed system services
       |                   |
       v                   +--> llama.cpp server (loopback)
Host policy mutations      +--> safety supervisor
                           +--> authenticated gateway
                           +--> optional Open WebUI
```

### Required dependency direction

- GUI and CLI may import domain services and immutable view models.
- Domain services may import repositories, policies, the operation engine, and adapter interfaces.
- Repositories own persistence details. Domain modules must not manipulate raw SQLite or JSON.
- Host adapters may execute commands, but they must not make product policy decisions.
- The privileged helper accepts only typed allowlisted operations; it never accepts arbitrary shell text.
- Generated launch/service configuration must come from typed configuration models and deterministic renderers.
- No domain or GUI module may import user-home defaults as hidden global state.

### Proposed package layout

Names may change during implementation, but responsibilities must remain separated:

```text
bc250_llm_mode/
  app.py                      # composition root
  paths.py                    # AppPaths only
  config.py                   # typed runtime/application configuration
  db.py                       # connection, transaction, integrity helpers
  migrations/                 # ordered schema migrations
  repositories/
    state.py
    models.py
    operations.py
    events.py
    metrics.py
    credentials.py            # metadata only; secrets remain in protected files/store
  operations/
    engine.py
    types.py
    recovery.py
    steps.py
  services/
    runtime.py
    models.py
    profiles.py
    sharing.py
    updates.py
    diagnostics.py
    backup.py
    safety.py
  adapters/
    commands.py
    systemd.py
    podman.py
    tailscale.py
    sensors.py
    filesystem.py
  privileged/
    protocol.py
    client.py
    helper.py
    policy.py
  gui/
  cli/
  chat.py
```

Do not perform a large package move before behavior is covered. Introduce facades and repositories first, migrate call sites incrementally, then move modules when imports are stable.

---

## 5. Dependency and delivery order

The work must be executed in this order. Later milestones depend on contracts created earlier.

```text
R0 Clean baseline
  -> R1 Path authority and composition root
    -> R2 Durable state and migrations
      -> R3 Persistent operation engine
        -> R4 Domain services and host adapters
          -> R5 Privilege and safety boundaries
            -> R6 Authentication and supply-chain security
              -> R7 Storage, profiles, diagnostics, and recovery
                -> R8 GUI/chat completion
                  -> R9 Release qualification and 1.0 launch
```

Parallel work is safe only where explicitly called out. For example, CI lint setup can proceed while path injection is being completed, but a GUI redesign should not proceed before the domain service and operation contracts stabilize.

### Milestone mapping

| Release | Primary goal | Required exit result |
| --- | --- | --- |
| `0.8.0` | Stabilize and package the current feature pass | Clean source/wheel tests, consistent paths, documented preview features, no known P0 regression |
| `0.9.0` | Transactional core and independent safety | SQLite, migrations, operation recovery, service facade, helper boundary, safety supervisor |
| `0.10.0` | Secure exposure and trusted artifacts | Authenticated gateway, immutable dependencies, app/runtime/model provenance, update rollback |
| `0.11.0` | Appliance-grade operations and UX | Storage/profiles, diagnostics, backup/restore, cancellable GUI, hardened chat |
| `1.0.0-rc` | Qualification | Full CI/release pipeline, migration matrix, VM tests, BC-250 HIL passes |
| `1.0.0` | Production release | All go/no-go gates signed off; no open P0/P1 defect |

---

# Release 0.8 — Stabilize the current development pass

## R0. Establish a clean, reproducible baseline

### R0.1 Fix the remaining path-isolation test failure — P0 — PARTIAL

**Affected files:** `bc250_llm_mode/gui/`, `bc250_llm_mode/logging_utils.py`, `bc250_llm_mode/paths.py`, `bc250_llm_mode/state.py`, `tests/test_phase0_cli.py`, shared test fixtures.

**Implementation:**

1. Trace the dashboard refresh call from its `Wizard` or controller object to `CommandRunner` creation.
2. Make the runner’s log target derive from the same injected `AppPaths` instance as the state store.
3. Do not infer a different application root from `Path.home()` after the application is composed.
4. Update the test fixture to build one temporary `AppPaths` and pass it through the real composition path.
5. Add a regression assertion that no file or directory is created outside the temporary application root.
6. Search for other tests that construct only `StateStore(tmp_path / "state.json")`; migrate them to the common isolated application fixture.

**Tests:**

- The currently failing test passes without sandbox escalation.
- A test monkeypatches the real home to a sentinel and verifies no production path is touched.
- Full suite passes with `PYTHONPATH=.`.

**Acceptance criteria:** All 198 current tests pass and isolated tests produce no writes beneath the real user application directory.

### R0.2 Remove dead and inconsistent CLI control flow — P0 — NOT STARTED

**Affected files:** `bc250_llm_mode/__main__.py`, CLI tests.

**Implementation:**

1. Remove the unreachable statements after the `llamacpp` command branch.
2. Audit every command branch for one explicit return path and one output policy.
3. Define stable exit codes: success, validation error, dependency unavailable, operation failed, authorization denied, and internal error.
4. Ensure JSON mode writes machine-readable output only to stdout and diagnostics to stderr.
5. Ensure status/query commands do not require acknowledgements or privilege.
6. Add parser tests for all commands and actions, including missing/invalid combinations.

**Acceptance criteria:** No unreachable command code; CLI behavior and exit codes are documented and covered.

### R0.3 Repair editable-install ambiguity — P0 — NOT STARTED

**Affected files:** `.venv` workflow documentation, `pyproject.toml`, CI, `README.md`, `AGENTS.md`.

**Implementation:**

1. Reinstall the project with `.venv/bin/pip install -e '.[test]'` in an environment where dependency installation is authorized.
2. Confirm `python -c` resolves `bc250_llm_mode.__file__` to the source tree.
3. Add a test or CI smoke assertion that fails if the import resolves to a stale copy.
4. Keep `PYTHONPATH=.` in the emergency local instructions until the editable install is proven reproducible.
5. Document how to recreate the environment from scratch.

**Acceptance criteria:** Both editable-install and source-tree test commands collect the same tests and pass.

### R0.4 Review and split the dirty feature pass — P0 — NOT STARTED

Do this only after the baseline passes. Do not squash unrelated areas into one opaque commit.

**Recommended commit slices:**

1. Version/changelog/packaging and CI scaffolding.
2. Phase 0 CLI, launcher, and GUI import fixes with regression tests.
3. Catalog expansion, validation tiers, local aliases, and docs.
4. Chat persistence, benchmark, command, and recommendation features.
5. Thermal, optimization, and autotune behavior.
6. Runtime update staging and server self-healing.
7. GUI package split and feature additions.
8. Production planning and continuation documents.

**Review checklist for each slice:**

- No unrelated formatting churn.
- Tests specific to the slice pass before and after the commit.
- Public behavior appears in the changelog and README.
- New state fields have defaults and migration coverage.
- Preview capabilities are not described as production-supported.

### R0.5 Reconcile documentation with the code — P1 — NOT STARTED

Update all stale counts, versions, commands, files, security descriptions, and feature maturity labels. In particular:

- Replace the earlier test-count claim with a dynamically maintained statement or remove exact counts from long-lived docs.
- Explicitly label unauthenticated sharing as development-only until R6.
- State that `0.8.0` does not yet provide crash-safe operations or production update guarantees.
- Ensure every model in the README matches catalog ID, tier, context guidance, and local filename aliases.
- Document that the supported production target is still BC-250/Bazzite, not generic Linux.

---

## R1. Make paths and application composition authoritative

### R1.1 Complete `AppPaths` integration — P0 — PARTIAL

**Affected files:** `paths.py`, `constants.py`, `state.py`, `__main__.py`, `bootstrap.py`, `gui/`, `chat.py`, `download.py`, `prepare.py`, `logging_utils.py`, `openwebui.py`, `env.py`, `update.py`, tests.

**Implementation sequence:**

1. Add a single application composition function that accepts `AppPaths`, a command runner, a clock, and optional host adapters.
2. Make all CLI entry points construct `AppPaths.for_home()` once, validate it, and pass it to the composition root.
3. Make tests use `AppPaths.temporary(tmp_path)` or an equivalent fixture.
4. Change `StateStore` to receive either `AppPaths` or an explicit state path plus an explicit lock path. It must not reconstruct sibling paths.
5. Change logging configuration to require an injected logs directory.
6. Replace operational use of `DEFAULT_*` constants with fields on `AppPaths`.
7. Retain constants only as backward-compatible default names if necessary; do not evaluate user home paths at import time.
8. Resolve all model, conversation, backup, staging, and support-bundle paths through `AppPaths`.
9. Validate that app-owned directories are not symlinks before privileged or destructive operations.
10. Validate ownership, expected file type, and permissions for sensitive files.
11. Reject a model path that escapes the configured model root after canonical resolution, except for an explicitly imported external read-only model workflow.
12. Ensure root-run helper processes never use root’s home to locate the calling user’s application state.

**Required test fixture:**

Create a shared `isolated_app` fixture containing:

- temporary `AppPaths`;
- initialized state store;
- fake command runner;
- fake clock;
- fake sensor source;
- captured events/logs;
- helper assertions that no command or file path escapes the temporary root.

**Acceptance criteria:**

- `rg` finds no production path construction from `Path.home()` outside `paths.py` and the explicit composition root.
- Tests can run with an unwritable real home.
- CLI, GUI, chat, updater, downloader, and support paths all use the same application root.

### R1.2 Add filesystem safety primitives — P0 — NOT STARTED

**Affected files:** new `adapters/filesystem.py`, download/update/model modules, tests.

Implement reviewed helpers for:

- canonical containment checks;
- regular-file and directory assertions;
- non-symlink checks;
- safe atomic file replacement;
- fsync of file and parent directory where durability matters;
- permission application and verification;
- bounded recursive deletion of a previously validated exact target;
- free-space checks with reserved headroom;
- temporary/staging directory creation within the destination filesystem;
- ownership verification before privileged changes.

Every helper must return a structured error rather than silently swallowing failures. Cleanup functions may be best-effort only when the primary operation has already failed and the leftover is harmless and reported.

### R1.3 Classify all `check=False`, shell, and destructive command sites — P0 — NOT STARTED

Create a checked-in audit table, ideally in tests or developer documentation, covering every use of:

- `check=False`;
- `bash -lc` or other shell interpretation;
- `rm`, `mv`, `cp`, `install`, or recursive mutation;
- string interpolation into command text;
- calls through `elevated()`.

For each site, mark it as:

- required and checked;
- bounded best-effort cleanup;
- migrated to direct argv;
- migrated to filesystem API;
- delegated to the privileged helper;
- prohibited and removed.

**Acceptance criteria:** No user-controlled or state-controlled path is interpolated into shell text. Ignored command failures are limited to documented cleanup and probing cases.

### R1 exit gate

- Full tests pass from source and editable install.
- All application paths are injected and isolated.
- Filesystem mutation helpers have traversal, symlink, and permission tests.
- Shell/destructive-command audit has no unclassified P0 site.

---

# Release 0.9 — Transactional core and safety boundary

## R2. Replace JSON as the durable source of truth

### R2.1 Freeze and document the existing JSON schema — P0 — NOT STARTED

Before migration code is written:

1. Capture fixture files for every supported state schema version, including malformed-but-recoverable examples.
2. Document every field’s type, default, ownership, and whether it is configuration, observation, history, credential metadata, or derived data.
3. Identify values that should not be durable state, such as transient process IDs or derivable fit results.
4. Identify sensitive values that should be moved to protected secret storage rather than SQLite.
5. Define forward-compatibility behavior for unknown fields in imported JSON.

### R2.2 Introduce SQLite and migration infrastructure — P0 — NOT STARTED

**New files:** `db.py`, `migrations/`, migration tests.

**Database settings:**

- SQLite with foreign keys enabled.
- WAL mode when supported by the deployment filesystem.
- A bounded busy timeout.
- Explicit transactions.
- Schema version table with checksum/name/applied timestamp.
- Integrity check and recoverable backup before migration.
- File mode `0600`; containing directory mode `0700`.

**Initial logical schema:**

```text
schema_migrations(version, name, checksum, applied_at)
settings(key, value_json, updated_at, revision)
runtime_config(id, model_artifact_id, context, slots, profile_id, extra_json, updated_at)
runtime_observation(id, desired_state, observed_state, health, pid, endpoint, checked_at)
model_artifacts(id, sha256, size_bytes, gguf_metadata_json, validation_status,
                source_uri, source_revision, acquired_at, last_verified_at)
model_aliases(alias, artifact_id, display_name, catalog_id)
installed_components(id, component, version, digest, source, installed_at, active)
operations(id, type, status, requested_by, created_at, started_at, finished_at,
           current_step, progress, cancel_requested, error_code, error_json,
           recovery_policy, parent_operation_id)
operation_steps(id, operation_id, sequence, name, status, started_at, finished_at,
                checkpoint_json, rollback_json, error_json)
events(id, occurred_at, severity, category, operation_id, code, payload_json)
metrics(id, observed_at, name, value, unit, labels_json)
autotune_runs(id, model_artifact_id, candidates_json, winner_json, created_at)
backups(id, type, path, manifest_json, created_at, verified_at, status)
credentials(id, purpose, public_id, secret_ref, created_at, rotated_at, revoked_at)
```

Do not store API secret values directly in event payloads or ordinary settings.

### R2.3 Build repositories and typed records — P0 — NOT STARTED

Implement repositories with narrow methods rather than a generic dictionary API:

- settings repository;
- runtime configuration and observation repository;
- model artifact repository;
- operation and step repository;
- component provenance repository;
- event repository;
- metrics repository;
- backup repository;
- credential metadata repository.

Repository methods must:

- accept and return typed dataclasses or validated models;
- own transaction boundaries where atomic multi-table changes are required;
- enforce optimistic revision checks for user-editable configuration;
- avoid exposing raw connections to GUI or domain code;
- map database failures to stable product error codes.

### R2.4 Implement one-time JSON import — P0 — NOT STARTED

Migration flow:

1. Acquire the application migration lock.
2. Validate and back up `state.json` without altering it.
3. Create a new database in staging.
4. Run schema migrations.
5. Convert JSON fields to typed records.
6. Validate invariants, including selected model existence and fit.
7. Run SQLite integrity and foreign-key checks.
8. Atomically publish the database.
9. Mark import completion in the database and write a human-readable migration receipt.
10. Keep the original JSON backup until the next successful version upgrade or an explicit cleanup action.

If import fails, the application must continue in repair mode using the untouched JSON backup; it must not silently initialize empty state.

### R2.5 Migrate every direct state mutation — P0 — NOT STARTED

The audit must explicitly cover existing direct save/mutation sites in:

- `thermals.py`;
- `model_manager.py`;
- `tune.py`;
- GUI forms/dashboard/setup code;
- CLI branches in `__main__.py`;
- bootstrap/setup modules;
- server/runtime update modules;
- Open WebUI and sharing modules;
- chat benchmark history.

For each site:

1. Decide which domain service owns the mutation.
2. Replace whole-state edits with a repository or operation command.
3. Add a concurrent-update regression test.
4. Add a stale-revision test for user-editable settings.
5. Confirm a failure cannot partially update related records.

### R2.6 Add integrity, repair, and export commands — P1 — NOT STARTED

Add read-only `doctor` checks and explicit repair commands for:

- database integrity;
- missing model artifact paths;
- orphaned aliases;
- operations stuck in transitional states;
- missing active component artifacts;
- inconsistent runtime desired/observed state;
- unsupported schema version;
- permissions and ownership.

Provide a redacted state export for support. Repair must never discard data without a preview and explicit confirmation.

### R2 exit gate

- Fixtures from every supported JSON schema migrate deterministically.
- Migration interruption at each durable phase recovers to old or new valid state.
- No production module outside repositories writes raw durable state.
- Database corruption produces repair mode, not an empty replacement database.

---

## R3. Build a persistent operation engine

### R3.1 Define the operation state machine — P0 — NOT STARTED

Use this minimum state model:

```text
QUEUED -> PREPARING -> RUNNING -> VERIFYING -> COMMITTING -> SUCCEEDED
                    \-> CANCELLING -> CANCELLED
                    \-> ROLLING_BACK -> FAILED_ROLLED_BACK
                    \-> RECOVERY_REQUIRED
```

Rules:

- Only valid state transitions are accepted.
- State and step checkpoints are committed before external side effects where recovery requires knowing intent.
- Cancellation is cooperative and only honored at declared safe points.
- `COMMITTING` is short and non-cancellable.
- A process crash leaves enough information to resume, verify, roll back, or ask the user for a decision.
- Operation status is durable and queryable by CLI and GUI.
- A per-resource lock prevents conflicting operations, such as model activation and runtime update.

### R3.2 Define operation types and resource locks — P0 — NOT STARTED

Initial operation types:

- model download/import;
- model prepare/validate;
- model activate;
- model delete/garbage collect;
- runtime start/stop/restart/ensure;
- runtime install/update/rollback;
- application update/rollback;
- Open WebUI install/update/backup/restore/uninstall;
- sharing enable/disable/credential rotation;
- autotune;
- profile apply;
- backup/restore;
- repair/factory reset;
- support bundle generation.

Resource lock keys should include `runtime`, `models`, `llamacpp-install`, `application-update`, `webui`, `sharing`, `host-policy`, and `backup`. Lock ordering must be defined to prevent deadlock.

### R3.3 Implement step/checkpoint contracts — P0 — NOT STARTED

Each operation step declares:

- name and sequence;
- precondition probe;
- side effect;
- postcondition verification;
- checkpoint data;
- compensation/rollback action;
- cancellation safety;
- timeout;
- retry classification;
- user-visible progress label;
- redaction rules.

Do not implement automatic retries for destructive or ambiguous operations. Retry only idempotent probes/download ranges or explicitly designed steps.

### R3.4 Implement startup recovery — P0 — NOT STARTED

At startup:

1. Query non-terminal operations.
2. Inspect the durable phase and host reality.
3. Classify each operation as safely resumable, safely revertible, already completed, or requiring user review.
4. Perform only policy-approved automatic recovery.
5. Record every recovery decision as an event.
6. Surface unresolved recovery before allowing a conflicting operation.

Examples:

- A fully downloaded artifact with matching digest can resume validation.
- A runtime update with an activated new tree but failed health check should restore the recorded backup.
- A database migration interrupted before publication should discard staging and retain the old database.
- A model activation whose service health is unknown should probe before choosing rollback.

### R3.5 Add deterministic cancellation and progress — P1 — NOT STARTED

- Progress must be monotonic within a step and weighted across steps.
- Download progress uses verified byte counts.
- Build progress may be indeterminate but must emit heartbeat events.
- Cancellation removes or quarantines staging artifacts according to policy.
- GUI closing must not kill the underlying operation.
- CLI may detach and later query the operation ID.

### R3.6 Add crash-injection tests — P0 — NOT STARTED

For every critical operation, inject failure or process termination:

- before side effect;
- after side effect but before checkpoint;
- after checkpoint but before verification;
- during verification;
- during commit;
- during rollback.

Assert restart recovery produces one valid terminal state and preserves the prior working configuration when the new configuration cannot be verified.

### R3 exit gate

- Critical long-running workflows are represented as durable operations.
- GUI and CLI can both start, inspect, cancel, and recover the same operation.
- Crash-injection tests cover model activation, runtime update, application update, and restore.

---

## R4. Introduce domain services and typed host adapters

### R4.1 Create the application composition root — P0 — NOT STARTED

The composition root must instantiate:

- paths;
- database and repositories;
- operation engine;
- event sink;
- command runner;
- clock;
- filesystem adapter;
- systemd, Podman, Tailscale, sensor, and network adapters;
- domain services;
- CLI or GUI frontend.

Production construction and test construction must differ only in adapters, not business logic.

### R4.2 Build domain service facades — P0 — NOT STARTED

Create stable service interfaces for:

- runtime status and lifecycle;
- model catalog/search/import/prepare/activate/remove;
- workload profiles and fit preview;
- thermal and host policy status;
- sharing and credential management;
- component updates and rollback;
- backups and restore;
- diagnostics and support bundles.

Service methods return typed results containing operation ID, warnings, required acknowledgement, and stable error information. Frontends must not parse log strings to infer success.

### R4.3 Replace positional launcher configuration — P0 — NOT STARTED

The current generated shell launcher uses positional configuration values and remains fragile even after line-continuation fixes.

Implementation options, in preferred order:

1. Generate a validated argv vector directly in Python and use a minimal `exec` wrapper only if systemd requires it.
2. Write a small Python launcher that reads a mode-`0600` typed configuration file and calls `os.execv()` with a constructed argv list.
3. If a shell file remains, render it from a typed model, quote every value, and syntax-test plus behavior-test it.

Specific requirements:

- Reject unknown flags and invalid numeric ranges.
- Do not emit `--threads 0`; compute an explicit bounded thread count.
- Preserve the one-service-owner invariant.
- Include runtime version/digest and config revision in status output.
- Unit-test exact argv for each supported profile.

### R4.4 Separate desired and observed runtime state — P0 — NOT STARTED

Store user intent separately from probes:

- desired state: stopped/running, selected model, context, slots, profile;
- observed state: service active state, endpoint health, loaded model, process identity, last probe.

`ensure` reconciles desired and observed state through an operation. A failed health check must not rewrite desired configuration unless rollback policy explicitly restores the prior known-good configuration.

### R4.5 Replace broad command execution with typed adapters — P0 — NOT STARTED

Adapters must expose methods such as:

- `systemd.start(unit)`, `stop(unit)`, `is_active(unit)`, `verify_unit(path)`;
- `podman.pull(image_digest)`, `run(spec)`, `inspect(name)`;
- `tailscale.serve_https(target, identity)`, `disable_serve()`;
- `sensors.read_snapshot()`;
- `filesystem.swap_trees(active, staged, backup)`.

They may internally invoke commands by argv, but callers cannot provide arbitrary flags or shell text.

### R4.6 Add timeout and error taxonomy — P0 — NOT STARTED

Define stable error categories:

- invalid input;
- unsupported host;
- dependency missing;
- insufficient space/memory;
- fit rejected;
- authorization denied;
- command timeout;
- network unavailable;
- checksum/provenance failure;
- health check failure;
- thermal safety stop;
- state conflict;
- recovery required;
- internal error.

Every external command and HTTP request receives a bounded timeout appropriate to its operation. Long downloads use progress and idle timeout rather than an infinite total timeout.

### R4 exit gate

- CLI and GUI use domain services rather than mutating state or running commands directly.
- Runtime launcher generation is typed and behavior-tested.
- External integrations have fake adapters and stable errors.
- Desired/observed status discrepancies are visible and recoverable.

---

## R5. Establish privilege and thermal safety boundaries

### R5.1 Define the privileged operation protocol — P0 — NOT STARTED

The helper must accept a versioned request schema, not a command string. Initial allowlisted requests may include:

- install/remove a specifically named generated systemd unit from an approved staging file;
- daemon reload;
- start/stop/restart an allowlisted unit;
- apply/revert a bounded CPU governor profile;
- apply/revert validated GPU clock limits;
- install/remove approved desktop/session configuration;
- perform a validated atomic swap of approved runtime directories;
- read narrowly scoped host status unavailable to the unprivileged process.

Each request includes operation ID, caller identity, target, validated parameters, and protocol version.

### R5.2 Implement helper-side validation — P0 — NOT STARTED

The helper must independently validate:

- real user identity and authorization;
- exact unit names and directories;
- canonical paths beneath configured roots;
- ownership and non-symlink status;
- numeric ranges;
- expected source/destination types;
- operation nonce or ID;
- request size and schema;
- absence of unknown fields for security-sensitive operations.

The helper should use direct syscalls/filesystem APIs or fixed argv calls. It must not invoke a shell with request-derived content.

### R5.3 Add policy authorization and packaging — P0 — NOT STARTED

- Add a narrowly scoped polkit policy or equivalent Bazzite-supported authorization mechanism.
- Require interactive authorization for installation, host mode transitions, destructive reset, and policy changes.
- Avoid repeated prompts for safe steps within one authenticated operation where policy supports it.
- Package helper, policy, and service files with verified permissions.
- Add install/uninstall tests in a disposable Linux environment.

### R5.4 Migrate existing elevated call sites — P0 — NOT STARTED

Inventory every use of `elevated()` and map it to an allowlisted helper operation. Remove the generic `elevated(command)` API after the last migration. A temporary compatibility layer may exist only with a failing test that tracks remaining call sites.

### R5.5 Implement the independent safety supervisor — P0 — NOT STARTED

The supervisor must run independently of the GUI and chat processes and own continuous safety enforcement.

Responsibilities:

- poll configured and validated sensor sources;
- distinguish sensor unavailable, sensor stale, and sensor over-threshold;
- apply thermal hysteresis;
- stop the model service at the critical threshold;
- latch the stop across process restarts and reboots;
- require deliberate human reset after conditions are safe;
- emit structured events;
- expose a read-only status endpoint or status file;
- fail safely if no trustworthy temperature reading is available for a configured duration.

### R5.6 Define sensor and threshold policy — P0 — NOT STARTED

- Enumerate supported BC-250 sensor labels/paths and units.
- Reject implausible readings.
- Store warning, throttle, stop, and resume thresholds with bounded ranges.
- Keep a minimum hysteresis gap.
- Prevent user profiles from raising values beyond compiled safety maxima without a separately reviewed expert mechanism.
- Define policy for sensor loss while idle, loading, and actively generating.

### R5.7 Generate and verify service units — P0 — NOT STARTED

Add separate units as appropriate for:

- llama server;
- safety supervisor;
- authenticated gateway;
- optional Open WebUI;
- one-shot boot cleanup/revert if required.

Apply systemd hardening compatible with GPU/runtime access, such as restrictive filesystem access, private temporary directories, capability bounding, no-new-privileges, restart policy, and resource limits. Validate generated units with `systemd-analyze verify` in Linux CI or VM tests.

### R5.8 Hardware-test the safety chain — P0 — BLOCKED until BC-250 access

Required BC-250 tests:

- valid sensor discovery after cold boot and warm reboot;
- warning and throttle transitions;
- simulated/controlled critical stop;
- latch survives supervisor restart and host reboot;
- manual reset is denied while temperature is unsafe;
- sensor disappearance triggers the documented fail-safe behavior;
- desktop reboot-safety policy still returns the next boot to graphical mode;
- host tuning cleanup occurs after stop, crash, and reboot.

### R5 exit gate

- No arbitrary elevated command interface remains.
- Thermal enforcement works without GUI, chat, or CLI running.
- Unit files pass static verification and disposable-host tests.
- Hardware evidence confirms stop/latch/revert behavior.

---

# Release 0.10 — Secure access and trusted artifacts

## R6. Authenticate every remotely reachable request

### R6.1 Perform an authentication capability spike — P0 — NOT STARTED

Before selecting a gateway implementation, verify the exact pinned llama.cpp runtime supports:

- API-key authentication options;
- secret delivery by protected file or environment rather than process argv;
- health endpoints that can remain local-only;
- compatibility with OpenAI-style streaming clients;
- compatibility with Open WebUI.

Record the decision in an architecture decision record. If native authentication is sufficient and safely configurable, prefer it. Otherwise use a pinned, minimal reverse proxy/gateway with a reviewed configuration. Do not write a new security-sensitive proxy casually.

### R6.2 Introduce gateway topology — P0 — NOT STARTED

Required topology:

```text
llama.cpp: loopback/private socket only
       ^
       |
authenticated gateway: only externally shared endpoint
       ^
       +-- local CLI/chat credential
       +-- Open WebUI service credential
       +-- optional Tailscale clients
```

Tailscale Serve must publish the gateway, never the raw llama endpoint.

### R6.3 Credential lifecycle — P0 — NOT STARTED

Implement:

- cryptographically random credentials;
- protected storage with mode `0600` or an OS secret facility;
- credential IDs separate from secret values;
- one-time reveal or explicit copy action;
- rotation with bounded overlap where needed;
- revocation;
- separate credentials per client/purpose;
- no secret values in argv, logs, database events, support bundles, or crash output;
- last-used metadata where the gateway can provide it safely.

Remove the placeholder `sk-no-key-needed` behavior from production paths.

### R6.4 Add gateway policy controls — P1 — NOT STARTED

- Request body and header size limits.
- Connection and concurrency limits consistent with selected slots.
- Idle and total stream timeouts.
- Optional rate limits.
- Method/path allowlist.
- Local-only administrative and health routes.
- Stable request IDs propagated to events.
- Sanitized access logs.

### R6.5 Secure sharing UX — P0 — NOT STARTED

The CLI/GUI must show:

- whether sharing is disabled, local-only, tailnet-only, or otherwise exposed;
- the exact gateway URL;
- authentication status;
- credential age and rotation action;
- a warning before exposure changes;
- an emergency disable action that does not depend on model-server health.

### R6.6 Security tests — P0 — NOT STARTED

Test that:

- requests without credentials fail;
- invalid/revoked credentials fail;
- secrets are absent from `ps`, generated unit text, logs, events, and support bundles;
- raw llama endpoint cannot be reached through Tailscale Serve;
- administrative endpoints are not remotely exposed;
- traversal and oversized requests fail safely;
- credential rotation does not silently leave the old credential valid after the overlap deadline.

---

## R7. Harden Open WebUI

### R7.1 Pin and record the image digest — P0 — PARTIAL

Replace the mutable version-only image reference with a reviewed digest. Record image name, version, digest, acquisition time, compatibility version, and license/provenance metadata in installed components.

### R7.2 Replace host networking — P0 — NOT STARTED

- Create a private Podman network or equivalent constrained topology.
- Connect Open WebUI only to the authenticated gateway.
- Bind the UI port to loopback by default.
- Publish it through Tailscale only through an explicit sharing action.
- Verify the container cannot reach unnecessary host services.

### R7.3 Harden container runtime policy — P0 — PARTIAL

In addition to current capability and process limits:

- use a read-only root filesystem if compatible;
- provide explicit writable volumes/tmpfs only where needed;
- run as a non-root UID where supported;
- set CPU/memory limits compatible with the 4 GiB host budget;
- add health checks;
- set restart behavior deliberately;
- constrain environment variables and redact inspect output;
- verify SELinux labeling on Bazzite;
- document volume location and ownership.

### R7.4 Implement backup/update/rollback — P0 — NOT STARTED

Before image update:

1. Check free space.
2. Stop or quiesce the UI.
3. Create and verify a data backup.
4. Pull the exact approved digest.
5. Launch a staging instance or perform a compatibility probe.
6. Activate the new container.
7. Verify health and gateway connectivity.
8. Roll back image and data if activation fails.
9. Retain backups according to a bounded policy.

### R7.5 Handle first-run security — P0 — NOT STARTED

- Require documented admin account creation.
- Do not expose the first-run setup endpoint beyond loopback/tailnet policy.
- Warn if Open WebUI internal authentication is disabled.
- Define supported account recovery without exposing secrets in logs.

---

## R8. Make updates and dependencies reproducible

### R8.1 Create a signed release manifest format — P0 — NOT STARTED

The manifest should include:

- application version and channel;
- supported source and target versions;
- package artifact URLs and SHA-256 digests;
- platform/architecture;
- runtime source revision and build recipe identity;
- container image digests;
- required database migration range;
- release notes URL;
- minimum free space;
- signing key ID and signature.

Embed trusted public keys in the installed application through a reviewed rotation mechanism.

### R8.2 Implement transactional application updates — P0 — NOT STARTED

Flow:

1. Fetch and verify signed metadata.
2. Reject downgrade unless explicitly authorized and schema-compatible.
3. Check host compatibility and free space.
4. Download to staging with digest verification.
5. Back up database/configuration and record current package provenance.
6. Install into a versioned location or immutable environment.
7. Run offline migration and self-tests.
8. Atomically switch the active version.
9. Run post-activation health checks.
10. Roll back code and database when compatible; otherwise enter explicit recovery mode.

Never run unverified update scripts downloaded from the network.

### R8.3 Complete llama.cpp update provenance — P0 — PARTIAL

Replace mutable tags with an approved commit SHA or signed release artifact. Record:

- upstream repository;
- exact commit/release;
- source digest;
- build container digest;
- build options;
- compiler/runtime versions;
- binary digest;
- compatibility probe results.

Expand the existing one-backup mechanism into operation-managed retention. Test both activation failure and later manual rollback.

### R8.4 Pin build and base images — P0 — NOT STARTED

Replace `registry.fedoraproject.org/fedora:latest` and other mutable references with digests. Update them only through reviewed manifest changes. CI must fail if production image references are tag-only.

### R8.5 Produce SBOM and provenance — P0 — NOT STARTED

For application and runtime artifacts:

- generate CycloneDX or SPDX SBOM;
- include Python dependencies, container images, and compiled runtime source identity;
- attach build provenance/attestation where the release platform permits;
- archive license inventory;
- scan known vulnerabilities and document exception policy;
- sign release artifacts and checksums.

---

## R9. Build a trusted model artifact pipeline

### R9.1 Use content hashes as artifact identity — P0 — NOT STARTED

The current local identifier hashes the path string rather than file content. Replace it with SHA-256 of the artifact bytes, calculated during download/import with progress reporting.

Store aliases separately so renaming a file does not create a new artifact identity.

### R9.2 Introduce staged acquisition and quarantine — P0 — NOT STARTED

All downloads/imports enter an operation-specific staging directory. They are not available for activation until:

- transfer completes;
- expected size is met where known;
- content digest is known and verified where published;
- GGUF parsing succeeds;
- artifact restrictions pass;
- fit policy has at least one safe supported profile;
- provenance is recorded.

Invalid artifacts move to a bounded quarantine or are removed with a recorded reason. Never leave a partial file under its final model alias.

### R9.3 Enforce GGUF and companion-file policy — P0 — PARTIAL

Retain the current prohibition on fused/MAX, vision projector, and MTP artifacts unless the supported runtime and hardware policy explicitly changes. Expand tests for:

- malformed headers;
- oversized metadata fields;
- sparse/truncated files;
- misleading extensions;
- symlinks and hard links;
- duplicate artifacts;
- unsupported architecture/quantization;
- external paths;
- companion files accidentally selected as the main model.

### R9.4 Build content-addressed storage and aliases — P1 — NOT STARTED

Recommended layout:

```text
models/
  objects/sha256/ab/<full-digest>.gguf
  aliases/<safe-display-name>.json
  staging/<operation-id>/
  quarantine/<operation-id>/
```

Do not use untrusted model names directly as filesystem paths. Alias documents should point to an artifact ID and contain validated display metadata.

### R9.5 Add integrity re-verification — P1 — NOT STARTED

- Fast checks on startup: existence, size, metadata fingerprint.
- Full digest check on import, before first activation, and on explicit verify.
- Scheduled/background verification only when it will not interfere with inference.
- Mark changed artifacts untrusted and prevent activation until revalidated.

### R9.6 Catalog governance — P1 — PARTIAL

For each catalog entry require:

- immutable upstream revision or artifact digest when available;
- expected filename and size;
- license and attribution metadata;
- validation tier (`supported`, `preview`, `blocked`);
- tested context/slot/profile combinations;
- chat template behavior;
- expected quality/performance notes;
- local filename aliases;
- test coverage.

Promotion from preview to supported requires BC-250 evidence, not only a fit calculation.

### R9 exit gate

- Every active model has a content digest and validation record.
- Partial/corrupt artifacts cannot appear installed.
- Artifact removal cannot escape the model object store.
- Supported catalog entries have provenance and hardware evidence.

---

# Release 0.11 — Appliance operations, recovery, and UX

## R10. Add storage management and workload profiles

### R10.1 Build a storage inventory — P1 — NOT STARTED

Report separately:

- active model objects;
- inactive model objects;
- duplicate aliases;
- partial staging data;
- quarantine;
- runtime source/build/install trees;
- Open WebUI data/backups;
- application backups;
- conversations;
- logs/metrics/support bundles;
- reclaimable total.

Use actual allocated size where meaningful, not only logical file size.

### R10.2 Add preflight and quota policy — P0 — NOT STARTED

Every download, build, backup, and update must calculate:

- expected final size;
- staging overhead;
- rollback/backup overhead;
- filesystem reserve;
- worst-case temporary use.

Reject operations that cannot retain both the old known-good state and the new staged state. Do not solve low disk space by deleting the rollback copy automatically.

### R10.3 Add safe cleanup and garbage collection — P1 — NOT STARTED

- Preview exact objects and bytes.
- Never delete the active artifact, required rollback artifact, in-progress staging data, or most recent verified backup.
- Remove aliases before collecting an unreferenced object.
- Recheck references in the same database transaction that marks an object for deletion.
- Perform deletion through validated filesystem helpers.
- Record results as events.

### R10.4 Introduce named workload profiles — P1 — NOT STARTED

Initial profiles:

- **Safe:** conservative context, one slot, conservative clocks, lower thermal target.
- **Balanced:** validated default for normal interactive use.
- **Throughput:** multiple slots only when the fit model and host budget permit.
- **Long Context:** lower concurrency and explicitly calculated KV-cache budget.
- **Custom:** bounded advanced settings with validation preview.

Profiles must declare model/context/slots, thread count, batch settings, GPU settings, thermal policy, and expected host-memory reserve. Applying a profile is transactional and must roll back after a failed health check.

### R10.5 Improve fit calculations with measured calibration — P1 — NOT STARTED

Keep the conservative static calculation as the hard preflight. Add optional measured data:

- actual runtime memory after load;
- peak memory during prompt processing;
- per-slot impact;
- model-specific overhead;
- safe headroom.

Measured values may make recommendations more conservative automatically. They must not override compiled hard safety ceilings without a reviewed policy change.

---

## R11. Structured observability and diagnostics

### R11.1 Define a structured event schema — P1 — NOT STARTED

Every event should have timestamp, severity, category, stable code, operation ID, component, redacted payload, and optional remediation ID. Categories include lifecycle, model, thermal, security, update, storage, network, and recovery.

### R11.2 Add correlation-aware logging — P1 — NOT STARTED

- Generate a correlation/operation ID for every state-changing request.
- Include it in application logs, helper requests, service events, and UI error details.
- Use rotation and bounded retention.
- Sanitize paths/usernames where support export requires it.
- Never log prompts or conversation content by default.
- Never log credentials, authorization headers, full environment dumps, or unredacted command output likely to contain secrets.

### R11.3 Add bounded metrics — P1 — NOT STARTED

Capture low-frequency appliance metrics:

- temperatures;
- model-server health and restart count;
- load and generation latency;
- prompt/generated token rate;
- context/slot settings;
- memory and disk headroom;
- operation duration/failure category;
- gateway request count and rejected-auth count without prompt content.

Define retention/downsampling so the database cannot grow without bound.

### R11.4 Expand `doctor` into a layered health model — P1 — PARTIAL

Checks should cover:

1. Platform and hardware identity.
2. Application paths and permissions.
3. Database integrity and schema.
4. Required command/runtime versions.
5. systemd units and desired/observed state.
6. GPU/Vulkan access.
7. sensor availability and safety latch.
8. model artifact validity and fit.
9. gateway authentication and exposure.
10. Open WebUI health.
11. disk/memory headroom.
12. update and backup recoverability.

Each check returns status, evidence, impact, and remediation. `doctor --json` must be stable enough for support tooling.

### R11.5 Add a redacted support bundle — P1 — NOT STARTED

Bundle contents:

- version/build/provenance;
- redacted configuration;
- database integrity summary and selected redacted records;
- recent structured events;
- bounded service logs;
- generated unit/config fingerprints, not secrets;
- hardware/Vulkan/sensor summaries;
- model IDs/digests and metadata, not model contents;
- doctor report;
- manifest with file hashes.

Before creation, show what will be included. Add automated secret canaries to verify redaction.

---

## R12. Backup, restore, repair, and factory reset

### R12.1 Define backup scopes — P1 — NOT STARTED

Support at least:

- configuration-only backup;
- configuration plus metadata/conversations;
- Open WebUI data backup;
- full portable backup excluding reproducible model/runtime artifacts by default;
- optional model inclusion with size warning.

Each backup has a versioned manifest, hashes, application/schema version, platform information, and required space estimate.

### R12.2 Implement consistent backup — P1 — NOT STARTED

- Quiesce or snapshot SQLite correctly.
- Coordinate with in-progress operations.
- Never copy a live database file unsafely.
- Verify archive readability and file hashes before marking the backup valid.
- Restrict permissions.
- Keep retention bounded.

### R12.3 Implement staged restore — P0 — NOT STARTED

Flow:

1. Read and validate manifest.
2. Check compatibility and free space.
3. Extract to staging with traversal protection.
4. Verify every hash and file type.
5. Back up current state.
6. Stop affected services.
7. Run required migrations in staging.
8. Atomically activate restored state.
9. Verify database, paths, services, and selected model references.
10. Roll back current state if verification fails.

### R12.4 Add repair mode — P0 — NOT STARTED

Repair mode must start without the model service and provide:

- database integrity diagnosis;
- recovery from last verified backup;
- operation-journal reconciliation;
- component provenance verification;
- service/unit regeneration;
- model alias rebuild from trusted object metadata;
- sharing emergency disable;
- support bundle generation.

### R12.5 Add previewable factory reset — P1 — NOT STARTED

Factory reset options should independently cover:

- app configuration/database;
- generated services and host tuning;
- sharing credentials/configuration;
- Open WebUI container/data;
- conversations/logs;
- model files;
- runtime builds.

Default reset must preserve large model files unless the user explicitly selects their removal. Show canonical targets and estimated bytes before confirmation. Record whether each removed class is recoverable.

---

## R13. Complete the GUI and terminal experience

### R13.1 Move GUI actions to services/operations — P0 — PARTIAL

Eliminate from GUI code:

- direct state mutation/save;
- direct `CommandRunner` construction;
- direct systemd/Podman/Tailscale invocation;
- assumptions that a daemon thread completes before process exit;
- parsing logs for domain status.

GUI handlers should validate input, call a service, subscribe to operation events, and render typed results.

### R13.2 Add a real GUI task model — P1 — NOT STARTED

Each visible operation should support:

- operation name and ID;
- current phase and progress;
- start time and elapsed time;
- cancellation where safe;
- clear non-cancellable phase indication;
- warnings requiring acknowledgement;
- recovery/rollback status;
- copyable error details and remediation;
- persistence across GUI restart.

Closing the window should detach from safe operations, not terminate them. Destructive confirmation dialogs must identify exact effects.

### R13.3 Add stale-data and concurrency handling — P1 — NOT STARTED

- Read view models with revisions.
- Detect edits based on stale revisions.
- Refresh only affected panels after operation events.
- Disable conflicting actions according to resource locks.
- Preserve unsaved user form input during unrelated status refreshes.
- Make thermal and sharing emergency controls available even when another non-conflicting operation is active.

### R13.4 Improve accessibility and resilience — P1 — NOT STARTED

- Full keyboard navigation.
- Visible focus.
- Screen-reader labels where tkinter permits.
- Non-color-only status indicators.
- Scalable text and usable layout at common resolutions.
- No modal dialog loops.
- Recoverable validation messages adjacent to fields.
- Graceful handling of missing optional tools and unavailable services.

### R13.5 Add headless and integration tests — P0 for 1.0 — NOT STARTED

Test under Xvfb/Linux:

- import and startup;
- setup wizard resume;
- dashboard refresh;
- model selection and fit rejection;
- operation progress and cancellation;
- stale revision conflict;
- thermal latch display/reset denial;
- sharing enable/disable and credential rotation;
- update failure/rollback rendering;
- repair mode startup.

Use fake services for most tests and a small number of Linux integration tests with real tkinter event processing.

### R13.6 Harden terminal chat — P1 — PARTIAL

Implementation tasks:

- Replace `timeout=None` with connect, write, read-idle, and operation deadlines.
- Build a strict incremental SSE parser that tolerates fragmented events, comments, unknown fields, and terminal markers.
- Add cooperative cancellation and clean terminal restoration.
- Write conversation files atomically with restrictive permissions.
- Make conversation persistence opt-in or clearly disclosed.
- Add retention/delete commands and exclude content from support bundles.
- Use model-aware token counting where available; otherwise use a conservative estimator.
- Ensure prompt cache keys include model identity, system prompt, template, and relevant sampling/context parameters.
- Bound retries and never blindly retry a request after generation may have started.
- Make reasoning filtering robust to split tags and malformed output.
- Record benchmark methodology and environment so comparisons are meaningful.

### R13 exit gate

- GUI and CLI present the same operation/status truth.
- Closing/restarting a frontend does not corrupt or lose long-running work.
- Chat cannot wait forever without a visible cancellation path.
- Accessibility and headless integration checks pass.

---

# Release 1.0 — Verification, release engineering, and launch

## R14. Expand automated quality gates

### R14.1 Align the Python/platform matrix — P0 — PARTIAL

The current workflow tests Python 3.11 and 3.14. Confirm the Python versions actually shipped/supported by target Bazzite releases, then test the minimum, current target, and next version intentionally. Avoid a matrix that skips deployed versions without explanation.

### R14.2 Add lint, formatting, and type checks — P0 — NOT STARTED

Recommended gates:

- Ruff formatting check and lint;
- mypy or pyright on domain, repository, operation, and privileged protocol boundaries;
- import-cycle check;
- compileall;
- build metadata/version consistency;
- generated artifact determinism checks.

Adopt incrementally with a checked baseline; do not hide new warnings with broad ignores.

### R14.3 Add coverage policy — P1 — NOT STARTED

Use risk-based coverage, not only a global percentage:

- near-complete branch coverage for fit policy, thermal hysteresis, migrations, operation transitions, path validation, helper request validation, auth, update verification, and restore;
- strong coverage for service orchestration and CLI outputs;
- lower emphasis on trivial GUI rendering lines.

Publish coverage artifacts and prevent regression in critical modules.

### R14.4 Pin CI actions and harden workflow permissions — P0 — PARTIAL

- Pin third-party actions to reviewed commit SHAs.
- Set minimum token permissions per job.
- Separate untrusted pull-request tests from signing/release jobs.
- Never expose signing or publishing credentials to forked code.
- Cache dependencies safely using lockfile hashes.
- Upload test/build reports with bounded retention.

### R14.5 Add dependency and security scanning — P0 — PARTIAL

Replace the crude secret grep as the only control with layered checks:

- secret scanner with test canary;
- Python dependency vulnerability scan;
- container image scan;
- static security analysis;
- SBOM generation;
- license policy check;
- production-reference check rejecting mutable tags;
- shell-script analysis for any remaining generated shell.

Scans need a documented triage and exception process so failures are actionable.

### R14.6 Add packaging tests — P0 — PARTIAL

Test:

- sdist build/install;
- wheel build/install in a clean environment;
- console entry point;
- GUI imports/resources;
- migration files and service templates included;
- license/changelog/readme metadata;
- no development-only paths embedded;
- package uninstall does not remove user data unexpectedly.

### R14.7 Add migration matrix tests — P0 — NOT STARTED

For every released schema/application pair still supported:

- install old version or fixture state;
- populate representative data;
- upgrade to candidate;
- verify state, models, profiles, operations, conversations, and credentials metadata;
- test failed migration recovery;
- test restore after upgrade;
- document downgrade limitations.

### R14.8 Add fuzz/property tests for trust boundaries — P1 — NOT STARTED

Targets:

- path containment and archive extraction;
- GGUF metadata parsing;
- privileged request decoding/validation;
- SSE parsing;
- migration import conversion;
- operation state transitions;
- signed manifest parsing;
- gateway header/path policy.

Use bounded deterministic corpora in normal CI and longer fuzz runs on schedule.

---

## R15. Build environment and hardware qualification

### R15.1 Linux/Bazzite disposable integration environment — P0 — NOT STARTED

Create a reproducible VM/container-based test environment for non-GPU host behavior:

- install/uninstall packaging;
- polkit/helper authorization structure;
- systemd unit generation and verification;
- user service lifecycle where applicable;
- Podman network/container policy;
- Tailscale command adapter behavior with fakes or a test namespace;
- migration, backup, restore, and factory reset;
- Xvfb GUI tests;
- permission and ownership checks.

Container tests alone are insufficient for systemd/polkit semantics; use a VM for those gates.

### R15.2 BC-250 hardware-in-the-loop suite — P0 — BLOCKED until hardware runner exists

Automate or script a repeatable qualification run for:

- hardware/platform validation;
- Vulkan discovery;
- fresh install;
- llama.cpp build/install from approved provenance;
- each supported model tier load;
- supported profile fit and health;
- prompt and streaming generation;
- sustained thermal soak;
- thermal stop/latch/reset;
- server crash and restart policy;
- GUI crash during operation;
- power interruption at selected update/activation phases where safe;
- low-disk behavior;
- Open WebUI install/update/rollback;
- authenticated Tailscale access and unauthorized rejection;
- next-boot graphical recovery;
- uninstall and host-setting cleanup.

Record firmware/BIOS, Bazzite image, kernel, Mesa/Vulkan versions, runtime digest, ambient conditions, and model digest with results.

### R15.3 Performance and resource qualification — P1 — NOT STARTED

For each supported model/profile combination record:

- load time;
- time to first token;
- prompt processing rate;
- generation rate;
- idle/peak host memory;
- GPU/UMA use;
- temperature curve;
- throttling events;
- stability over a defined soak period;
- concurrent-slot behavior;
- recovery after client cancellation.

Set release thresholds and identify regressions relative to the previous candidate.

### R15.4 Failure-mode qualification — P0 — NOT STARTED

Explicitly test:

- missing/corrupt database;
- missing model file;
- corrupted GGUF;
- failed digest/signature;
- network loss during download;
- disk full during staging/backup;
- permission denial;
- helper unavailable;
- systemd/Podman/Tailscale unavailable;
- sensor missing/stale/implausible;
- model server unhealthy after activation;
- gateway credential mismatch;
- Open WebUI schema incompatibility;
- reboot during a pending operation;
- clock change and stale timestamps.

Every failure must produce a stable error, preserved known-good configuration, and an actionable recovery path.

---

## R16. Release process and launch controls

### R16.1 Define release channels — P1 — NOT STARTED

- **Development:** frequent, unsupported migrations allowed with explicit warning.
- **Preview:** feature-complete candidates for volunteer BC-250 testing.
- **Release candidate:** frozen schema/API except blocker fixes; signed artifacts.
- **Stable:** passed all production gates.

Channel metadata must be signed. Downgrades across incompatible schema versions are blocked unless a supported restore path exists.

### R16.2 Produce release artifacts — P0 — NOT STARTED

Each candidate should include:

- wheel and sdist or chosen platform package;
- checksums and signatures;
- signed update manifest;
- SBOM;
- provenance/attestation;
- migration notes;
- upgrade and rollback instructions;
- supported platform/runtime/model matrix;
- known issues;
- backup/restore instructions;
- support bundle instructions.

### R16.3 Run release-candidate soak — P0 — NOT STARTED

Run the candidate on the supported hardware for a defined multi-day period covering idle, repeated model changes, chat, benchmarks, Open WebUI, remote access, reboots, and thermal load. Any P0/P1 defect restarts the relevant qualification gate after correction.

### R16.4 Final documentation pass — P0 — NOT STARTED

Required user documentation:

- supported hardware/software contract;
- installation and preflight;
- security model and remote-access setup;
- model selection and profiles;
- thermal behavior and safety latch;
- backup/restore/repair;
- update/rollback;
- storage cleanup;
- troubleshooting/doctor/support bundles;
- uninstall/factory reset;
- privacy behavior for conversations/logs/metrics;
- limitations and unsupported configurations.

### R16.5 Final go/no-go review — P0 — NOT STARTED

The stable release is **NO-GO** if any item below is false:

- [ ] No open P0 or P1 defect.
- [ ] Source, editable install, sdist, wheel, and installed-package test paths pass.
- [ ] All supported schema migrations and interrupted-migration recovery pass.
- [ ] Critical operations pass crash-injection and rollback tests.
- [ ] Privileged helper has no arbitrary command path.
- [ ] Thermal supervisor independently stops and latches on real BC-250 hardware.
- [ ] Next boot returns to safe graphical mode after all tested LLM-mode scenarios.
- [ ] Remote API access requires authentication and raw backend exposure is impossible by supported configuration.
- [ ] No secrets appear in process arguments, logs, events, diagnostics, or support bundles.
- [ ] Application, runtime, images, and supported models have immutable provenance.
- [ ] Update and rollback pass on supported hardware.
- [ ] Backup and restore are verified, including a failed-restore rollback.
- [ ] Low-space behavior preserves the known-good state.
- [ ] Open WebUI has a tested backup/update/rollback path.
- [ ] GUI headless tests and accessibility review pass.
- [ ] Sustained-load thermal/performance qualification passes.
- [ ] Signed artifacts, manifest, SBOM, and release notes are published.
- [ ] Fresh install, upgrade, repair, uninstall, and factory reset runbooks are verified.

---

## 6. Cross-cutting implementation specifications

These details apply across releases and should be treated as acceptance criteria for relevant tasks.

### 6.1 Transaction boundaries

Use a database transaction for durable intent and metadata, but do not hold a SQLite transaction open across a long external command. Use the operation pattern:

1. Transaction: validate current state, create/update operation step intent, commit.
2. External side effect with timeout.
3. Probe actual result.
4. Transaction: write observation/checkpoint or failure.
5. Continue, compensate, or enter recovery.

This avoids long database locks while retaining crash evidence.

### 6.2 Known-good configuration

Maintain an explicit last-known-good runtime configuration containing:

- model artifact ID/digest;
- context and slots;
- profile and resolved argv;
- runtime component digest/version;
- successful health-check time;
- compatibility metadata.

Update it only after the server has started, loaded the expected model, and passed health and minimal inference probes. Rollback restores this record and verifies it again.

### 6.3 Health checks

Layer health checks:

- process/service active;
- TCP/socket reachable;
- HTTP health endpoint valid;
- expected model loaded;
- minimal inference returns valid streaming/non-streaming response within a bound;
- safety supervisor reports healthy sensor coverage;
- gateway rejects unauthenticated request and accepts an authorized probe.

Do not treat “systemd says active” as application health.

### 6.4 Timeouts and retry policy

Every external action specifies:

- connect/start timeout;
- idle/progress timeout;
- total timeout where appropriate;
- cancellation mechanism;
- retry eligibility;
- maximum retries and backoff;
- behavior after timeout when the external process may still be running.

Never use unbounded HTTP timeouts in interactive production paths.

### 6.5 Secret handling

- Generate with the operating system CSPRNG.
- Store outside ordinary config/state payloads.
- Pass through protected files, inherited descriptors, or carefully scoped environment only when unavoidable.
- Never pass on command argv.
- Redact known secret values and secret-like headers.
- Include canary secrets in tests for logs, events, crash messages, support bundles, and generated service files.
- Rotate after suspected disclosure.

### 6.6 Retention policy

Define bounded defaults for:

- logs;
- metrics;
- events;
- operation history;
- application/runtime rollback copies;
- Open WebUI backups;
- database/configuration backups;
- staging and quarantine;
- conversations.

Retention cleanup is itself an operation when it can remove material user data.

### 6.7 Error presentation

Every user-visible error should contain:

- concise summary;
- stable error code;
- affected operation ID;
- whether the prior state remains working;
- recommended next action;
- location/action for detailed diagnostics;
- whether retry is safe.

Tracebacks belong in development logs, not as the primary end-user message.

---

## 7. Exact verification matrix

### Unit tests

- catalog validation, search, recommendation, and fit boundaries;
- thermal hysteresis and latch transitions;
- path canonicalization, containment, symlinks, permissions;
- configuration parsing and launcher argv generation;
- state migration transforms;
- repository optimistic concurrency;
- operation transitions, locks, cancellation, and recovery classification;
- helper request schema and range validation;
- signed manifest and digest verification;
- model identity/provenance and GGUF validation;
- SSE parsing and chat trimming/token budgets;
- credential redaction;
- storage accounting and cleanup eligibility.

### Component tests with fakes

- model acquisition through activation;
- failed model activation rollback;
- runtime install/update/rollback;
- app update/migration/rollback;
- sharing/gateway credential lifecycle;
- Open WebUI backup/update/rollback;
- autotune winner and failed candidate cleanup;
- backup/restore/factory reset previews;
- doctor and support bundle generation;
- GUI operation subscription and cancellation.

### Linux integration tests

- generated unit verification;
- helper installation and authorization denial/success;
- process/service lifecycle;
- Podman network and container hardening;
- Xvfb GUI behavior;
- package install/uninstall;
- filesystem ownership and modes;
- database WAL/backup behavior.

### Hardware tests

- Vulkan/runtime/model compatibility;
- memory fit under real UMA behavior;
- sustained inference and thermal control;
- reboot safety and host-setting reversal;
- Tailscale authenticated access;
- crash/power-loss recovery scenarios approved for the test rig;
- real performance baselines.

### Release tests

- clean artifact installation without source tree;
- signed update from previous stable release;
- failed update rollback;
- all supported data migrations;
- backup from previous release and restore into candidate where supported;
- SBOM/signature/provenance verification;
- documentation command smoke tests.

---

## 8. Recommended first execution batch

The next implementation session should not begin with a new user-facing feature. Execute this batch in order:

1. **R0.1:** Fix the single failing GUI/log-path isolation test through complete `AppPaths` injection.
2. **R0.2:** Remove unreachable CLI statements and lock down command exit/output behavior.
3. **R0.3:** Repair and verify editable installation.
4. Run `git diff --check`, source tests, editable tests, CLI help/status smoke, package build, and wheel smoke.
5. **R1.1:** Finish path integration across one vertical slice at a time, starting with CLI composition, logging, GUI, then download/update/model operations.
6. **R1.3:** Produce the shell/elevation/destructive-call audit before changing those commands.
7. Review and commit the existing dirty work in the slices listed under R0.4.
8. Begin **R2.1** by freezing JSON fixtures and documenting field ownership.
9. Implement **R2.2–R2.4** in a branch with migration tests before moving any frontend call sites.
10. Build the operation engine before adding application updates, authenticated sharing, or further GUI workflows.

### Commands for the first batch

```bash
git status --short
git diff --check
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m bc250_llm_mode --help
PYTHONPATH=. .venv/bin/python -m build
```

Install the resulting wheel into a fresh temporary virtual environment and run the entry-point/import smoke tests. Do not rely only on the source checkout.

---

## 9. Definition of done for every task

A task is not **DONE** until all applicable items are true:

- Behavior is implemented behind the intended architecture boundary.
- Unit tests cover success, validation failure, dependency failure, and rollback/recovery.
- Relevant integration tests pass.
- No new direct state, shell, path, privilege, or secret-handling bypass was introduced.
- Error codes and user remediation are defined.
- Logging/events are structured and redacted.
- Timeouts and cancellation behavior are explicit.
- Storage and backup implications are handled.
- CLI and GUI behavior remain consistent.
- README/operator documentation and changelog are updated.
- Migration/backward-compatibility impact is tested.
- `git diff --check` and all required quality gates pass.
- The continuation status in this document and `AGENTS.md` is updated.

---

## 10. Scope control and deferred ideas

The following should remain deferred until the 1.0 production foundation is complete unless they are required to solve a release blocker:

- generic non-BC-250 hardware support;
- multi-node inference or model sharding;
- public-internet exposure outside the Tailscale security model;
- arbitrary user-provided llama.cpp flags;
- plugin execution inside the inference appliance;
- automatic BIOS modification;
- automatic overclocking beyond reviewed safe limits;
- cloud account synchronization;
- multi-user role management beyond the selected gateway/Open WebUI model;
- model conversion pipelines that require large host-memory overhead;
- vision/MTP/fused artifacts currently prohibited by the platform policy.

These are valid future directions, but adding them before transactional state, privilege separation, safety supervision, provenance, and recovery would increase launch risk disproportionately.

---

## 11. Handoff update protocol

At the end of every implementation session:

1. Update the status of completed/partial tasks in this document.
2. Record exact tests run and results in the handoff.
3. Record new migrations, schema versions, and compatibility constraints.
4. Record any operation or recovery scenario not yet tested.
5. Record any new privileged/helper request type.
6. Record immutable digests/revisions introduced or changed.
7. Update `AGENTS.md` with only current continuation-critical facts; do not duplicate this entire plan there.
8. Preserve uncommitted user work and identify which files belong to the current task.
9. State the next task ID, its dependencies, and the safest first test.

The immediate next task is **R0.1**. The immediate architectural milestone is the **R1 exit gate**. The first production foundation milestone is the **R3 exit gate**; no major new surface area should be added before it passes.

---

# 11. Handoff log

## Session: phase-0 + R1.1/R1.3/R2.1 first batch (post-0.8.0.dev0)

### Status updates

| Task | Status | Evidence |
| --- | --- | --- |
| R0.1 path isolation | **DONE** | `AppPaths` composed in `main()`/`Application.compose`; `GuiBase` accepts injected paths; `tests/test_phase1_paths.py` home-sentinel test proves zero writes beneath HOME; dashboard refresh runs with `paths=` injection |
| R0.2 CLI control flow | **DONE** | No duplicate branches (verified); exit codes 0/1/130 defined; parser wiring test covers 20 argv shapes; JSON-to-stdout policy in llm/status/doctor/bench/llamacpp branches |
| R0.3 editable install | **DONE** | `pip install -e . --no-deps --no-build-isolation` repairs resolution; `bc250_llm_mode.__file__` resolves to the source tree; CI installs `-e '.[test]'` as the primary model |
| R0.4 commit split | **DONE** | 8 commits: state-v5 foundation, catalog, chat, safety, runtime lifecycle, GUI refactor, CLI fixes, production/docs |
| R1.1 AppPaths integration | **PARTIAL** | Composition root + CLI/GUI/logging/state done; download/prepare/openwebui/env still read state strings (safe: values derive from the profile via `load_state_with_paths`) |
| R1.3 command audit | **DONE** | `docs/command_audit.md`: 109 sites classified (PROBE/CLEANUP/ELEVATED-MUTATION/SHELL-STAGING/FS-MUTATION); `elevated(` count frozen at 44 by guard test |
| R2.1 schema freeze | **DONE** | `docs/STATE_SCHEMA.md` field-ownership table; fixtures `tests/fixtures/state_v4.json`, `state_v5.json` with migration and round-trip tests |
| R2.2+ SQLite | NOT STARTED | Next milestone (0.9) |
| R3 operation engine | NOT STARTED | Blocked on R2 by design |

### Tests run this session

- `PYTHONPATH=. .venv/bin/pytest -q` → **206 passed**
- `.venv/bin/pytest -q` (editable install repaired) → **206 passed**
- Behavioral launcher test: executed a dummy binary, asserted full argv in one invocation
- Wheel built (`bc250_llm_mode-0.8.0.dev0-py3-none-any.whl`), installed into a clean venv; entry point and import verified
- `git diff --check` clean; working tree clean after commit split

### Schema / provenance notes

- JSON schema v5 is frozen (`docs/STATE_SCHEMA.md`); the R2.2 SQLite migration must consume these exact fixtures.
- Open WebUI pinned to `ghcr.io/open-webui/open-webui:v0.6.14`; release engineering must replace with an immutable digest.
- llama.cpp pin remains tag-form (`b7598`); full-commit identity deferred to R8.3.
- Elevation frozen at **44 call sites** pending the R5 privileged helper.

### Next task

**R2.2** (SQLite infrastructure) on a branch. Safest first test: migration round-trip over `tests/fixtures/state_v4.json` asserting every v5 key lands in its typed table.
