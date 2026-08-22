# BC250 LLM MODE improvement plan

## Objective

Move the current `v0.7.0` development tree from a feature-rich beta into a dependable BC-250 appliance manager. The next release should be safe under interrupted operations, predictable across GUI and CLI surfaces, diagnosable without exposing secrets, and validated on the actual BC-250/Bazzite target.

The project should remain deliberately narrow. This plan does not generalize it to unrelated GPUs or operating systems, enable public internet exposure, automatically change BIOS settings, relax the supported 12/4 UMA split, or allow fused/MAX models or `--no-mmap`.

## Baseline and findings

As of this plan, `main` is still at `v0.7.0` (`46bedc6`) with a large uncommitted feature pass. The work includes a 24-model catalog, richer chat and benchmark features, thermal/autotune modules, llama.cpp pin/update/rollback support, production-hardening tests, schema v5, and a split `gui/` package. `PYTHONPATH=. .venv/bin/pytest -q` passes all 174 collected tests.

That passing suite is not yet a release signal. The following concrete runtime defects must be treated as blockers:

1. `bc250_llm_mode/__main__.py` uses undefined `console` and `action` names in the real `llm` CLI branch. It should dispatch with `args.action` and emit normal JSON.
2. `server.generate_launcher()` omits shell continuations before the new thread and cache flags. The launcher can start `llama-server` without those flags and then try to execute `--threads` as a separate command.
3. `gui/app.py:run_gui()` references `Wizard`, which is defined in `gui/__init__.py`, not in `gui/app.py`'s globals. The public GUI launch path can therefore raise `NameError`.
4. `gui/dashboard.py` imports `.thermals` from inside the `gui` package; the real module is `..thermals`. Dashboard refresh can fail even though the stub-based GUI contract test passes.
5. Thermal throttling overwrites the configured GPU ceiling with the temporary cap. On recovery, the code reads the already-capped value and cannot reliably restore the user's original profile.
6. A latched thermal stop returns `stop` on every poll, so the watchdog can repeatedly invoke `stop_service` instead of treating the stopped state as an idempotent hold.
7. The llama.cpp updater changes the source checkout before the staged build is proven. A pre-swap build failure can leave source metadata on the failed target while the old binaries remain active. State can claim up to five historical builds even though only one physical `build-backup` is retained.

The implementation should preserve the existing dirty work and land it in reviewable slices. Do not reset or replace the current tree.

## Priority and sequencing

| Priority | Outcome | Depends on |
| --- | --- | --- |
| P0 | Stabilize the current feature pass and make tests exercise actual entry points | Nothing |
| P1 | Introduce concurrency-safe state and recoverable operation transactions | P0 |
| P1 | Make thermal and host safety enforcement persistent and reversible | State transactions |
| P1 | Harden server launch, lifecycle, and health semantics | P0, state transactions |
| P1 | Make llama.cpp and model updates reproducible and crash-safe | Runtime hardening |
| P2 | Simplify CLI/GUI architecture and improve long-operation UX | Stable service layer |
| P2 | Improve benchmarking, recommendations, and operational telemetry | Stable runtime and state |
| P2 | Add CI, packaging, Bazzite integration, and BC-250 hardware release gates | All P0/P1 work |

## Phase 0 — stabilize and checkpoint the current branch

### 0.1 Fix the known entry-point regressions

Files: `bc250_llm_mode/__main__.py`, `bc250_llm_mode/server.py`, `bc250_llm_mode/gui/app.py`, `bc250_llm_mode/gui/dashboard.py`.

- Replace the `llm` branch's copied Rich-console logic with `result = actions[args.action]()` followed by `store.save(state)` only when the action mutates state, then `print(json.dumps(result, indent=2))`.
- Do not require safety acknowledgment for read-only `llm status`; continue requiring it for start, stop, restart, and ensure if those operations are considered system-changing.
- Repair launcher continuations immediately. Then replace substring-only tests with a behavioral launcher test: generate a temporary state, substitute a dummy `llama-server` executable that records argv, run the launcher, and assert every expected flag belongs to the one `exec` invocation. `bash -n` should remain a supplemental check because it cannot detect flags executed as separate commands.
- Move `run_gui` into `gui/__init__.py` after `Wizard` is defined, or make `app.run_gui` accept/inject the composed class. Add a test that patches `Wizard.mainloop` and calls the exported `run_gui` function.
- Correct dashboard package-relative imports and exercise `_refresh_dashboard` with the headless stubs so deferred imports are not invisible to tests.
- Remove unreachable code after the `llamacpp` branch and add branch-level CLI tests for every parser action.

Acceptance criteria:

- `bc250-llm-mode llm status` returns valid JSON with no acknowledgment requirement and no undefined names.
- The generated launcher delivers thread, cache reuse, and defragmentation flags to the dummy executable in one argv.
- `from bc250_llm_mode.gui import run_gui; run_gui(...)` reaches `mainloop` under a stub.
- A dashboard refresh reaches thermal sampling without import errors.

### 0.2 Establish a clean test and packaging baseline

Files: `pyproject.toml`, tests, optional CI configuration.

- Refresh the editable environment so plain `.venv/bin/pytest` imports the source tree; retain `PYTHONPATH=.` in CI only as a defensive check, not as the primary installation model.
- Add clean-wheel and editable-install smoke tests in temporary virtual environments. Test `--version`, `--help`, module execution, package data, and GUI import.
- Add `python -m compileall`, Ruff formatting/linting, and a targeted type-checking baseline. Start type checking with state/catalog/server/model-manager modules rather than suppressing the entire existing application.
- Add ShellCheck for generated shell fixtures or eliminate the generated shell in Phase 3.
- Split the dirty work into coherent commits: GUI package split, catalog/chat features, thermal/autotune, production hardening, and llama.cpp lifecycle. Keep tests with their implementation.
- Update `AGENTS.md` so its working-tree inventory reflects the current GUI split, schema v5, architecture document, and round-5 tests.

Acceptance criteria:

- A clean checkout can build a wheel and sdist and run the suite from the installed wheel.
- Formatting, lint, compile, unit tests, and package smoke tests are one documented command or CI workflow.
- The release branch has no known P0 defect hidden behind a stub or substring assertion.

## Phase 1 — make state and operations transaction-safe

Atomic replacement protects against partial JSON writes, but it does not prevent lost updates when GUI polling, a background watchdog, and a CLI process save different in-memory copies.

### 1.1 Add a typed, validated state boundary

Files: `state.py`, a new `state_models.py` or equivalent, callers that directly mutate state.

- Define the complete schema with typed structures for installed models, optimization settings, benchmark records, build records, and active operations. A dataclass/`TypedDict` plus explicit validation is sufficient; avoid a heavy runtime dependency unless it materially improves migration safety.
- Validate types, ranges, enum values, and path fields on load and before save. Preserve unknown future keys only through an explicit compatibility policy.
- Back up the last known-good state before a schema migration. If parsing or validation fails, preserve the corrupt file, emit a clear recovery path, and never silently reset safety acknowledgments or rollback records.
- Add a monotonically increasing `revision` and timestamps using UTC ISO-8601 values rather than date-only strings.

### 1.2 Prevent concurrent lost updates

- Add a `StateStore.transaction(mutator, expected_revision=None)` API that takes an advisory file lock, reloads current state, applies a narrow mutation, validates, increments the revision, and atomically saves.
- Replace long-lived whole-state saves in GUI polling, thermal checks, benchmark history, and update flows with narrow transactions.
- On a revision conflict, merge append-only histories safely and require an explicit retry for mutually exclusive configuration changes.
- Reload state before every mutating CLI command and before GUI actions that may have been preceded by external CLI changes.

### 1.3 Add an operation journal

- Introduce an `active_operation` record with operation ID, kind, phase, baseline snapshot, candidate snapshot, start time, and recovery hint.
- Use consistent phases: `planned`, `applied`, `verifying`, `committed`, `rolling_back`, `failed`.
- Apply it first to model/context/slot activation, then llama.cpp update, autotune, environment setup, and host optimization changes.
- On startup and in `doctor`, detect incomplete operations and offer deterministic resume or rollback. Never guess when both source and target states are ambiguous.
- Add failure-injection tests at every state save, directory swap, service restart, and health-check boundary.

Acceptance criteria:

- Concurrent benchmark history and dashboard saves cannot erase each other.
- Killing the process after a candidate save or binary swap leaves enough information for `doctor` to restore or finish the operation.
- Every migration from schemas 1–5 remains tested, and a corrupt state file is preserved for recovery.

## Phase 2 — enforce BC-250 safety continuously

### 2.1 Turn the thermal watchdog into a managed runtime component

Files: `thermals.py`, `optimize.py`, `server.py` or a new service-management module, setup/uninstall paths.

- Install a companion `bc250-llm-thermal.service` or transient current-boot unit. It must be disabled for the next boot, start only during an explicitly activated LLM session, and stop during desktop mode/uninstall.
- Keep the pure hysteresis function, but represent actions as typed events: `none`, `throttle`, `hold`, `recover`, `stop`, `latched`.
- Persist the original governor profile and max clock in a dedicated watchdog baseline before throttling. Temporary caps must not overwrite user configuration.
- Make recovery restore the exact saved baseline and clear it only after successful governor application.
- Make thermal stop idempotent. Once latched, polling should report `latched` without repeatedly stopping the service. Add an explicit `thermals reset` command that requires safe temperature and human intent; restarting the model may also reset only after confirmation.
- Detect missing/stale sensors and impossible readings. A configured watchdog with no valid sensor should raise a prominent degraded-safety status, not quietly return `no-sensor` forever.
- Record temperature, action, configured thresholds, and clock ceiling in a bounded event history without high-frequency state-file writes.

### 2.2 Harden all host tuning rollback paths

- Capture the exact original Cyan governor config, swappiness, service enabled/active states, and installed unit/logrotate state before first mutation.
- Use a single optimization transaction. If any sub-step fails, restore already-applied sub-steps in reverse order and report both primary and rollback errors.
- Validate backup ownership, mode, regular-file status, and expected config sections before privileged copies. Reject symlinks and paths outside the fixed allowlist.
- Reinstall/update the model systemd unit when settings that affect `_service_text` change; merely saving `safeguards_enabled` must not leave a stale unit without memory guards.
- Derive memory limits from the validated host memory profile within conservative bounds instead of assuming every supported machine has exactly the same usable host memory.

Acceptance criteria:

- A throttle/recover cycle restores the pre-throttle clock profile exactly.
- A stopped watchdog performs one service stop and remains latched until explicit reset.
- Reboot and desktop-mode tests prove no watchdog or model service is enabled for the next boot.
- Failure at any optimization sub-step restores all preceding host changes.

## Phase 3 — harden the model server runtime

### 3.1 Replace positional launcher configuration

The current `CFG[0]` through `CFG[15]` interface is brittle and shell-sensitive.

- Prefer a small Python launcher executed inside the container. It should load state, validate a named runtime configuration, construct a list of arguments, set Vulkan environment variables, and use `os.execvpe` without shell interpolation.
- If a shell launcher is retained temporarily, generate a separate validated config file and shell-quote every value. Reject newline/NUL characters in paths and aliases.
- Do not emit `--threads 0`; omit thread flags when detection fails and let llama.cpp choose its default.
- Centralize llama.cpp capability/version checks so flags such as `--cache-reuse` and `--defrag-threshold` are only emitted for builds that support them.
- Unit-test argv generation as pure data, then execute a dummy binary in an integration test.

### 3.2 Clarify service lifecycle and health contracts

- Define one service state model: absent, stopped, starting, healthy, unhealthy, stopping, failed.
- Make `ensure_server` idempotent and distinguish installation failures, model load failures, API failures, and thermal lockout.
- Have health checks verify `/health`, served model identity, total context, slots, and a bounded test generation when a runtime/build update needs stronger validation.
- Use explicit startup and stop timeouts. Include systemd's result, exit code, and recent diagnostic log guidance in failures.
- Refresh the generated launcher and unit before every controlled restart when configuration affecting either has changed.
- Evaluate additional systemd protections on Bazzite (`TimeoutStopSec`, `KillMode`, restart limits, resource accounting). Apply only settings compatible with Podman/Distrobox and the root/non-root deployment variants.

Acceptance criteria:

- Every model server start is owned by `bc250-llm.service`; no GUI/chat path starts a competing process.
- Runtime settings become one validated argv and are observable in a dry-run/diagnostic report.
- A wrong model alias, wrong context, unhealthy endpoint, or thermal lockout produces a distinct actionable error.

## Phase 4 — make llama.cpp updates reproducible

Files: `env.py`, state schema, CLI/GUI runtime card, update tests.

- Separate source checkout identity from active binary identity. Record tag, full commit, build options, compiler version, Fedora image digest, build timestamp, and a hash of the active binaries or build manifest.
- Build from a detached staging worktree/clone without changing the active source checkout. A failed fetch or compile must leave both active source and active binaries untouched.
- Use versioned build directories such as `builds/<commit>/` and an atomically replaced `current` symlink. Keep an explicit bounded retention count that matches what actually exists on disk.
- Verify that a requested tag resolves to the expected commit. For the shipped known-good release, store the expected full commit in the application release rather than trusting a mutable tag name alone.
- Run binary smoke tests, Vulkan visibility, current-model load, API health, and a short deterministic generation before committing the new active build.
- Make rollback select from physically present builds and reconcile state from disk. If state and disk disagree, `doctor` should report the mismatch and avoid destructive cleanup.
- Preserve the active build if the model server is intentionally stopped; validate with `llama-server --version` and smoke checks, then defer model health until the next start.

Acceptance criteria:

- Failures during fetch, compile, swap, restart, or state commit leave the previous build selectable and accurately recorded.
- History count equals retained build directories.
- The pinned build is identified by immutable commit plus build manifest, not a tag prefix alone.

## Phase 5 — strengthen model acquisition, provenance, and preparation

### 5.1 Pin catalog artifacts

- Extend `ModelEntry` with repository revision/commit, expected filename, expected size, source URL, verification status, and optional SHA-256.
- Prefer exact filenames and revisions over broad globs. Conversion entries should pin every source file and tokenizer/config revision.
- Add a catalog validation tool that checks uniqueness, artifact restrictions, fit metadata, context limits, and documentation table generation without downloading multi-gigabyte files.
- Generate the README model table from catalog data to eliminate manual drift.

### 5.2 Make installs resumable transactions

- Write downloads into an operation-specific staging directory. Validate available space for partial download, final artifact, conversion intermediates, and rollback margin.
- Record repository revision and downloaded file metadata. Verify SHA-256 whenever available; otherwise calculate and store a local digest so later corruption can be detected.
- Atomically move only a verified final GGUF into the managed model directory, then register it in state.
- On cancellation or failure, retain safe resumable files but mark them clearly as incomplete and exclude them from discovery.
- Add explicit cleanup commands that show reclaimed space before deleting intermediates.
- Ensure the private Hugging Face token env file is created on a filesystem with mode `0600`, never logged, and always removed. Add tests for newline-containing tokens and runner exceptions.

### 5.3 Improve model compatibility validation

- Add a lightweight `llama-server` or `llama-cli` metadata/load probe after GGUF checks and before activation.
- Store validation status (`metadata-only`, `load-tested`, `generation-tested`, `BC-250-validated`) and the llama.cpp commit used.
- Keep compatibility candidates visually distinct from hardware-validated models in CLI and GUI recommendations.
- Calibrate fit estimates from measured weights, KV allocation, runtime overhead, and slot behavior while retaining a conservative floor. Never let measured optimism bypass the hard 12 GiB safety ceiling.

Acceptance criteria:

- An installed model has immutable provenance, a digest, preparation history, and compatibility status.
- Interrupted downloads/conversions never appear as selectable models.
- README catalog data cannot diverge from executable catalog metadata.

## Phase 6 — simplify CLI and service APIs

The CLI dispatcher is large and has already suffered copy/paste defects.

- Split parser construction from command handlers. Use one handler per domain: diagnostics, model management, server lifecycle, sharing, tuning, and runtime updates.
- Create a shared `ApplicationServices` object containing `StateStore`, runner, and domain services. This makes handlers independently testable without broad monkeypatching.
- Standardize result envelopes: `ok`, `command`, `data`, `warnings`, and optional `error`. Preserve human-readable defaults where useful, with a stable `--json` mode for automation.
- Define exit codes for invalid usage, unsupported hardware, unsafe fit, permission failure, unavailable dependency, health failure, and interrupted operation. Keep `KeyboardInterrupt` at 130.
- Require acknowledgment based on mutation risk, not command group. Status, search, recommendations, logs, and doctor should always be readable.
- Add `--dry-run` to privileged configuration paths. It should show validated commands/files and projected state changes with secrets redacted.
- Make `doctor` a set of independent checks with severity, evidence, and remediation. Add `doctor --fix` only for individually enumerated, reversible fixes with confirmation.

Acceptance criteria:

- Every parser action has a direct handler test and a CLI integration test.
- Read-only commands work on partially configured systems.
- Automation can rely on stable JSON and exit-code contracts.

## Phase 7 — improve GUI structure and long-operation UX

### 7.1 Finish the GUI package split cleanly

- Remove copied, unused domain imports from mixin modules. Mixins should call a narrow application/service facade rather than importing nearly the whole backend.
- Replace the frozen monolith method-name test with behavior-oriented contracts for navigation, state rendering, action dispatch, and error handling. Keep a small public API compatibility test only where external callers exist.
- Move pure formatting and form logic into presenters/view-models that are testable without tkinter stubs.
- Add one Linux/Xvfb smoke test with real tkinter to catch package-relative imports, widget option mistakes, and event-loop errors that inert stubs cannot detect.

### 7.2 Make background work controllable

- Represent each background task with ID, description, progress, cancellation policy, and result. Ignore callbacks from superseded tasks.
- On window close, confirm when a non-cancelable privileged operation is active; do not silently abandon a thread midway through a host mutation.
- Add stage-level progress for downloads, conversion, builds, health waits, and autotune. Stream log output while retaining a concise status summary.
- Disable all controls that conflict with the active operation, not only the Continue button.
- Reload state after a worker completes before rendering the dashboard so external CLI changes and worker mutations are merged.

### 7.3 Improve operational clarity

- Separate status, actions, and destructive/reversible maintenance into distinct dashboard sections.
- Show context as “per slot” and total reserved context together. Show the exact weights/KV/overhead breakdown behind FITS/TIGHT/NO-FIT.
- Surface thermal latch, missing sensor, stale unit, build drift, incomplete operations, and non-validated models as actionable banners.
- Add confirmation text that explains impact and rollback for model installation, context/slot changes, llama.cpp update, desktop transition, and cleanup.
- Improve keyboard navigation, focus order, text scaling, and screen-reader labels using standard tkinter/ttk capabilities.

Acceptance criteria:

- Real tkinter starts under Xvfb and all wizard/dashboard routes render.
- Closing the app during an operation has defined behavior and cannot silently corrupt state.
- A user can tell what is running, what will happen next boot, whether safety monitoring is active, and how to recover from the current screen.

## Phase 8 — improve chat, benchmarks, and autotuning

### 8.1 Chat reliability

- Make SSE parsing tolerant of split/malformed events, explicit server error payloads, and disconnects. Preserve partial assistant output with an interrupted marker.
- Keep raw reasoning text only when explicitly desired; document privacy implications of saving/exporting conversations.
- Replace the rough character-based context estimate with llama.cpp tokenization when available, with the existing estimator as a conservative fallback.
- Save conversation metadata: model, context, system prompt, sampling overrides, creation/update time, and schema version.
- Add atomic conversation writes and collision-safe names.

### 8.2 Rework autotune as a controlled experiment

- Require a healthy server, valid temperature sensor when thermal safety is enabled, and a starting temperature below a configurable limit.
- Preserve an immutable baseline in the operation journal and restore it in `finally` on cancellation, interrupt, or unexpected exception.
- Add warm-up runs, deterministic prompts, fixed sampling, cooldown between candidates, and randomized candidate order to reduce thermal/order bias.
- Compare prompt and generation throughput separately and reject candidates with errors, thermal throttling, memory pressure, or materially worse latency.
- Store model digest, llama.cpp commit, driver/kernel versions, context, slots, temperature range, and benchmark parameters with results.
- Apply a winner only when it exceeds baseline by a meaningful threshold; otherwise keep the baseline.
- Expose benchmark history and autotune comparison in the dashboard with a reset/export option.

Acceptance criteria:

- Interrupting autotune always restores the baseline runtime settings and a healthy service where possible.
- Results are reproducible enough to explain why a winner was selected.
- Historical results are invalidated or clearly marked when model, runtime build, driver, context, or slot count changes.

## Phase 9 — diagnostics, security, and supportability

- Add a redacted support bundle containing version, state schema and non-secret settings, hardware/memory profile, unit status, runtime build manifest, model metadata, recent bounded logs, and doctor results. Exclude tokens, conversation content, tailnet identity where unnecessary, and arbitrary environment variables.
- Centralize command redaction in `CommandRunner`; sensitive arguments/env-file contents must be impossible to emit even if a caller makes a mistake.
- Remove state-derived `bash -lc` string interpolation. Where shell is unavoidable, strictly validate and quote container names, paths, tags, and unit names.
- Validate privileged file targets against fixed directories; reject symlinks and unexpected ownership before installation or deletion.
- Audit all destructive paths, especially model cleanup and build rotation. Resolve exact targets and show them before deletion.
- Pin the Fedora container image by supported release or digest instead of `fedora:latest`; document the refresh process.
- Add dependency locking or constraints for application and build environments, plus periodic vulnerability review.
- Retest Tailscale Serve configuration to guarantee local backends, tailnet-only exposure, and explicit rejection/remediation of Funnel.

Acceptance criteria:

- Automated tests inject recognizable secrets and prove they do not appear in argv logs, state, support bundles, or exception messages.
- No privileged mutation accepts an arbitrary path from corrupted state.
- Runtime and dependency versions are reproducible from release metadata.

## Phase 10 — CI, release engineering, and hardware validation

### Automated pipeline

- Run unit and integration tests on supported Python versions, with Linux as the primary platform. macOS can remain a development smoke target but is not a supported runtime.
- Build wheel/sdist, install each into a clean environment, and execute CLI/package smoke tests.
- Run Ruff, targeted type checking, generated-launcher tests, secret scans, and documentation/catalog consistency checks.
- Add coverage reporting with thresholds focused on safety-critical modules and command handlers; do not optimize for a superficial global percentage.
- Test migrations using fixture state files from every released schema.

### Bazzite integration pipeline

- Use a Fedora Atomic/Bazzite-like test environment for Podman, systemd-unit generation, rpm-ostree command planning, root/non-root paths, and SELinux-sensitive log locations.
- Keep host mutation tests isolated behind fakes or disposable VMs. Never run desktop-target or kernel-argument changes on generic CI hosts.
- Add failure injection for missing Podman/Distrobox, read-only filesystems, full disks, permission denial, service timeouts, corrupted GGUFs, and interrupted swaps.

### BC-250 hardware-in-loop release gate

Run this gate on the supported 12/4 machine before marking a release stable:

1. Fresh installation and resume from every wizard checkpoint.
2. Reboot-safety proof: after setup, repair, LLM mode, update, failure, and uninstall, the next boot target remains graphical and the model service is disabled.
3. Vulkan smoke and generation tests on at least one small validated model and one near-budget model.
4. Model switch, context change, and slot change with both successful activation and forced rollback.
5. Thermal throttle, recovery, stop, latch, and manual reset using controlled thresholds; verify exact governor restoration.
6. llama.cpp pinned update, forced health failure rollback, manual rollback, and interrupted-build recovery.
7. Open WebUI and Tailscale Serve lifecycle, including confirmation that Funnel is not public.
8. Memory-pressure test demonstrating that service guards protect the desktop under the supported host-memory allocation.
9. Sustained benchmark measuring temperature, clocks, VRAM, throughput, and service stability.
10. Desktop-mode and uninstall reversal, with managed models retained unless explicit removal was selected.

Record the kernel, Mesa/Vulkan driver, Bazzite image, firmware/BIOS split, cooling setup, llama.cpp commit, and model digests with the release evidence.

## Suggested delivery milestones

### Milestone A — releasable feature branch

- Complete Phase 0.
- Update `AGENTS.md` and architecture documentation.
- Produce clean commits and a clean-install test report.

Exit gate: no known entry-point defects; all tests and package smoke checks pass.

### Milestone B — safety core

- Complete state transactions/journal, thermal service, host rollback, and server argv/lifecycle hardening.

Exit gate: interruption and rollback tests pass; thermal behavior is validated on hardware.

### Milestone C — reproducible lifecycle

- Complete immutable llama.cpp build management and transactional model acquisition/provenance.

Exit gate: build/model failures at every phase preserve the previous healthy configuration.

### Milestone D — polished operations app

- Complete CLI handlers, GUI task model, doctor/support bundle, chat/autotune improvements, and real-tkinter tests.

Exit gate: GUI and CLI expose the same domain behavior and recovery information.

### Milestone E — stable release

- Complete CI, packaging, Bazzite integration, and BC-250 hardware-in-loop gate.
- Version the schema/application consistently, publish a changelog and migration notes, and retain a tested rollback path to the previous application release.

Exit gate: all automated and on-card evidence is attached to the release; no compatibility candidate is described as hardware validated without recorded proof.

## Definition of done for every change

A change is complete only when:

- It preserves the one-service-owner, fit-gate, reboot-safety, reversibility, and no-secret invariants.
- Its failure path is designed and tested, including rollback failure where applicable.
- CLI, GUI, state schema, README, and architecture documentation remain consistent.
- Unit tests cover pure logic; fake-runner tests cover exact side-effect ordering; an integration or hardware test covers the real boundary when needed.
- Logs and errors are actionable and redacted.
- The change works from an installed package, not only from the source tree.
- Any hardware-dependent claim includes the BC-250/Bazzite environment and evidence used to validate it.
