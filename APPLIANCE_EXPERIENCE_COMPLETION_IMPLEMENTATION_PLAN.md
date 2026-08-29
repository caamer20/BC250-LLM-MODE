# BC250 LLM MODE — Appliance Experience Completion Implementation Plan

**Status:** Implementation-ready follow-up plan; implementation not started

**Plan IDs:** EXP-1 through EXP-8

**Planning baseline:** `1911ad5` on `main`

**Package-code baseline:** `ccd1777` (`0.9.0.dev0`, schema v10)

**Predecessor:** `UNIFIED_NATIVE_GUI_IMPLEMENTATION_PLAN.md` (GUI-1 through GUI-8)

**Product-experience authority:** `END_USER_EXPERIENCE_IMPLEMENTATION_PLAN.md`

**Safety, durability, host, and release authorities:** accepted ADRs,
`MASTER_IMPLEMENTATION_PLAN.md`, `V1_0_RELEASE_CLOSURE_IMPLEMENTATION_PLAN.md`,
and the current `AGENTS.md` handoff

This plan begins after GUI-8 replaces the legacy wizard/dashboard experience.
It completes the last mile around that window: installing and launching it
like a normal desktop application, preventing duplicate GUI owners, guiding
remote clients, converting expert settings into reusable workload profiles,
presenting maintenance before it becomes failure, making repair actions
understandable and recoverable, and defining a trustworthy application-update
path.

It does not redesign the unified window a second time. It composes existing
services and adds only the missing contracts needed to make BC250 LLM MODE
feel like a dependable appliance throughout its lifecycle.

---

## 1. Outcome

After EXP-8, a non-expert user can:

1. install BC250 LLM MODE through one reviewed command or offline bundle;
2. launch it from the Bazzite/CachyOS application menu like a normal app;
3. relaunch it without opening duplicate windows or refresh loops;
4. resume the page and safe unfinished task they were using;
5. connect Open WebUI, PocketPal, or another OpenAI-compatible client using
   an exact tested URL, model name, and purpose-scoped credential;
6. choose **Interactive**, **Long context**, **Shared**, **Cool**, or a custom
   workload without learning llama.cpp flags first;
7. understand what needs attention from one prioritized maintenance inbox;
8. receive optional privacy-safe notifications for long operations, thermal
   stops, backup failures, and storage pressure;
9. run a recommended repair with a preview, evidence, and verified result;
10. update the application only from an eligible, signed release, with a
    staged rollback path—or receive an honest “not available yet” result;
11. uninstall or return to desktop mode without losing models by default;
12. accomplish every supported task through the GUI while retaining CLI
    parity for automation and recovery.

The result is still a small native Python/tkinter application. It does not
gain a browser shell, cloud account, telemetry agent, tray daemon, or
always-running GUI process.

---

## 2. Findings from the current-product audit

### 2.1 Strong foundations already present

The repository already has most appliance mechanics. These must be reused:

- one SQLite source of truth at schema v10 and one composed application graph;
- durable, fenced operations for activation, acquisition/import, runtime
  update/rollback, model removal, backup creation, and backup restore;
- bounded Home, Doctor, Storage Capacity, Support Bundle, Model Library, and
  Activity services;
- tailnet-only HTTPS sharing through the authenticated gateway, with the raw
  llama backend remaining loopback-only;
- gateway provision/verify/rotate/revoke behavior that keeps the secret out
  of SQLite, argv, logs, events, and support bundles;
- exact Open WebUI and API URL computation from observed Tailscale state;
- model fit analysis using model, quantization, context, and parallel slots;
- local benchmark/autotune attribution and bounded result retention;
- read-only doctor findings with stable IDs and recommended commands;
- a pure Repair Center catalog that routes mutation through existing services
  or durable operations;
- dry-run storage cleanup suggestions with exact targets and recoverability;
- a non-destructive desktop-mode command and separately confirmed uninstall;
- a release evaluator, artifact inventory, manifest, SBOM, provenance, and
  approval-gated workflow that already fail closed.

### 2.2 Remaining experience gaps

| Gap | What the user experiences today | Required improvement |
| --- | --- | --- |
| Installation is repository/venv oriented | The user copies commands and remembers a long executable path | Reviewed installer, desktop entry, stable launcher, offline option, and uninstall preview |
| No GUI single-instance contract | A second launch may create another shell and refresh owner | Same-user local activation broker with stale-owner recovery |
| Remote URLs exist but setup is expert-shaped | Phone clients fail on `/api` vs `/v1`, wrong port, missing model alias, or missing auth | Guided connection cards and positive/negative end-to-end probes |
| Gateway credential is installation-scoped | Rotating a phone credential can disrupt every client | Purpose-scoped client records and independently revocable secrets |
| Context, slots, KV, and tuning are controls rather than goals | Users must infer what “good” settings mean | Named workload profiles with fit, thermal, and benchmark evidence |
| Health, storage, backup, and operation state are separate | Users discover maintenance only after an action fails | Prioritized maintenance inbox with freshness and ownership |
| Repair recommendations often end in CLI text | Users cannot tell what will change or whether it worked | Previewable repair actions with typed result verification |
| Runtime updates are safe but application updates are manual | `git pull`/`pip install` is not a product update flow | Signed, staged, rollback-capable application update design |
| Desktop feedback ends when the app closes | Long downloads and thermal stops are easy to miss | Optional redacted desktop notifications, deduplicated and bounded |
| User-facing copy is distributed across modules | Terms and recovery wording drift | Stable message catalog, glossary, accessibility and locale-ready formatting |
| No formal usability gate exists | Technical correctness can pass while journeys remain confusing | Scripted task acceptance with novice and operator evidence |

### 2.3 Priority decision

The implementation order is based on risk and user frequency:

1. **P0:** installation/desktop launch and single-instance ownership;
2. **P0:** remote-client connection assistant and credential isolation;
3. **P1:** named workload profiles and locally calibrated recommendations;
4. **P1:** maintenance inbox and optional notifications;
5. **P1:** guided repair, safe undo, and support handoff;
6. **P2:** signed application update and offline update bundle;
7. **P0 throughout:** plain-language copy, accessibility, bounded resources,
   privacy, CLI parity, tests, and physical evidence.

The updater is intentionally late. Convenience cannot precede release
identity, signature, rollback, and database-compatibility guarantees.

---

## 3. Non-negotiable product invariants

Every EXP phase must preserve these rules:

- BC-250 detection always scans AMD PCI vendor `0x1002`; no `cardN` is cached.
- The 12 GiB fast-VRAM budget, host-RAM constraints, mmap requirement, and
  standard-layout-only model policy remain authoritative.
- No action proposes ReBAR/Above-4G or an on-the-fly 14/2 UMA split.
- The next boot remains graphical with no model auto-start unless an accepted
  future product decision explicitly changes that invariant.
- The thermal latch blocks unsafe activation, tuning, and benchmark work.
- Widgets never call systemd, Podman, Distrobox, package managers, Tailscale,
  Vulkan tools, HTTP backends, or privileged helpers directly.
- Only composed services and the durable operation engine own mutation.
- Convenience cannot bypass leases, revisions, fit validation, credential
  policy, or `RECOVERY_REQUIRED` barriers.
- The raw llama backend remains loopback-only. Remote API publication goes
  through the authenticated gateway and Tailscale Serve; Funnel stays off.
- Prompts, completions, conversation text, credentials, authorization headers,
  and raw remote bodies never enter logs, events, notifications, metrics, or
  support bundles.
- No multi-GB model or unbounded log/list is loaded into host RAM.
- No silent application, runtime, catalog, model, container, or host update.
- No release, support, or hardware claim is inferred from developer tests.

---

## 4. Target architecture

### 4.1 New bounded domain surfaces

Introduce modules only as their owning phase lands:

```text
bc250_llm_mode/
    desktop_integration.py   # pure desktop-entry/launcher plans + service
    instance_broker.py       # GUI-only local activation protocol
    connection_setup.py      # client cards, credential commands, probes
    workload_profiles.py     # built-ins, custom profiles, preview policy
    performance_coach.py     # evidence-bound suggestions, never mutation
    maintenance_center.py    # prioritized query-only maintenance inbox
    notifications.py         # redacted notification policy + adapter
    repair_commands.py       # preview/execute/verify routing
    application_update.py    # signed update query/plan contracts
    message_catalog.py       # stable UI messages and glossary keys
```

Do not place Tk imports in these modules. GUI pages consume typed plain-data
views and invoke composed commands. CLI commands consume the same views.

### 4.2 New composed services

The application graph gains bounded interfaces:

- `DesktopIntegrationService`
- `ConnectionSetupQueryService`
- `ConnectionCredentialCommandService`
- `WorkloadProfileQueryService` and `WorkloadProfileCommandService`
- `PerformanceCoachService`
- `MaintenanceCenterQueryService`
- `NotificationPreferenceService`
- `RepairCommandService`
- `ApplicationUpdateQueryService`
- `ApplicationUpdateCommandService` only after its ADR/trust gates pass

Service results must be serializable, bounded, stable-code based, and free of
Tk widgets and secrets. Read-only queries must not write “last checked” state.

### 4.3 Persistence changes are deliberate milestones

Do not hide new durable concepts in arbitrary settings JSON. Proposed schema
changes must be reviewed one migration at a time:

- **Migration 011 — client credentials:** named client metadata, scopes,
  fingerprint, creation/rotation/revocation timestamps, and revision; secret
  bytes remain separate mode-0600 files.
- **Migration 012 — workload profiles:** built-in IDs plus custom profile and
  revision records; active runtime stores the applied revision/fingerprint.
- **Migration 013 — notification receipts/preferences:** category preferences
  and redacted terminal-event receipt keys; no body persistence.
- **Migration 014 — application installations:** installed release identity,
  artifact inventory digest, schema compatibility, prior slot, attempt, and
  rollback status.

Each requires interruption tests, existing-row preservation, wheel install
tests, forward-only behavior, and a documented rollback boundary. Numbers are
proposals until the preceding phase confirms no intervening schema change.

### 4.4 GUI integration

The unified shell gains these routes without changing its one-window rule:

- `connections`
- `profiles`
- `maintenance`
- `maintenance/repair`
- `maintenance/updates`

Home links to them through one primary recommendation. Existing System,
Settings, and Help pages retain expert controls. No EXP phase recreates a long
dashboard, second Activity window, or permanent log pane.

---

## 5. EXP-1 — Normal installation, launch, and single-instance ownership

**Priority:** P0

**Dependency:** GUI-8 complete; host/bootstrap tests green on the candidate.

### 5.1 Installation experience

Provide two reviewed entry paths:

1. **Online release bundle:** fetch a named version, verify manifest,
   checksums, and signature before executing package code, create the app venv,
   install the exact wheel, run a read-only smoke check, and offer the launcher.
2. **Offline bundle:** the same wheel, locked dependencies, manifest, SBOM,
   signature/provenance, and installer entrypoint on removable storage. Offline
   verification is identical; offline does not mean unsigned.

Until a qualified signed release exists, production install must refuse and
explain that only source/developer installation is available. Do not make
`curl | sh`, a mutable branch, or an unverified GitHub asset authoritative.

The installer must:

- detect supported Python/host before mutation;
- show exact profile/install/disk targets;
- never run a host-wide Python upgrade;
- create a dedicated venv under resolved `AppPaths`;
- use hashes/locked artifacts for bundled Python dependencies;
- install no Tk/host package until the platform plan authorizes it;
- preserve existing profile/database content;
- stop before migration if the app cannot safely read the profile;
- emit a redacted, bounded install receipt.

### 5.2 Desktop integration

`DesktopIntegrationService` generates and validates:

- a stable launcher in the resolved user-local executable directory;
- `bc250-llm-mode.desktop` under the observed XDG applications directory;
- a packaged local icon—no network fetch;
- `StartupNotify=true` where supported and `Terminal=false` for the GUI;
- a separately labeled terminal-chat desktop action only if supported;
- no desktop autostart entry.

The Exec line uses an exact path without shell evaluation. Rendered entries are
validated before atomic replacement. Removal deletes only app-owned,
content-verified files.

### 5.3 GUI single-instance broker

Only the GUI is single-instance. CLI commands and the worker remain peers.

Implement a same-user local broker using:

- `fcntl.flock` inside the mode-0700 profile directory;
- an AF_UNIX socket with mode 0600;
- Linux peer-UID validation where available;
- a closed, size-bounded protocol: `ACTIVATE`, `ROUTE`, `OPEN_OPERATION`, and
  `OPEN_MODEL`; no arbitrary command, path, prompt, or credential;
- a random per-owner nonce held in the protected runtime file;
- a 500 ms connection/ack deadline;
- stale cleanup only after lock acquisition and failed socket/PID proof;
- no signal/process kill as stale recovery.

A second launch activates and navigates the existing shell, then exits zero.
If activation cannot be proved, it prints recovery guidance and does not start
a competing GUI blindly.

### 5.4 Safe session resume

Persist only last route, non-secret filters/sort, bounded window size, and
advanced-section expansion. Never persist draft prompts, typed confirmations,
revealed credentials, log scrollback, or destructive confirmations. Setup and
operation resume derive from durable domain state, not UI state.

### 5.5 CLI parity

```text
bc250-llm-mode desktop-integration status|install|remove
bc250-llm-mode gui --route <closed-route-id>
```

### 5.6 Tests and exit gate

- desktop-entry escaping, XDG injection, and paths-with-spaces;
- no shell in Exec and no autostart;
- two-process test: one Tk owner, one activation delivery;
- wrong UID/nonce, oversized payload, unknown verb, stale socket refusals;
- abrupt owner death and safe stale recovery;
- CLI remains usable while GUI owns the broker;
- source/editable/wheel launcher parity;
- Bazzite KDE and CachyOS menu launch evidence;
- idle RSS/CPU remains inside the GUI budget.

**EXP-1 exit:** install, find, launch, relaunch, and remove work without a venv
path, duplicate window, or background auto-start.

---

## 6. EXP-2 — Connection Assistant for Open WebUI, PocketPal, and API clients

**Priority:** P0 for every remote-access claim

**Dependency:** EXP-1; ADR 005 gateway topology; GUI Connections page.

### 6.1 One authoritative connection snapshot

Compose without mutation:

- model health and observed public alias;
- gateway health, credential readiness, and supported API paths;
- Open WebUI installed/running/healthy state;
- Tailscale installed/daemon/connected/DNS state;
- Serve mappings and Funnel refusal state;
- exact local/tailnet URLs and probe freshness.

Distinguish explicitly:

- Open WebUI: `https://<node>:8443/`
- OpenAI base URL: `https://<node>:10000/v1`
- Models: `<base-url>/models`
- Chat completions: `<base-url>/chat/completions`
- Model value: the observed public alias, never a filesystem path.

Never advertise `/api` as the OpenAI base URL.

### 6.2 Guided state machine

Check in order:

1. model server healthy;
2. authenticated gateway credential available;
3. authorized local probe passes;
4. unauthorized local probe fails as expected;
5. Tailscale connected with DNS identity;
6. Serve targets exactly gateway/Open WebUI loopback ports;
7. Funnel disabled;
8. authorized tailnet `/v1/models` passes;
9. instructions render from the observed endpoint.

Every failure gives one safe next action. Starting sharing shows all components
it will start and never silently broadens exposure.

### 6.3 Client cards

Ship local, versioned cards for Open WebUI, PocketPal, generic
OpenAI-compatible apps, curl, Python, and raw SSE diagnostics. Each declares:

- **Hardware-tested** with named client/version evidence;
- **Protocol-tested** with fixture evidence only; or
- **Example only** without a support claim.

Cards use the client's actual labels: Base URL, API Key, Model, streaming, and
timeout. Metadata is reviewed locally, never scraped live by the GUI.

### 6.4 Independently revocable credentials

After migration 011 and an ADR update:

- create bounded user labels such as “Cameron's phone”;
- generate/display the secret once;
- persist only fingerprint, scopes, dates, revision, and revoked state;
- keep each secret in a separate mode-0600 non-user-named file;
- revoke one client without disrupting others;
- support bounded overlap rotation, off by default;
- record last-used time and endpoint class only—no body/model/address history;
- provide emergency **Disable all remote API access** independent of model
  health and Open WebUI.

Migrate the singleton as `legacy-install` without exposing/rotating its secret.

### 6.5 Reveal/copy policy

- Reveal/copy is explicit and time-limited.
- Never persist secrets in snippets, screenshots, argv, events, or notices.
- Clear the UI variable on page exit where practical without claiming
  cryptographic Python-memory erasure.
- Existing credentials cannot be revealed again; rotate instead.
- Copy feedback never echoes the copied secret.

### 6.6 End-to-end probes

1. loopback unauthorized request must fail;
2. loopback authorized `/v1/models` returns the public alias;
3. tailnet unauthorized request must fail;
4. tailnet authorized minimal chat streams one valid event by deadline.

Use fixed non-sensitive prompts and persist no response body. Same-host
topology limitations are labeled; physical qualification uses a second device.

### 6.7 CLI parity

```text
bc250-llm-mode connections status|clients
bc250-llm-mode connections add-client --label <label>
bc250-llm-mode connections rotate-client|revoke-client <id>
bc250-llm-mode connections disable-all
bc250-llm-mode connections instructions <client-type>
bc250-llm-mode connections test <client-id>
```

Secret creation writes once only to a controlling TTY. Default JSON contains
metadata, never secret material.

### 6.8 Tests and exit gate

- exact URLs, paths, and aliases; no `/api` or model filesystem paths;
- multi-client create/rotate/revoke/overlap with revision fencing;
- migration interruption/preservation;
- unauthorized negative probes mandatory;
- secret canaries absent from DB/log/event/support/argv/notice/default JSON;
- Funnel blocks ready state;
- physical PocketPal, Open WebUI, and generic SSE journeys;
- emergency disable while llama.cpp is unhealthy.

**EXP-2 exit:** the app gives a phone user exact working values and revokes
that phone without rotating every other client.

---

## 7. EXP-3 — Workload Profiles and evidence-bound Performance Coach

**Priority:** P1

### 7.1 Built-in goals

| Profile | Goal | Default shape | Guardrail |
| --- | --- | --- | --- |
| Interactive | One responsive local user | 1 slot, moderate context | Prefer comfortable VRAM headroom |
| Long context | Maximum safe context | 1 slot, fit-derived context | Explicit confirmation for TIGHT |
| Shared | Simultaneous clients | 2–4 slots, smaller per-slot context | KV uses context × slots |
| Cool | Lower sustained heat/noise | conservative batch/clock | Thermal margin before throughput |
| Throughput | Highest validated rate | benchmark-derived batch/KV | Hardware-tested combinations only |
| Custom | Expert bounded settings | user selected | Same fit/thermal gates |

Built-ins contain intent and constraints, not universal magic numbers.
Resolution uses installation, quant, observed VRAM, host, fit, and local tests.

### 7.2 Durable profile model

Migration 012 stores stable ID/owner/name/purpose, schema/revision, context,
slots, KV, batch/ubatch, flash preference, allowed optimization preset ID,
thermal policy, idle behavior, evidence provenance, timestamps, and soft-delete.

Active runtime records exact profile revision and resolved fingerprint. Editing
a profile never changes the running server; apply is a separate operation.

### 7.3 Preview and comparison

Show model/quant, verification, context per slot, slots, total context,
weights/KV/overhead/headroom, fit verdict, restart, host changes, thermal
readiness, tested/estimated status, and known-good rollback. Compare at most
three profiles.

### 7.4 Performance Coach

Return at most three query-only, stable-code suggestions for insufficient fit
headroom, unused/clipped context, below-attributed baseline, thermal warnings,
repeated fingerprint-matched load failure, idle-policy mismatch, or a smaller
model that safely meets requested users/context.

Each carries evidence age/fingerprint, benefit/tradeoff, confidence
(`MEASURED_LOCAL`, `HARDWARE_VALIDATED`, `ESTIMATED`), preview, separate apply,
and rollback availability. Never rank intelligence or auto-apply tuning.

### 7.5 Calibration journey

Preflight fit/thermal/operations, explain duration/candidates, use fixed
non-sensitive prompts, measure TTFT/prompt rate/generation rate/peak temp and
throttling, cancel only between candidates, fully attribute results, label
partials, and apply a proposed winner only through verified activation with
known-good restoration on failure.

### 7.6 Idle behavior

- `KEEP_LOADED` only in the explicitly running current boot;
- `STOP_AFTER` bounded 5–240 minutes and suppressed during requests/operations;
- `STOP_ON_DESKTOP` always enforced.

Idle behavior cannot start a server or change next-boot policy.

### 7.7 CLI parity

```text
bc250-llm-mode profiles list|show|preview|create|edit|delete|apply
bc250-llm-mode coach
bc250-llm-mode calibrate --profile <id>
```

### 7.8 Exit gate

Test profile resolution at common contexts/slots, revision conflicts, no-fit,
thermal latch, benchmark attribution, cancellation rollback, no auto-apply,
and idle invariants. Physically calibrate Interactive, Long context, Shared,
and Cool on a small and 9B model.

**EXP-3 exit:** users choose outcomes and see evidence/tradeoffs without first
translating them into raw llama.cpp flags.

---

## 8. EXP-4 — Prioritized Maintenance Inbox and safe notifications

**Priority:** P1

### 8.1 Maintenance snapshot

Compose doctor, operation recovery, thermal freshness, server/model/runtime
verification, storage/cleanup preview, backup age, credentials/topology,
optional services, update availability, and platform qualification. Mark each
as live, cached, stale, or not checked. Never recompute expensive digests on
normal GUI refresh.

### 8.2 Closed priority policy

Show at most five Home items, ordered:

1. safety stop/sensor loss;
2. recovery required/failed rollback;
3. insecure remote topology/credential failure;
4. corrupt or mismatched durable/runtime/model evidence;
5. critically low storage;
6. stale/unverified backup;
7. paused/failed operation;
8. verified update;
9. informational recommendation.

Every item includes impact, evidence age, resource, primary action, details,
and dismissibility. Safety/recovery/integrity/security cannot be dismissed.

### 8.3 Notification categories and privacy

Allow optional notices only for long-operation success/failure, thermal
warning/stop, critical storage, backup failure/staleness, safety-driven remote
disable, and verified application update. Never include paths, addresses,
client labels, secrets, prompts, output, exceptions, or logs.

### 8.4 Delivery architecture

- detect capability; install nothing silently;
- fixed-argv command adapter or reviewed D-Bus adapter, never widgets;
- no tray/resident GUI;
- operation notice only after terminal event commit;
- thermal notice from watchdog truth, not another sensor loop;
- migration 013 preferences plus redacted dedupe receipt key, no body;
- rate-limit/collapse repeats;
- delivery failure never changes domain result.

### 8.5 Maintenance schedule

Use existing worker/systemd capabilities only for opt-in bounded checks: daily
disk/backup/topology, explicit weekly full doctor, and update metadata only
after a signed channel exists. Never download, repair, clean, or start a model
automatically.

### 8.6 CLI parity and exit

```text
bc250-llm-mode maintenance status|check
bc250-llm-mode maintenance cleanup --dry-run
bc250-llm-mode notifications status|test
bc250-llm-mode notifications set <category> on|off
```

Test deterministic priorities, stale labels, absence of refresh probes,
cross-restart dedupe, privacy canaries, rate limiting, delivery failure, no
tray/poll loop, and physical KDE enabled/disabled behavior.

**EXP-4 exit:** the highest-risk current issue is visible before work starts,
with useful optional notices but no background GUI or privacy leak.

---

## 9. EXP-5 — Guided repair, verified cleanup, and bounded undo

**Priority:** P1

### 9.1 Typed repair contract

Each action gains stable IDs, owning service/operation, preconditions and
unavailability reason, exact mutation preview, privilege, cancellation policy,
duration/space estimate, reversibility, success probe, failure mapping, and
support relevance. GUI cannot execute a route string dynamically; composition
maps a closed ID to one command.

### 9.2 Initial guided repairs

- regenerate a stale runtime handoff from verified state;
- reclaim an expired worker lock through fencing;
- resume/recover where policy permits;
- restore known-good model/runtime/config lineage;
- rotate/revoke a broken gateway credential;
- disable unsafe sharing;
- verify/quarantine an invalid model with evidence;
- rebuild service/launcher files from verified state;
- return to desktop while preserving models;
- create/self-check a redacted support bundle.

Database corruption/newer schema never gets a reset shortcut; offer verified
backup inspection/restore or upgrade.

### 9.3 Durable cleanup workflow

Freeze an ADR for exact identities, containment, active/known-good/newest
backup exclusions, quarantine-first behavior, retention/restore, deletion safe
point, crash probing, and post-effect free-space verification. Default-select
only proven abandoned app staging. External model paths are never deleted.

### 9.4 Honest undo

Show Undo only when the workflow defines an exact inverse, prior identity
exists/verifies, no later operation superseded it, lease is available, and the
deadline holds. No generic undo. Permanent deletion and expired secret
rotation are not labeled reversible.

### 9.5 Support handoff

Show stable IDs and whether the prior working state survives, preview/self-check
a local support bundle, never upload it, and provide exact offline recovery
commands.

### 9.6 CLI parity

```text
bc250-llm-mode repair list|preview|run|verify
bc250-llm-mode undo list|preview|run
bc250-llm-mode storage cleanup --dry-run
bc250-llm-mode storage cleanup --apply --confirm <token>
```

### 9.7 Exit gate

Test one typed mapping per action, unbypassable preconditions, preview/execution
revision equality, cleanup crash matrix, exclusions, undo expiry/supersession,
post-effect probes, and secret-free support. Physically test interruption,
runtime rollback, gateway disable, desktop return, and quarantine/restore.

**EXP-5 exit:** problems lead to understandable, previewed, verified actions;
Undo appears only when mechanically true.

---

## 10. EXP-6 — Trustworthy application update and rollback

**Priority:** P2; release- and owner-gated

**Dependency:** EXP-5, application-update ADR, eligible signed artifacts,
migration 014, and physical backup/restore evidence.

### 10.1 Trust boundary

Accept only release sets passing repository identity/ref, eligible evaluator
decision, manifest/inventory/checksums, wheel-bound SBOM, approved provenance,
signature, platform qualification, and database compatibility. Branches,
“latest” redirects, arbitrary URLs, pip name resolution, and unsigned wheels
are not authorities.

Before a verified channel exists, show an honest unavailable message rather
than recommending an untrusted upgrade command.

### 10.2 Check and preview

Show installed version/commit/digest/channel; candidate immutable ref, sizes,
date; platform qualification; migration compatibility; signed plain-text
notes; space/backup/restart plan; rollback slot; and stable refusal reasons.
Never render untrusted remote Markdown/HTML.

### 10.3 Two-slot installation

```text
releases/<content-identity>/venv/
current -> releases/<identity>/
previous -> releases/<identity>/
```

The stable launcher resolves `current`. Stage without modifying the running
venv, install exact verified artifacts, run `pip check` and smoke tests, then
use one same-filesystem atomic pointer switch through a minimal digest-verified
helper. Launch new app in post-update mode with a bounded acknowledgment.

### 10.4 Profile safety

Before migration-capable launch, create/verify a durable profile backup, record
source/target schema, retain old app, and refuse unless rollback remains exact.
Run integrity/composition/read-model smoke checks and start no model. Failure
restores app pointer and, when required and verified, profile backup. Ambiguity
becomes `RECOVERY_REQUIRED`, never reset.

### 10.5 Durable self-update workflow

Freeze crash behavior at bundle verification, staging, smoke, backup, pointer
switch, new-process ack, schema migration, health, rollback switch, profile
restore, and cleanup. Recovery inspects actual pointer identities, receipts,
schema, and acknowledgments rather than stale step state.

### 10.6 Retention/offline update

Keep current plus one verified prior release. Never remove the only app able to
read the schema. Downgrade requires exact compatibility/restore. Offline import
uses the same verifier and rejects traversal, symlinks, special files,
duplicates, mutation, and unknown extras before execution.

### 10.7 CLI parity

```text
bc250-llm-mode update status|check
bc250-llm-mode update preview|apply <version>
bc250-llm-mode update import-bundle <path>
bc250-llm-mode update rollback
bc250-llm-mode update cleanup --dry-run
```

### 10.8 Exit gate

Test every trust-property tamper, untrusted-note rendering, concurrency,
two-slot parity, full crash matrix/fencing, migration/rollback/profile restore,
no autostart, offline negatives, and physical Bazzite/CachyOS update/rollback.
The release evaluator remains sole eligibility authority.

**EXP-6 exit:** self-update either verifies and starts the new version with
profile integrity or returns to the exact prior installation without pretending
success.

---

## 11. EXP-7 — Copy, accessibility, discoverability, and privacy

**Priority:** P0 polish across all phases

### 11.1 Stable message catalog

Centralize consistency-critical state, fit/thermal/security, lifecycle,
context/slots, endpoint, privilege, recovery/rollback/undo, evidence level, and
destructive-confirmation copy. Unknown codes show safe generic text plus the
code, never raw exception text as headline.

### 11.2 Offline glossary/help

Define model/quant/GGUF/context/KV/slots; VRAM/GTT/RAM/UMA/CU/Vulkan;
Open WebUI/base URL/gateway/Serve/Funnel; and installed/verified/active/
known-good/recovery. Core help is bundled and internet-independent.

### 11.3 Lightweight command palette

`Ctrl+K` uses bounded local token matching—no fuzzy dependency/network. It
lists only permitted navigation/actions and why blocked. Protected actions
open their normal preview page; the palette never executes them directly.

### 11.4 Accessibility

Require keyboard reachability, logical tab/focus, safe Escape/Enter semantics,
non-color status, contrast, 125–200% scaling, no streaming focus theft,
reduced motion, and text alternatives for weak table accessibility. Document
Tk/platform limitations honestly.

### 11.5 Locale readiness and privacy center

Avoid sentence fragments; format dates/durations/numbers/bytes/temperatures/
tokens through helpers while IDs/JSON/logs stay neutral. Inventory conversation,
logs, events, benchmarks, credentials, Open WebUI data, backups, bundles,
notices, and update network behavior with retention/manage actions only where a
safe owner exists. There is no telemetry toggle because there is no telemetry.

### 11.6 Exit gate

Test catalog completeness/fallbacks, banned ambiguous terms, keyboard/focus,
theme/scale review, protected palette behavior, actual privacy locations, locale
formatters, and zero Help/palette/glossary/icon network use.

**EXP-7 exit:** language is consistent, core tasks are keyboard-operable, help
is offline, and retained/transmitted data is accurately disclosed.

---

## 12. EXP-8 — Journey qualification and release handoff

**Priority:** P0 completion gate

### 12.1 Scripted journeys

Run from fresh and upgraded profiles:

1. install/menu launch;
2. safety/setup;
3. import existing model;
4. apply profile/start model;
5. native chat/stop;
6. PocketPal setup/stream;
7. add second client/revoke first;
8. interrupted-operation recovery;
9. low-storage cleanup/quarantine/undo;
10. backup/restore;
11. signed app update/rollback if permitted;
12. desktop mode/reboot with no model;
13. redacted support bundle after seeded failure;
14. uninstall preserving models/reinstall/rediscover.

Capture completion, wrong turns, terminal use, unclear labels,
time-to-first-safe-action, resources, and safety/privacy defects—locally, not
as telemetry.

### 12.2 Participants

Include a Linux user unfamiliar with llama.cpp, existing BC-250 owner, recovery
operator, keyboard-only pass, and mobile-client pass. Feedback becomes tracked
issues; it cannot weaken safety requirements.

### 12.3 Resource qualification

On Bazzite and CachyOS measure idle/peak RAM and CPU, thread/callback counts,
FD/socket stability, notification impact, and no large host allocation. Retain
GUI-8 goals: <=90 MiB idle RSS, <1% settled CPU, <=3 GUI-process background
threads, bounded lists/chat/logs.

### 12.4 Documentation

Update README install/platform/menu/first-run, exact connection URLs, profiles,
maintenance/repair/update, offline bundles, privacy, uninstall/reinstall/model
preservation, CLI reference, qualification labels, CHANGELOG, and AGENTS.

### 12.5 Release consequence

Any EXP package-code change creates a new candidate and invalidates evidence
for `ccd1777`. Repeat developer/package gates, Bazzite+CachyOS physical
journeys, thermal/runtime/backup/soak, GUI resources, remote positive/negative,
update/rollback if advertised, and all security/human/release gates. Never
infer evidence from a plan, test, screenshot, or older commit.

**EXP-8 exit:** the shipped experience has reproducible journey, resource,
security, host, hardware, and release evidence.

---

## 13. Commit boundaries

Each boundary is independently reviewable and green before the next.

### EXP-1

1. `docs(EXP-1): freeze desktop integration and instance ownership contract`
2. `feat(EXP-1): add verified desktop integration plans and CLI`
3. `feat(EXP-1): add bounded same-user GUI activation broker`
4. `feat(EXP-1): integrate safe route and window-state resume`
5. `test(EXP-1): qualify menu launch and duplicate-launch recovery`

### EXP-2

6. `docs(EXP-2): freeze multi-client gateway credential contract`
7. `feat(EXP-2): add atomic client-credential migration and repositories`
8. `feat(EXP-2): add connection snapshot, cards, and bounded probes`
9. `feat(EXP-2): add Connections page and CLI parity`
10. `test(EXP-2): qualify phone, browser, SSE, and auth-negative journeys`

### EXP-3

11. `docs(EXP-3): freeze workload profile and coach evidence contract`
12. `feat(EXP-3): add profile migration, repositories, and built-ins`
13. `feat(EXP-3): add preview, apply, and known-good rollback`
14. `feat(EXP-3): add evidence-bound coach and calibration flow`
15. `test(EXP-3): qualify fit, thermal, benchmark, and idle behavior`

### EXP-4

16. `feat(EXP-4): add bounded prioritized maintenance snapshot`
17. `docs(EXP-4): freeze notification privacy and deduplication policy`
18. `feat(EXP-4): add notification migration, adapter, and preferences`
19. `feat(EXP-4): add Maintenance inbox and CLI parity`
20. `test(EXP-4): qualify priorities, notifications, and resource bounds`

### EXP-5

21. `docs(EXP-5): freeze repair, cleanup, and undo contracts`
22. `feat(EXP-5): add typed repair preview/execute/verify service`
23. `feat(EXP-5): add durable cleanup quarantine and recovery workflow`
24. `feat(EXP-5): add evidence-gated undo and support handoff`
25. `test(EXP-5): qualify crash recovery, exclusions, and physical repair`

### EXP-6

26. `docs(EXP-6): freeze signed self-update and two-slot publication ADR`
27. `feat(EXP-6): add release query, verifier, and update preview`
28. `feat(EXP-6): add installation migration and staging`
29. `feat(EXP-6): add digest-pinned publisher and durable recovery`
30. `feat(EXP-6): add UI, CLI, offline import, and rollback`
31. `test(EXP-6): qualify tamper rejection and physical update round trip`

### EXP-7 and EXP-8

32. `refactor(EXP-7): centralize stable copy and local glossary`
33. `feat(EXP-7): add bounded command palette and privacy center`
34. `test(EXP-7): enforce keyboard, scale, terminology, and privacy gates`
35. `docs(EXP-8): update operator and end-user documentation`
36. `test(EXP-8): record candidate-bound journey and resource evidence`
37. `docs(EXP-8): record release handoff without fabricating gates`

Do not collapse a schema migration, workflow, host adapter, GUI page, and
physical evidence claim into one commit.

---

## 14. Cross-cutting test matrix

### Pure policy

- closed routes/actions/messages;
- deterministic maintenance priority;
- exact profile resolution/fit;
- bounded evidence-bound coach;
- redacted/deduplicated notices;
- precondition-derived repair/undo;
- stable update refusal codes.

### Repositories/migrations

- fresh schema and v10 upgrade through each migration;
- interruption boundaries and old-row preservation;
- CAS conflicts for clients/profiles/notices/updates;
- no secrets in database dumps;
- non-destructive newer-schema refusal.

### Services/GUI

- queries never write;
- widgets import no host/process/network implementations;
- mutation enters one command/operation;
- reopen never duplicates work;
- stale observation labels;
- required auth-negative probes;
- one primary action and reason for every disabled action;
- no second app window/messagebox regression.

### Packaging/architecture

- new modules included in wheel;
- source/editable/wheel/clean-install parity;
- desktop entry points to installed wheel;
- release evaluator remains sole authority;
- guards for `--no-mmap`, hardcoded DRM card, public raw backend, Funnel, and
  mutable update sources.

### Physical

- both hosts menu/duplicate launch and desktop reboot;
- small/9B profiles under thermal observation;
- two tailnet clients and independent revoke;
- PocketPal and Open WebUI alias visibility;
- repair/cleanup interruption recovery;
- signed app update/rollback only if advertised;
- eight-hour mixed GUI/chat/API/maintenance soak.

---

## 15. Explicitly rejected or deferred ideas

- **System tray daemon:** desktop-dependent, always-running, and duplicates
  worker/systemd truth. Use optional notices and normal launch.
- **Automatic updates:** apply remains explicit after trust/backup/rollback.
- **Public Funnel/raw forwarding:** supported remote mode is authenticated
  tailnet HTTPS through the gateway.
- **Credential QR code:** easy to retain/leak; reconsider only with reviewed
  ephemeral credentials.
- **Telemetry/crash upload:** support bundles stay local and user-controlled.
- **Plugin marketplace:** unnecessary supply-chain/UI scope.
- **Automatic model-quality ranking:** fit and measured performance are
  evidence; subjective quality ranking is not.
- **Generic one-click repair:** repairs have different authority/reversibility.
- **On-the-fly UMA repartitioning:** unsupported; preserve 12/4.
- **Electron/browser/Qt/animation/online assets:** violates the lightweight
  native-window goal.

---

## 16. Final acceptance criteria

- Normal install/menu launch requires no remembered venv path.
- Repeated launch yields one window and refresh owner.
- Core setup/management remains inside that window.
- PocketPal works with exactly the displayed URL/key/model.
- Credentials are independently revocable and never leak.
- Profiles expose goals/evidence while fit/thermal gates remain authoritative.
- Maintenance prioritizes risk without expensive refresh work.
- Notices are optional, redacted, bounded, and non-authoritative.
- Repair/cleanup/undo are typed, previewed, fenced, recoverable, and verified.
- Updates accept only eligible signed artifacts or refuse honestly.
- Desktop/no-LLM next boot, mmap, vendor detection, standard layouts, and
  12 GiB fit policy remain green.
- Core journeys are keyboard-operable and scale-readable.
- No telemetry, tray, web frontend, automatic update, Funnel, or hidden host
  mutation is introduced.
- Exact-candidate developer, physical, security, usability, and release
  evidence is recorded, never inherited or fabricated.

---

## 17. First implementation handoff

After GUI-8 and its candidate decision, begin EXP-1 with documentation and red
tests only:

1. freeze desktop ownership/uninstall rules;
2. freeze single-instance protocol, peer validation, bounds, stale recovery;
3. add red tests for Exec escaping, no autostart, duplicate launch, wrong
   peer/nonce, oversized messages, and abrupt-owner recovery;
4. audit Bazzite KDE and CachyOS XDG paths on physical hosts without writing;
5. stop before production implementation and review evidence.

Do not begin migration 011, Connections, profiles, notifications, cleanup, or
updater work in that session.
