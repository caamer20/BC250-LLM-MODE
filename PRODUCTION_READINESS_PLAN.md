# BC250 LLM MODE production-readiness plan

## Purpose and relationship to the earlier plan

This document is a fresh production assessment of the current repository. It supersedes the priority ordering in `IMPLEMENTATION_PLAN.md`, while retaining useful technical work from that document. The earlier plan is primarily an engineering-hardening roadmap; this plan defines what “production ready” means, adds missing product capabilities, establishes release gates, and addresses operations over the full installation, upgrade, failure, and recovery lifecycle.

The target remains deliberately specific: AMD BC-250/GFX1013, Bazzite, approximately 12 GiB GPU UMA and 4 GiB host RAM, Vulkan llama.cpp, standard per-tensor GGUF models, local-only backends, and optional tailnet-only HTTPS. Production readiness does not mean broadening support to generic Linux, NVIDIA, Apple Silicon, Windows, fused/MAX models, public Funnel, or unattended BIOS changes.

## Current launch assessment

### Verified strengths

- The project has explicit safety invariants: one systemd-owned model server, fit-gated model/context/slot changes, desktop/no-LLM next boot, reversible tuning, and a mandatory risk acknowledgment.
- State writes are atomic and mode `0600`; migrations through schema v5 are tested.
- Model activation already has health-checked rollback.
- The current feature branch adds llama.cpp staging/rollback, thermal latching and profile restoration, secret-free Hugging Face token delivery, rotating setup logs, a split GUI package, richer chat, diagnostics, and a 24-model fit-aware catalog.
- Behavioral phase-0 tests now exercise the real launcher argv, CLI `llm` branch, exported GUI entry point, and deferred dashboard thermal import.
- The repository currently collects 185 tests. In this managed workspace, 184 pass and one dashboard test fails because its fixture leaves `logs_dir` at the real home default, causing a sandbox-denied write. That is test-isolation debt and must be fixed rather than waived.

### Production scorecard

| Area | Status | Production concern |
| --- | --- | --- |
| Core setup and service management | Yellow | Strong happy-path behavior, but incomplete crash recovery and cross-process coordination |
| Hardware safety | Yellow | Good validation and latching logic; watchdog is not yet a managed always-on current-boot service |
| State/data integrity | Red | Atomic JSON does not prevent lost updates between GUI, CLI, watchdog, and benchmark writers |
| Runtime security | Red | Tailnet API has no real application-level credential; Open WebUI uses host networking and a placeholder API key |
| Supply-chain security | Red | Open WebUI tracks `:main`; Fedora uses `latest`; model and llama.cpp provenance are not immutable enough |
| Upgrades and rollback | Yellow/Red | llama.cpp rollback exists; application, Open WebUI data/schema, catalog, and state upgrades lack a unified rollback contract |
| Observability/support | Yellow | Logs and doctor exist, but no structured events, support bundle, operation IDs, or local metrics history |
| GUI/CLI quality | Yellow | Broad functionality; dependency injection, cancellation, accessibility, and real-tkinter integration tests are incomplete |
| Packaging/release engineering | Red | No production installer/update channel, lockfile, CI release pipeline, signed artifacts, SBOM, or stable channel policy |
| Hardware validation | Red | Compatibility candidates and the known-good pin need recorded on-card evidence and sustained soak testing |

The application must not be labeled stable or production ready while any Red item remains or while a release-blocking test is failing.

## Production service objectives

These are release criteria, not telemetry promises to external customers. They should be measured in automated tests and repeated BC-250 qualification runs.

### Safety objectives

- Zero test cases in which setup, repair, update, rollback, crash recovery, or uninstall leaves the next boot target non-graphical or enables model auto-start.
- Zero accepted `NO-FIT` model/context/slot configurations.
- Thermal stop must occur within two watchdog intervals after a valid stop-threshold reading and remain latched until explicit reset.
- Every privileged host mutation must have a recorded inverse or be explicitly documented as non-reversible before confirmation.

### Reliability objectives

- At least 99 successful healthy starts out of 100 controlled starts for each release-validation model after warm caches, with every failure producing a classified error and recovery path.
- Model/context/slot, runtime, and application upgrades must preserve the last healthy configuration after injected failure at every transaction boundary.
- A 72-hour sustained-inference soak must complete without an unbounded log, state, memory, process, or temperature trend.
- State/configuration recovery after forced process termination must take less than ten minutes using documented in-app commands and must not require hand-editing JSON.

### Security objectives

- No credentials, conversation content, tailnet peer map, or sensitive environment values in argv, logs, state exports, support bundles, or crash messages.
- No unauthenticated model API outside loopback, including tailnet HTTPS.
- Every shipped container image, application artifact, runtime build, and curated model reference must resolve to immutable provenance.
- No privileged file operation may accept an arbitrary target from corrupted state.

### Usability objectives

- A new user can complete preflight, setup, first model install, first healthy generation, and return-to-desktop without a terminal.
- Every operation longer than two seconds shows its stage; every operation longer than thirty seconds exposes progress and a defined cancellation policy.
- The dashboard must always answer four questions: what is running, is it safe, what changed, and what will happen after reboot.

## Release tier model

Use explicit maturity levels so beta features cannot silently become production promises.

| Tier | Meaning | Required evidence |
| --- | --- | --- |
| Experimental | Hidden behind an advanced toggle; behavior or hardware compatibility is uncertain | Unit tests and explicit warning |
| Preview | User-visible but not recommended for unattended use | Integration test plus rollback path |
| Supported | Included in the stable release contract | Automated coverage, documentation, migration support, BC-250 validation |
| Hardware-validated | Model/runtime behavior measured on the reference BC-250 | Recorded environment, digest/commit, load, generation, thermals, and soak result |

Catalog entries, fast sync, custom clocks, autotune, and runtime upgrades must each carry a tier. The GUI and CLI should display it.

## Program 0 — rescue and freeze the current feature branch

### 0.1 Restore an honest green baseline

- Fix `test_dashboard_refresh_executes_thermal_import` by setting all application paths, especially `logs_dir`, inside `tmp_path`, or by injecting a no-I/O runner/logger. Tests must never write to the developer's real application directory.
- Remove the unreachable statements after the `llamacpp` CLI branch.
- Run all 185 tests from both the source tree and an installed wheel.
- Add `python -m compileall`, package import, `--help`, `--version`, and GUI-entry smoke checks.
- Execute a real-tkinter smoke test under Xvfb on Linux; inert widget stubs are not sufficient to certify GUI startup.
- Reconcile `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, both planning documents, parser help, and the actual thermal reset/update behavior.

### 0.2 Normalize the development baseline

- Create coherent commits for phase-0 repairs, GUI package split, thermal changes, llama.cpp staging, chat/catalog additions, and production tests. Preserve all current user work.
- Add a changelog entry describing state schema v5 and operational behavior changes.
- Bump the development version away from released `0.7.0`; do not let unreleased schema/API behavior present itself as the tagged version.
- Define branch protection and required checks before further features land.

Exit gate:

- Clean source and wheel tests pass with no workspace/home writes.
- No known entry-point regression remains.
- The large dirty feature pass is reviewable as bounded commits.

## Program 1 — introduce a production application core

The current dictionary passed between modules makes every surface responsible for validation, persistence, and rollback. Production behavior needs one transactional core used by GUI, CLI, chat management commands, watchdog, and future remote control.

### 1.1 Create explicit application paths and dependencies

Add an `AppPaths` value object containing state/database, models, staging, logs, backups, conversations, runtime manifests, and support-bundle paths.

- Construct it once from the selected installation profile and inject it into stores and services.
- Never compute `Path.home()` defaults at module import time.
- Distinguish the invoking desktop user from root/pkexec execution. Resolve ownership before privilege escalation and preserve that owner for user data.
- Validate every path against allowed roots and reject symlinks for privileged write/delete targets.
- Make tests use an isolated `AppPaths.temporary(tmp_path)` fixture.

This solves the current test failure and prevents a real root-run setup from accidentally creating a second configuration under root's home.

### 1.2 Replace mutable JSON state with a transactional repository

For v1.0, use SQLite from the Python standard library as the source of truth. It provides process-safe transactions, schema migrations, durable operation journals, append-only events, and bounded history without building a custom locking protocol.

Recommended tables:

- `settings`: validated current configuration and revision.
- `installed_models`: model identity, path, digest, provenance, validation tier, sampling profile.
- `operations`: transactional operation state and recovery data.
- `runtime_builds`: immutable llama.cpp build manifests and active/retained status.
- `events`: bounded operational/security/safety events with operation IDs.
- `benchmarks`: measured results plus environment fingerprint.
- `conversations`: metadata only; content remains in separately controlled files unless the user opts into database storage.

Implementation requirements:

- Migrate schemas 1-5 transactionally after making a timestamped, mode-`0600` JSON backup.
- Keep `state export` and `state import --dry-run` for support and disaster recovery.
- Use foreign keys, uniqueness constraints, typed adapters, and explicit migration versions.
- Use synchronous durability appropriate for power-loss safety; qualify the chosen journal mode on the Bazzite filesystem rather than assuming WAL behavior through every bind mount.
- Expose narrow repository methods; domain code must not execute arbitrary SQL.

### 1.3 Build a recoverable operation engine

Represent every multi-step change as a persisted state machine:

```text
planned -> preparing -> applied -> verifying -> committed
                         |             |
                         +-> rolling_back -> rolled_back / recovery_required
```

Each operation records:

- unique ID and type;
- baseline and candidate references;
- exact reversible steps completed;
- owning process and heartbeat;
- confirmation record;
- timestamps, progress, and last error;
- automatic and manual recovery actions.

Apply the engine to model activation, context/slot changes, optimization changes, model install/conversion, llama.cpp/Open WebUI/application updates, desktop/LLM transitions, sharing changes, and uninstall.

On startup, stale operations enter maintenance mode. The app should offer resume, rollback, inspect logs, or export support data; it must not launch another conflicting mutation.

### 1.4 Introduce a domain service facade

Create narrow services such as `RuntimeService`, `ModelService`, `SafetyService`, `SharingService`, `UpdateService`, and `DiagnosticService`. GUI and CLI should call the same methods and receive typed results/events.

- Remove backend orchestration from tkinter mixins and the terminal chat loop.
- Centralize risk/acknowledgment decisions in command metadata.
- Add one operation lock for mutually exclusive host/runtime changes while allowing read-only status calls.
- Make dry-run a first-class mode that validates and reports projected actions without mutating host or state.

Exit gate:

- Concurrent GUI, CLI, benchmark, and watchdog writes cannot lose data.
- Forced termination at any persisted operation phase produces deterministic recovery.
- GUI and CLI use the same transaction and authorization paths.

## Program 2 — establish a minimal privileged boundary

Today, many modules construct commands that become `sudo` or `pkexec` operations. A corrupted state file or future command-building bug has too much authority.

### 2.1 Replace general elevation with allowlisted privileged actions

- Create a small privileged helper with a versioned, declarative request schema.
- Permit only fixed actions: install/remove known unit files, set known systemd targets/masks, adjust approved kernel argument, write the known udev/sysctl/logrotate paths, manage approved service names, and update the known Cyan config keys.
- Validate caller UID, target path, regular-file/symlink status, expected previous content, and request version.
- Never accept a free-form shell command or arbitrary path.
- Return structured evidence describing what changed and the inverse action.
- Package a restrictive polkit policy so the desktop user authorizes defined operations, not a general shell.

### 2.2 Make host mutation plans auditable

- Before confirmation, show files, units, current values, proposed values, reboot impact, and rollback method.
- Write a signed or hashed local change receipt into the operation record.
- Detect external drift before rollback; do not overwrite a user/admin change made after the app's mutation without confirmation.
- Add `host changes` and `host revert --dry-run` commands.

Exit gate:

- No app-controlled route can elevate an arbitrary command.
- Every privileged integration test asserts both allowed actions and rejected tampered requests.

## Program 3 — complete safety and resource governance

### 3.1 Install a managed current-boot safety supervisor

Replace foreground `thermals watch` as the enforcement mechanism with a companion safety service or transient systemd unit.

- Start it only during an explicitly activated LLM session; stop it in desktop mode and uninstall; never enable it for the next boot.
- Monitor GPU temperature, sensor freshness, model-service state, host available memory, VRAM use, process restart rate, and disk/log pressure.
- Use monotonic time and consecutive-sample confirmation to reject sensor spikes.
- Preserve and restore the exact pre-throttle profile; keep thermal stop idempotently latched.
- Add an emergency fallback: if sensor access is lost while the server is loaded and safety monitoring was required, cap clocks conservatively or stop the server according to a user-visible policy.
- Deliver desktop and terminal notifications for throttle, degraded sensor, stop, recovery-required operation, and repeated server failure.

### 3.2 Add safe idle and maintenance behavior

New production features:

- Optional idle auto-stop after a configurable period with no active slots. Default off until hardware validated.
- Maintenance mode that drains/rejects new requests before model/runtime updates.
- Cooldown gate before restart after a thermal stop.
- Request-aware shutdown with a bounded grace period.
- Manual “safe stop now” action always available, even when another non-critical task is running.

### 3.3 Make resource limits adaptive

- Calculate `MemoryHigh` and `MemoryMax` from validated host RAM with conservative minimum desktop headroom.
- Add disk-space low/high watermarks for downloads, conversion, logs, runtime builds, Open WebUI data, and support bundles.
- Set container/service CPU, process, file-descriptor, and log limits appropriate to the BC-250.
- Detect zram and swap behavior and report advice; do not silently rewrite it outside explicit optimization operations.
- Record OOM/systemd termination reasons and prevent restart storms.

Exit gate:

- Safety enforcement survives GUI/terminal exit.
- A 72-hour soak shows bounded temperatures, logs, memory, disk use, and process count.
- Thermal, sensor-loss, OOM, and disk-full fault tests stop or degrade safely.

## Program 4 — secure the inference and sharing plane

Tailnet membership is a useful network boundary, but the model API should not rely on it as the only authorization control.

### 4.1 Add real API credentials

- Generate a strong per-install API key and store it using systemd credentials or a root/user-owned secret file outside general state exports.
- Pass it to llama.cpp through a non-argv secret mechanism supported by the selected runtime; if upstream only accepts argv, front the API with a local authenticated proxy and keep llama.cpp on a private loopback socket.
- Configure Open WebUI through a Podman secret or protected env file, not `OPENAI_API_KEY=sk-no-key-needed` in the container definition.
- Add key rotation, revoke, reveal-once/copy, and client configuration guidance.
- Support separate credentials for Open WebUI and direct API access if the proxy architecture permits it.
- Add rate limiting, request-size limits, timeouts, and connection limits at the authenticated gateway.

### 4.2 Harden Tailscale publishing

- Continue binding raw backends to loopback.
- Publish only the authenticated gateway and Open WebUI, never raw llama.cpp.
- Verify Tailscale Serve ownership before changing ports; preserve unrelated user Serve configuration.
- Fail closed if Funnel is present on managed endpoints and provide an explicit remediation preview.
- Optionally validate tailnet ACL/tag posture and show a warning when all tailnet users can reach the node.
- Add a remote-access self-test from a second tailnet node to the hardware release checklist.

### 4.3 Harden Open WebUI

- Replace `ghcr.io/open-webui/open-webui:main` with a tested version plus immutable digest.
- Replace host networking with a rootless/private Podman network and an explicit `127.0.0.1` port mapping where compatible.
- Drop unnecessary capabilities, enable `no-new-privileges`, set memory/PID limits, and document the required writable volume.
- Add explicit Open WebUI status, update, backup, rollback, and migration operations. Back up the data volume before any version requiring a database migration.
- Validate first-run authentication settings and prohibit accidental no-auth mode when tailnet publishing is enabled.

### 4.4 Privacy and data controls

New production features:

- Conversation retention policy: session-only, keep N days, or manual-only.
- One-click delete/export with a precise list of affected files.
- Redaction controls for support bundles and logs.
- A local privacy page stating what is stored and confirming there is no telemetry unless explicitly enabled in a future release.

Exit gate:

- Direct API requests without a valid credential fail on loopback gateway and tailnet HTTPS.
- Container/image updates are pinned, backed up, health checked, and reversible.
- Security tests prove no secrets reach argv/log/state/support exports.

## Program 5 — build a reproducible software and supply-chain lifecycle

### 5.1 Application installation and self-update

Add a supported installer rather than relying only on ad hoc `pip install .`:

- Install versioned application environments under the app directory and atomically switch a `current` pointer.
- Verify a signed release manifest, wheel hash, Python compatibility, and migration requirements before activation.
- Keep at least one previous application version and database backup.
- Start the new version in migration/doctor mode before committing the switch.
- Provide `app status`, `app check-update`, `app update`, and `app rollback`; updates remain explicit by default.
- Separate stable, beta, and development channels. Never auto-promote between channels.
- Support an offline signed update bundle for appliances without ongoing internet access.

### 5.2 Reproducible runtime/container builds

- Pin the Fedora image to a supported release/digest, not `fedora:latest`.
- Pin build dependencies or record exact resolved versions.
- Identify the known-good llama.cpp release by full immutable commit and build manifest, not only a tag prefix.
- Use versioned source/build directories and an atomic active pointer. State history must match physical retained builds.
- Include compiler, CMake options, Vulkan loader, Mesa/kernel, and binary hashes in the runtime manifest.
- Qualify runtime updates against current model load, deterministic generation, context/slots, and thermal/memory behavior.

### 5.3 Model and catalog supply chain

- Pin Hugging Face repository revisions and exact artifact names.
- Store expected size and SHA-256 when publishers provide it; always calculate a local digest after download.
- Add model license, usage restrictions, source, publisher, validation tier, and last-verified runtime commit to catalog metadata.
- Present license information before first download and persist acknowledgment when required.
- Generate README/catalog tables from executable metadata.
- Distribute catalog updates only inside signed application/catalog releases. Do not fetch and trust a mutable remote catalog at runtime.

### 5.4 Release artifacts and provenance

- Produce reproducible wheel/sdist where feasible, checksums, signatures, SBOM, dependency/license notices, and a machine-readable release manifest.
- Add `SECURITY.md`, vulnerability disclosure instructions, supported-version policy, and release signing key rotation/revocation process.
- Run dependency, secret, license, and static-analysis checks in CI with reviewed suppressions.

Exit gate:

- Application, Open WebUI, Fedora image, llama.cpp, and catalog/model references are immutable and attributable.
- Every update class has backup, health verification, rollback, and interrupted-operation recovery.

## Program 6 — production model and storage management

### 6.1 Introduce a content-addressed model store

- Stage downloads/conversions outside the active store.
- Verify the final GGUF, calculate its digest, then atomically place it under a digest-based path.
- Keep friendly model records as references so duplicate files are deduplicated.
- Exclude partial, temporary, f16, and failed artifacts from discovery.
- Detect external local-model replacement by inode/size/digest changes before launch.

### 6.2 Add a storage manager

New production features:

- Dashboard/CLI storage inventory showing models, source checkpoints, conversion intermediates, runtime builds, Open WebUI data, conversations, logs, backups, and reclaimable space.
- Safe cleanup preview with exact targets, total reclaimed bytes, retention constraints, and rollback limitations.
- Configurable low-space alerts and download/conversion refusal thresholds.
- Move/import model workflow between approved filesystems with digest verification and atomic registry update.
- Verify/repair registry action that finds missing files, duplicate digests, corrupted artifacts, and orphaned staging directories.

### 6.3 Add named workload profiles

New production features:

- Named profiles combining model, context per slot, slot count, KV type, batch/ubatch, sampling defaults, thread policy, governor profile, and optional idle-stop policy.
- Built-in Safe, Balanced, Throughput, Multi-user, Long-context, and Quiet profiles, each fit-checked against the selected model.
- User profiles are transactional and exportable.
- Profile activation uses maintenance/drain, health check, and rollback.
- Mark autotuned profiles with the exact environment fingerprint that produced them.

### 6.4 Improve model validation and lifecycle

- Separate metadata verified, runtime load tested, generation tested, and hardware validated states.
- Add model update/replace workflow preserving the previous digest until the new artifact passes activation.
- Add model quarantine when corruption, forbidden layout, repeated load failure, or digest drift is detected.
- Provide explicit uninstall model action that refuses to remove the active/rollback model until another healthy profile is selected.

Exit gate:

- Interrupted downloads/conversions never become selectable.
- Users can understand and safely reclaim storage.
- Profile activation and model replacement always retain a healthy rollback target.

## Program 7 — redesign diagnostics and observability

### 7.1 Structured operations and logs

- Add operation IDs to every command, GUI task, privileged action, service restart, and health check.
- Emit structured JSON events internally with stable event codes, severity, redacted fields, and human messages.
- Keep bounded rotating human logs and a bounded event table; avoid writing high-frequency temperature samples to the main configuration store.
- Correlate model-server logs with the activation/update operation that launched it.
- Add clock synchronization/timezone-safe UTC timestamps.

### 7.2 Local metrics and health history

New production features:

- Dashboard history for temperature, GPU clocks/utilization, VRAM, host available memory, server state, active slots, prompt/generation throughput, and restart count.
- Store downsampled bounded history locally; default retention should respect the 4 GiB host/disk constraints.
- Add status summaries for current, 15-minute, and session peak values.
- Optional localhost-only Prometheus endpoint behind an advanced toggle; never publish it automatically.
- Alerts for temperature, sensor loss, memory pressure, disk pressure, runtime drift, failed backups, and repeated unhealthy starts.

### 7.3 Turn doctor into a diagnostic framework

- Each check returns stable ID, severity, evidence, impact, remediation, and whether a safe automated fix exists.
- Cover hardware identity, UMA profile, disk, filesystem permissions, state/database integrity, incomplete operations, unit drift, boot policy, runtime manifest, container image pin, Vulkan, model digests, API auth, Tailscale/Funnel, Open WebUI auth, sensor freshness, and backups.
- Add `doctor --quick`, `doctor --full`, and `doctor --json`.
- `doctor --fix <check-id>` may apply only one explicit reversible fix after confirmation; no blanket fix-all for privileged changes.

### 7.4 Add a redacted support bundle

New production feature:

- Export app/runtime versions, architecture manifest, doctor report, redacted settings, unit status, recent bounded logs, model metadata/digests, operation history, and hardware profile.
- Exclude API keys, HF tokens, conversation content, tailnet peer map, and unnecessary identity/address data.
- Show bundle contents before creation and add an automated secret canary test.

Exit gate:

- Every release-blocking failure can be classified from doctor/support data without asking users to expose secrets.
- Metrics/log storage remains bounded during the 72-hour soak.

## Program 8 — deliver a production operations UX

### 8.1 First-run and recovery experience

- Add a preflight summary before any change: hardware, BIOS split, cooling acknowledgment, disk, network, dependencies, planned privileged actions, and estimated setup time/space.
- Make every wizard phase resumable from persisted operation state, not only a numeric step.
- On interrupted setup/update, launch directly into a recovery screen with safe choices.
- Provide offline-install instructions and detect when network-dependent steps cannot proceed.
- Add a final verification report and exportable setup receipt.

### 8.2 Dashboard improvements

New production features:

- Top-level safety banner with thermal state, sensor freshness, next-boot policy, API exposure/auth status, and incomplete operation status.
- Runtime card with model/profile, context per slot and total, slots in use, runtime build, validation tier, and one-click safe stop.
- Storage manager, update center, backup status, alerts, and recent operation timeline.
- Search/filterable logs and diagnostic recommendations.
- Copy buttons for local and tailnet API endpoints with authenticated client examples that never reveal secrets unless explicitly requested.

### 8.3 Task and cancellation model

- Every background task has a unique ID, stage, progress, cancelability, and recovery policy.
- Disable all conflicting controls while allowing safe stop and read-only inspection.
- Confirm window close during a mutation; continue safe service-owned tasks or cancel only at defined checkpoints.
- Ignore callbacks from superseded/destroyed views and reload repository state before rendering results.
- Add desktop notifications for long operation completion and safety events.

### 8.4 Accessibility and localization readiness

- Keyboard-complete navigation, visible focus, scalable fonts, high-contrast compatibility, descriptive labels, and no color-only FITS/TIGHT/NO-FIT meaning.
- Keep user-visible strings centralized even if translation is not a v1.0 requirement.
- Test minimum supported screen size and long/error text wrapping.
- Add real tkinter tests under Xvfb and a manual accessibility checklist on Bazzite KDE.

### 8.5 CLI consistency

- Split the monolithic dispatcher into typed domain handlers.
- Add stable `--json` output envelopes and documented exit codes.
- Add `--dry-run`, `--yes` only for explicitly safe non-destructive operations, and interactive confirmation for destructive actions.
- Ensure every read-only command works before acknowledgment and on partially configured systems.
- Add shell completion generation for Bash and Zsh.

Exit gate:

- GUI and CLI produce equivalent operations, safety gates, progress, results, and recovery behavior.
- A non-expert can recover from interrupted setup/update using only the app.

## Program 9 — improve chat and inference operations

### 9.1 Harden terminal chat

- Implement a correct SSE parser for partial frames, error payloads, disconnects, and completion reasons.
- Preserve partial responses with an interrupted marker and allow explicit retry.
- Use llama.cpp tokenization for context budgeting with a conservative fallback.
- Version conversation files and write them atomically.
- Add model/profile metadata, timestamps, and optional retention policy to saved sessions.
- Make reasoning visibility and persistence explicit; hiding `<think>` in display must not mislead users about what is saved.

### 9.2 Add session and workload controls

New production features:

- Named system-prompt presets and per-profile sampling defaults.
- Session inspector showing estimated tokens, cache reuse, active model/profile, and remaining context.
- Export to Markdown and structured JSON with redaction options.
- Queue/load status and a clear “server busy” experience instead of indefinite waits.
- Optional request timeout and maximum generation limits per client/profile.

### 9.3 Make benchmarks scientifically useful

- Add warmup, deterministic sampling, fixed prompts, cooldown, and environment fingerprinting.
- Measure prompt throughput, generation throughput, first-token latency, peak VRAM, temperature rise, and errors.
- Compare candidates against baseline with a minimum meaningful improvement threshold.
- Abort/reject results when thermal throttling, memory pressure, runtime drift, or sensor loss occurs.
- Restore baseline in `finally` and through the operation journal after interruption.
- Provide benchmark history comparison/export and invalidate stale results after model/runtime/driver/profile changes.

Exit gate:

- Chat failures preserve user work and do not corrupt session history.
- Autotune cannot leave an unverified candidate active after interruption.

## Program 10 — backup, restore, and disaster recovery

Production appliances need a recovery story independent of a working GUI.

### 10.1 Add backup sets

New production feature:

- Back up the database/config, app/runtime manifests, custom profiles, model registry/digests, conversation metadata/content according to user choice, Open WebUI data volume, and host-change receipts.
- Do not copy multi-gigabyte model files by default; include an optional model-data tier and always include enough provenance to redownload/verify them.
- Produce a versioned manifest with checksums and required application version.
- Support local path and user-mounted external drive targets; avoid embedding cloud credentials in v1.0.

### 10.2 Restore safely

- `backup verify` checks manifest, checksums, version compatibility, free space, and path ownership.
- `restore --dry-run` shows migrations, conflicts, missing models, host changes that will not be restored automatically, and rollback plan.
- Restore into staging, migrate, validate, and atomically activate.
- Never restore boot-target, kernel-argument, or tailnet identity changes blindly from another machine.
- Add bare minimum emergency CLI recovery using only Python standard-library dependencies.

### 10.3 Factory reset

New production feature:

- Separate reset settings, reset app/runtime, remove managed models, remove Open WebUI data, and full uninstall.
- Display exact targets and recoverability for each tier.
- Require typed confirmation for irreversible model/data removal.
- Verify desktop boot and host rollback before deleting the final recovery metadata.

Exit gate:

- A backup from the previous supported release restores successfully onto a clean supported installation.
- A failed restore preserves the original working state.

## Program 11 — testing and qualification

### 11.1 Automated test layers

1. Pure unit tests for fit math, schemas, operation transitions, safety decisions, command metadata, redaction, and presenters.
2. Component tests with fake runners, fake repositories, fake clocks, and deterministic failure injection.
3. Executed boundary tests using dummy binaries for launcher/gateway argv, temporary SQLite databases, temporary filesystems, and real subprocess signals.
4. Package tests from wheel/sdist in clean Python environments.
5. Real tkinter tests under Xvfb.
6. Container integration tests for pinned Fedora/Open WebUI images where network is available.
7. Disposable VM tests for systemd/polkit/unit installation and rollback.
8. BC-250 hardware-in-loop tests for Vulkan, thermal, memory, performance, boot, and Bazzite integration.

### 11.2 Required failure matrix

Inject failure before and after every externally visible commit point:

- process kill, SIGINT, and system power loss approximation;
- disk full and filesystem read-only;
- permission/polkit denial;
- network loss, HF rate limit, partial download, and checksum mismatch;
- corrupted state/database/model/build/container volume;
- missing Podman/Distrobox/systemd/Tailscale/Vulkan/sensor;
- service timeout, wrong served model, wrong context, OOM kill, restart storm;
- failed rollback and external drift after app mutation;
- Bazzite/kernel/Mesa update changing hardware/runtime behavior.

### 11.3 Compatibility matrix

Record supported combinations rather than claiming “Bazzite” generically:

- Bazzite image/release and desktop variant;
- kernel and Mesa/Vulkan versions;
- Python versions used by the packaged app;
- Podman and Distrobox versions;
- root and desktop-user installation mode if both remain supported;
- Open WebUI version/digest;
- known-good llama.cpp commit;
- BIOS UMA profile and reference cooling configuration.

### 11.4 Soak and performance gates

- 72-hour sustained inference with periodic requests and metrics capture.
- Repeated cold/warm start cycles.
- Concurrent slot saturation and queue recovery.
- Low-disk, high-log, and memory-pressure tests.
- Thermal threshold test with exact baseline restoration.
- Bazzite OS update followed by reboot, doctor, model start, generation, and desktop return.

Exit gate:

- All mandatory matrix rows pass or are explicitly removed from the supported contract.
- Release evidence is archived with logs, manifests, model digests, and environment fingerprint.

## Program 12 — CI/CD and release governance

### 12.1 Pull-request pipeline

- Formatting/lint, targeted strict typing, unit/component tests, coverage on safety-critical modules, compile, package build/install, GUI Xvfb smoke, documentation/catalog consistency, secret scan, dependency/license scan, and migration fixtures.
- Require review for privileged helper, state migration, security gateway, boot policy, model fit math, and update engine changes.
- Treat generated files as reproducible and fail on uncommitted regeneration drift.

### 12.2 Release pipeline

- Build in a controlled Linux environment.
- Generate signed artifacts, checksums, SBOM, release manifest, third-party notices, changelog, migration notes, and rollback instructions.
- Promote the exact artifact through development, beta, release candidate, and stable; never rebuild between stages.
- Stable promotion requires automated gates, disposable-VM gates, and signed BC-250 hardware evidence.
- Publish support lifetime, compatible schema range, and known issues.

### 12.3 Operational governance

- Define severity levels and a security-response process.
- Maintain release and runtime pin cadence without automatic upgrades.
- Establish deprecation policy for schema, profiles, catalog IDs, and command JSON.
- Maintain a tested rollback path to the previous stable release.

Exit gate:

- A release can be reproduced, verified, installed, upgraded, rolled back, and supported from its published artifacts alone.

## v1.0 feature commitment

### Must ship for production

- Transactional repository and operation recovery.
- Explicit `AppPaths`/ownership handling.
- Managed safety supervisor with thermal, memory, disk, and restart-storm protection.
- Minimal allowlisted privileged helper/polkit boundary.
- Authenticated API gateway and hardened, pinned Open WebUI.
- Signed application update/rollback and immutable runtime/container pins.
- Model provenance/digests, transactional installs, storage manager, and named workload profiles.
- Backup/verify/restore and tiered factory reset.
- Structured doctor, support bundle, alerts, and bounded metrics history.
- GUI task/recovery model, real-tkinter tests, accessibility basics, stable CLI JSON/exit codes.
- CI, package/release signing, SBOM, compatibility matrix, failure injection, 72-hour soak, and BC-250 hardware evidence.

### Should ship in v1.0 if it does not delay safety gates

- Idle auto-stop.
- Desktop notifications.
- Shell completions.
- Offline application/runtime/model manifest bundle.
- Open WebUI backup/update UI.
- Benchmark comparison dashboard.
- Localhost-only Prometheus export.

### Post-v1.0 candidates

- Secure remote administration beyond current Tailscale endpoints.
- Multiple BC-250 node coordination.
- Signed optional catalog packs.
- Additional UI localization.
- Opt-in anonymous reliability telemetry with a separate privacy review.

These post-v1.0 items must not expand the v1.0 attack surface or distract from recovery and hardware safety.

## Delivery sequence

### Release 0.8 — stabilized beta

- Complete Program 0.
- Finish the current feature pass and document experimental/preview tiers.
- Ship no new remote exposure.

Gate: isolated green tests, clean package install, known defects closed.

### Release 0.9 — production architecture preview

- Complete Programs 1–3: paths, transactional repository, operation engine, privileged boundary, and managed safety supervisor.
- Migrate existing users with backup and rollback.

Gate: concurrency, interruption, thermal, host rollback, and reboot-safety evidence.

### Release 0.10 — secure lifecycle release candidate

- Complete Programs 4–7: authenticated sharing, pinned Open WebUI, signed update lifecycle, model/storage/profiles, diagnostics and metrics.
- Begin full compatibility and soak qualification.

Gate: security review, immutable provenance, update/restore failure matrix, no Red scorecard items.

### Release 0.11 — UX and disaster-recovery release candidate

- Complete Programs 8–10: production GUI/CLI, chat/benchmark hardening, backup/restore/factory reset.

Gate: end-to-end new install, interrupted recovery, upgrade, backup restore, and uninstall by a non-developer tester.

### Release 1.0 — production stable

- Complete Programs 11–12 and all Must-ship commitments.
- Freeze supported Bazzite/runtime matrix and publish evidence.

Gate: signed release artifacts, all automated/VM/hardware tests, 72-hour soak, OS-update test, zero unresolved critical/high defects, tested rollback to the prior stable release.

## Go/no-go checklist

A production launch is **No-Go** if any answer below is no:

- Are all tests isolated, passing from the installed artifact, and required in CI?
- Can every multi-step mutation resume or roll back after process or power interruption?
- Is the next boot always graphical with no LLM auto-start after every tested path?
- Does the safety supervisor run independently of the GUI and stop safely on thermal/sensor/memory failures?
- Are all remote endpoints authenticated and all raw backends loopback-only?
- Are every app, image, runtime, and model reference immutable and verified?
- Can a user back up, verify, restore, and roll back without editing state manually?
- Are privileged actions narrowly allowlisted, previewable, and reversible?
- Are logs, metrics, and support bundles bounded and secret-free?
- Has the exact release artifact passed the compatibility matrix, failure matrix, 72-hour soak, and BC-250 hardware gate?
- Are version, migration, support, security, rollback, and known-issue documents published?

Only after all answers are yes should the README remove or soften the public-beta warning.
