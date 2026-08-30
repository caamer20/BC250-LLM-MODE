# ADR 010 — Workload profiles and evidence-bound Performance Coach

**Status:** Accepted for EXP-3 implementation

## Context

The appliance already has safe model activation, exact artifact identity,
fit calculation, thermal latching, known-good restoration, and bounded
optimization controls. Those controls are expressed as context, slots, KV
format, batch sizes, flash-attention policy, and optional host tuning. A user
should be able to ask for a responsive session, long context, several clients,
cooler operation, or validated throughput without translating that goal into
llama.cpp flags.

Profiles must not turn estimates into hardware claims, silently tune the host,
or weaken the durable activation/rollback boundary. The BC-250 still has a
12 GiB fast-VRAM budget, little host RAM, a per-allocation wall, and a thermal
latch. The next boot still returns to the graphical desktop with no model
auto-start.

## Decisions

### D1 — Stable goals, resolved against current evidence

The built-in IDs are stable and owned by the application:

| ID | Intent | Resolution constraint |
| --- | --- | --- |
| `builtin-interactive` | one responsive local user | one slot and comfortable headroom where possible |
| `builtin-long-context` | longest safe context | one slot; largest model-valid context that fits; TIGHT needs confirmation |
| `builtin-shared` | simultaneous clients | two to four slots; KV accounts for context × slots |
| `builtin-cool` | lower sustained heat/noise | conservative batch/clock policy and thermal margin before throughput |
| `builtin-throughput` | highest validated rate | only a fingerprint-matched measured winner; otherwise a conservative estimate |

`custom` is an ownership class, not a sixth mutable built-in row. Custom IDs
are injected random lowercase UUID hex and names are user-facing metadata only.
Built-ins store intent and closed constraints, not claims that one context,
clock, or batch value is universally best. A preview resolves the selected
profile against the installed model identity and quant, its trained context
limit, observed fast VRAM, current host capabilities, current thermal state,
current runtime, and locally attributable evidence.

Built-ins can be previewed and applied but not edited, deleted, or renamed.
Deleting a custom profile is a soft delete and cannot alter the running
runtime. A deleted profile remains available by identity for history and
known-good explanation, but cannot be newly previewed or applied.

### D2 — Migration 012 and explicit durable fields

Migration 012 adds `workload_profiles` with explicit columns for:

- stable profile ID, owner (`builtin` or `user`), name, purpose, and resolution
  policy ID;
- profile schema version and revision;
- optional desired context per slot and slots;
- KV cache type, batch, ubatch, flash-attention preference, and an allowed
  optimization-preset ID;
- thermal policy, idle policy, and optional stop-after minutes;
- bounded evidence class, evidence fingerprint/time, created/updated time, and
  soft-delete time.

It also adds the applied `profile_revision` and 64-hex
`profile_fingerprint` to both `runtime_config` and `known_good_runtime`.
`profile_id` already exists in both tables and remains nullable for legacy or
expert configurations. Migration seeds the five built-in IDs idempotently and
preserves every v11 row byte-for-byte except for the new nullable/defaulted
columns. It never inspects the host, model files, or network and never invents
benchmark evidence.

The canonical profile fingerprint is SHA-256 over a versioned, sorted,
separator-minimized JSON resolution containing the profile ID/revision, exact
model artifact identity, quant, context, slots, KV scale/type, batch/ubatch,
flash preference, optimization preset, thermal policy, idle policy, and the
resolution-policy version. It contains no path, prompt, response, credential,
user address, or free-form diagnostic text.

Names are 1–80 trimmed Unicode characters and purposes are selected from a
closed vocabulary. Active custom names are unique case-insensitively. Profile
records use expected-revision CAS for edit/delete; stale writers lose without
partial mutation. At most 32 active custom profiles exist. Profile schema and
resolution-policy versions are closed and unknown versions fail closed.

### D3 — Bounded settings and idle policy

Resolution reuses the existing runtime safety ranges: context 512–262144 and
no higher than the model limit, slots 1–8, batch 128–2048 in multiples of 64,
ubatch 64–512 in multiples of 64 and no greater than batch, KV `q8_0` or
`q4_0`, and flash preference `auto`, `on`, or `off`. The effective fit always
uses weights + (context per slot × slots × KV bytes per token × KV scale) +
overhead. `NO-FIT` can never be applied. `TIGHT` requires an explicit
confirmation bound to the exact preview fingerprint; a changed preview
invalidates confirmation.

Idle policies are closed:

- `KEEP_LOADED` keeps an already-running model loaded only in the explicitly
  running current boot;
- `STOP_AFTER` accepts 5–240 whole minutes and may stop only after both request
  activity and durable operations have been idle for the interval;
- `STOP_ON_DESKTOP` is always enforced when returning to desktop mode.

Idle handling can stop the one server owner but can never start it, enable boot
auto-start, change the next-boot target, bypass an active/recovering operation,
or act from stale activity. No tray daemon is introduced.

### D4 — Preview is one bounded, read-only authority

`WorkloadProfileQueryService` owns list, show, preview, and compare. Queries
perform no writes, host changes, service restarts, benchmarks, or network
calls. A preview returns stable-code plain data containing:

- profile ID/revision/fingerprint and model artifact identity/quant;
- verification state, context per slot, slots, and total context;
- weights, KV, overhead, required fast VRAM, headroom, and fit verdict;
- resolved batch/ubatch/KV/flash/optimization/thermal/idle values;
- restart and host-change requirements;
- thermal readiness and evidence class/age/fingerprint;
- tested versus estimated status and exact known-good rollback availability.

Lists are capped at the five built-ins plus 32 active custom rows; comparison
accepts one to three IDs. Display summaries and stable codes are bounded.
Unknown/stale observations are rendered as unknown or estimated, never ready
or measured.

### D5 — Applying is a fenced activation, not a settings write

`WorkloadProfileCommandService` owns create/edit/delete/apply. Apply requires
the expected profile revision and exact preview fingerprint. It refuses a
thermal latch, `NO-FIT`, missing/quarantined/unverified artifact, unresolved
recovery, stale revision/fingerprint, or unconfirmed `TIGHT` preview.

Apply enqueues the existing durable `MODEL_ACTIVATE v1` workflow through the
single operation registry. Candidate resolution checkpoints the exact profile
revision/fingerprint and resolved values before any effect. Runtime
configuration, handoff, health, inference verification, known-good promotion,
and restoration remain owned by that workflow; no second activation path is
created. Editing or deleting a profile after resolution never changes the
checkpointed candidate or running runtime.

The durable operation result and known-good record identify the applied
profile revision/fingerprint. Failure restores the exact prior verified
runtime using the existing `FAILED_SAFE`, `FAILED_ROLLED_BACK`, or
`RECOVERY_REQUIRED` evidence mapping. Profile apply never spawns a competing
server and never auto-applies host tuning.

### D6 — Evidence classes and attribution

Every recommendation and resolved performance-sensitive value has exactly one
confidence class:

- `MEASURED_LOCAL`: successful local measurements match model content digest,
  quant, runtime component identity, context, slots, KV, batch/ubatch, flash,
  optimization preset, and thermal-policy fingerprint;
- `HARDWARE_VALIDATED`: reviewed candidate-bound BC-250 evidence matches the
  declared hardware/runtime scope but is not a measurement of this exact
  local fingerprint;
- `ESTIMATED`: fit math, built-in constraints, or incomplete/stale evidence.

A higher label is never inferred from a lower one. Evidence age and the
matching fingerprint are shown. Unattributed legacy benchmark/autotune rows
remain visible as history but cannot drive `builtin-throughput` or claim
`MEASURED_LOCAL`.

### D7 — Performance Coach is query-only and bounded

`PerformanceCoachService` returns at most three deterministic suggestions,
ordered by a closed safety-first priority policy. Stable suggestion codes are:

- `FIT_HEADROOM_LOW`;
- `CONTEXT_UNUSED_OR_CLIPPED`;
- `BASELINE_UNATTRIBUTED`;
- `THERMAL_MARGIN_LOW`;
- `REPEATED_LOAD_FAILURE`;
- `IDLE_POLICY_MISMATCH`;
- `SMALLER_MODEL_MEETS_GOAL`.

Each result includes benefit, tradeoff, evidence class/age/fingerprint, a
read-only profile preview, whether rollback is available, and a separate apply
action. It never ranks model intelligence, reads prompt/completion content,
changes settings, starts/stops a service, launches calibration, downloads a
model, or auto-applies a suggestion. Repeated failure is considered only when
the failure evidence matches the candidate fingerprint.

### D8 — Calibration is a durable, privacy-safe experiment

Calibration uses a versioned `PROFILE_CALIBRATE v1` durable workflow with
exclusive runtime and benchmark resources. The request contains profile ID,
expected revision, model alias, bounded candidate policy, and requesting
surface—never prompts, paths, commands, or arbitrary settings. Preflight
requires fit, thermal readiness, no recovery barrier, verified artifact, and a
known-good restoration target when an active runtime would be displaced.

Candidates are deterministic and capped. Fixed bundled non-sensitive prompt
IDs are used; prompt and generated text are never persisted, logged, emitted
as operation detail, or placed in support bundles. Measurements are bounded to
TTFT, prompt rate, generation rate, peak temperature, throttling class,
candidate fingerprint, timestamps, and terminal status. Partial results are
labelled partial and never selected as a winner.

Cancellation is honored only between candidates, never inside an activation
commit or measurement critical section. Every candidate either verifies and
checkpoints or restores the exact prior known-good runtime. The proposed
winner is not applied automatically; the user previews and applies it through
the D5 activation path. Process death, lease takeover, and compensation use
the existing durable-operation fencing rules.

### D9 — GUI/CLI parity and presentation

The one-window application adds a primary Profiles page with built-in/custom
rows, one selected-profile preview, comparison of at most three, coach cards,
and calibration progress routed to embedded Activity/log surfaces. It uses the
existing three bounded task lanes and one refresh coordinator; it creates no
Tk root, modal/secondary window, timer, or unbounded table.

The matching CLI is:

```text
bc250-llm-mode profiles list|show|preview|create|edit|delete|apply
bc250-llm-mode coach
bc250-llm-mode calibrate --profile <id>
```

Default JSON is bounded and contains no prompts, completions, paths, secrets,
or raw benchmark bodies. GUI and CLI consume the same composed services and
stable codes.

## Rejected alternatives

- fixed “optimal” settings presented as universal BC-250 facts;
- a profile as arbitrary JSON or raw llama.cpp flags;
- multiplying context only once when several slots share KV memory;
- applying profile edits directly to a running server;
- automatic application of coach/calibration results;
- benchmarking without thermal preflight, attribution, cancellation safety,
  or exact prior-runtime restoration;
- accepting `TIGHT` or stale previews without fingerprint-bound confirmation;
- using legacy unscoped autotune rows as measured evidence;
- a background tray process, server auto-start, or next-boot policy change.

## Verification and evidence

Deterministic gates cover v11→v12 row preservation and interrupted migration,
built-in identity, validation/bounds, profile CAS conflicts, stable canonical
fingerprints, common one-/multi-slot fits, model context clipping, `NO-FIT`,
fingerprint-bound `TIGHT` confirmation, thermal/recovery refusal, pure preview,
three-profile/three-suggestion caps, evidence attribution, no auto-apply,
activation checkpoint binding, known-good rollback, calibration crash/cancel
recovery, idle suppression during requests/operations, and privacy canaries.

Physical qualification must calibrate Interactive, Long context, Shared, and
Cool on both a small model and a 9B model under thermal observation on each
advertised host profile. It records candidate/artifact/runtime identity and
bounded metrics only. Until that exact-candidate evidence exists, built-ins
and coach results remain estimated or otherwise explicitly scoped; no physical
performance, temperature, or throughput claim is made.
