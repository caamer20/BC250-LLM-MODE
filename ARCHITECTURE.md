# Architecture

BC250 LLM MODE turns an AMD BC-250 running Bazzite into a dedicated local
`llama.cpp`/Vulkan inference station, then operates it. This document maps the
module layout and the invariants that keep the hardware safe.

## Module map

| Module | Responsibility |
| --- | --- |
| `constants.py` | Paths, VRAM budgets, the shipped `KNOWN_GOOD_LLAMACPP` pin, tag validation |
| `hardware.py` / `memory_profile.py` | DRM/GPU discovery, host RAM, BIOS UMA-split inference; card numbers are never cached |
| `state.py` | Legacy v5 defaults + boot identity only; no writable runtime JSON |
| `legacy_schema.py` | Pure v1→v5 canonicalization of pre-SQLite JSON payloads (import source; no file I/O) |
| `db.py` / `unit_of_work.py` | SQLite connection policy (`open_database`, FKs/WAL/query-only), ordered atomic migrations, per-command units of work |
| `repositories.py` / `queries.py` | Typed SQL repositories and the assembled frontend read model |
| `services.py` | Typed domain services (setup, thermal, runtime config, activation, host-mode, component, OpenWebUI, sharing, maintenance) |
| `runtime_handoff.py` | Sole writer of the mode-0600 `runtime-handoff.json` rendered from committed state |
| `disclaimer.py` | The mandatory safety gate; privileged or destructive paths call `require_acknowledgment` |
| `llmmode.py` / `desktop.py` | Reboot safety: LLM mode is per-boot only; the next boot always returns to the desktop |
| `catalog.py` | Curated model metadata, forbidden-artifact rejection, VRAM fit math (`calculate_fit`), `best_quant`, `search_catalog`, `recommend_models` |
| `local_models.py` | Bounded GGUF discovery and catalog matching for files already on disk |
| `download.py` / `prepare.py` | Space-checked resumable downloads (SHA-256 manifest support), guarded GGUF verification/repair, local safetensors→GGUF conversion |
| `operations/runtime_lifecycle.py` + `runtime_lifecycle_adapter.py` + `runtime_lifecycle_command.py` | Durable `RUNTIME_UPDATE v1` / `RUNTIME_ROLLBACK v1`: immutable source resolution, bounded typed-argv builds, content-derived build IDs, atomic exchange, live verification, generation-CAS promotion/restoration; one composed command for every frontend |
| `runtime_builds.py` | Immutable build manifests/IDs (migration 005) and the build/verification/tree/component repositories |
| `runtime_process.py` / `runtime_exchange_helper.py` | Bounded, cancellable typed-argv process execution; the fixed digest-checked `renameat2(RENAME_EXCHANGE)` helper |
| `env.py` | Container/venv/toolchain provisioning ONLY — llama.cpp is never cloned or built here |
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
3. **Transactional operations.** Model and runtime changes are durable
   operations (`MODEL_ACTIVATE v1`, `RUNTIME_UPDATE v1`,
   `RUNTIME_ROLLBACK v1`) driven by the shared fenced engine: intent
   before effect, checkpoint after, probes on takeover, closed terminal
   meanings, and RECOVERY_REQUIRED whenever reality cannot be proven.
4. **Immutable runtimes, one atomic exchange.** A candidate build gets a
   content-derived ID over a canonical manifest (source commit, recipe,
   image/toolchain identity, per-binary sha256). Cutover is exactly one
   `renameat2(RENAME_EXCHANGE)` through a digest-verified helper — never
   a move dance — and promotion happens only after the seven-link live
   identity chain agrees. Rollback toggles verified lineage instead of
   trusting backup directories.
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

## Release authority (separate from runtime)

Release qualification is a PURE, candidate-bound evaluation chain that never
touches runtime state and never depends on release-evidence files at app
startup:

- `release_policy.py` — the reviewed policy (content revision 3; snapshot
  `release/policy-v3.json`): closed evidence-kind (18) and gate-code (23)
  vocabularies, RC vs 1.0 required-evidence sets, capability classification,
  approved verification mechanisms, canonical policy digest.
- `release_evidence.py` — schema-v2 envelope validation (pinned order,
  bounds, kind contracts, value-based secret refusal) plus the
  Raw/Validated/Verified boundary: only `verify_evidence_attestation`
  promotes a record to a `VerifiedEvidenceRecord`.
- `release_gate.py` — `evaluate_release` is the SOLE eligibility authority:
  it consumes a mandatory `CandidateIdentity` (version + full commit + ref +
  repository + policy digest) + an `ArtifactInventory`, binds evidence to the
  exact inventory (subject digests), and derives the decision. No boolean
  bypass exists (`release_state.may_tag_1_0_0()` is a non-authoritative
  constant False).
- `release_artifacts.py` / `release_manifest.py` — inventory v2 (roles +
  canonical digest) and the decision-derived manifest v3 (blocked drafts,
  final refusal, manifest digest).
- `tools/release/` (repository-only, NOT packaged) — validate/evaluate/
  manifest/verify CLI; `verify` performs full comparison (inventory equality,
  checksums, SBOM subject == actual wheel digest, manifest digest integrity).
- `.github/workflows/release.yml` — builds once, emits the complete release
  set, verifies, attests, verifies attestations, runs the evaluator as the
  final gate, and publishes only through the approval-gated environment.
  Every action is pinned to a network-verified full SHA; Dependabot manages
  pin updates. Runtime code imports none of this; the separation is enforced
  by the evaluator's pure inputs and the repository-only tooling boundary.

## Update policy

llama.cpp follows a **pinned known-good channel** realized as a durable
operation: the pin ships with each release, `bc250-llm-mode llamacpp status`
reports the promoted immutable build, retained rollback target, recovery
barriers, and any in-flight foreground operation, and updates are explicit
user actions. Every update resolves the ref to an exact commit first,
builds away from the active tree, exchanges atomically, verifies the new
process end-to-end, and only then promotes — with the prior build retained
as a verified rollback target. Foreground execution is the default; closing
a frontend pauses work safely for resume, and an explicit `--detach` hands a
queued operation to ONE profile-scoped supervised worker (U1.3) that never
auto-starts and never touches reboot policy. The model catalog follows the
same philosophy — new entries are compatibility candidates until they pass
an on-card Vulkan
load test.
