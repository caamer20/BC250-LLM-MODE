# BC250 LLM MODE — Unified Native GUI Implementation Plan

**Status:** Implementation-ready plan; implementation not started

**Plan IDs:** GUI-1 through GUI-8

**Planning baseline:** `18aa49f` on `main`

**Package-code baseline:** `ccd1777` (`0.9.0.dev0`, schema v10)

**Developer-qualified baseline:** 1174 default tests (1172 passed + 2 platform
skips), 52/52 slow battery, and 43/43 focused platform/bootstrap/setup tests
as recorded in `AGENTS.md`

**Product-experience authority:** `END_USER_EXPERIENCE_IMPLEMENTATION_PLAN.md`

**Safety, durability, and release authorities:** `MASTER_IMPLEMENTATION_PLAN.md`,
accepted ADRs, and `V1_0_RELEASE_CLOSURE_IMPLEMENTATION_PLAN.md`

**Post-GUI lifecycle successor:**
`APPLIANCE_EXPERIENCE_COMPLETION_IMPLEMENTATION_PLAN.md` (EXP-1 through EXP-8)

This plan turns the existing tkinter wizard/dashboard into one coherent,
attractive, resource-conscious desktop application. It does not replace the
durable operation engine, typed services, host-platform boundary, CLI, or
release controls. It changes how those capabilities are presented and driven.

---

## 1. Objective

The application should feel like a small local-AI appliance, not a setup
script followed by a crowded administration page.

The finished experience uses exactly one application window from launch to
shutdown. That window supports:

1. first-run hardware validation and the mandatory safety acknowledgment;
2. resumable setup with clear progress and reboot/relaunch handoffs;
3. a returning-user home screen with one obvious next action;
4. one model library for installed, discovered, and downloadable models;
5. native streaming chat for users who do not want a terminal or Open WebUI;
6. embedded durable-operation activity, progress, recovery, and cancellation;
7. system, thermal, network, runtime, backup, and optimization management;
8. contextual logs, diagnostics, and support tools without permanent clutter.

It must remain lightweight enough for the BC-250's approximately 3.6 GiB host
RAM. The implementation stays on Python/tkinter/ttk, adds no browser runtime,
Electron, Qt, webview, local web frontend, image framework, or animation
engine, and never loads model files or unbounded logs into GUI memory.

---

## 2. Meaning of “one window”

The current application has one `Tk` root, but the user journey is not truly
unified: Activity opens a `Toplevel`, chat opens a terminal, Open WebUI opens a
browser, errors are mostly message boxes, and setup/management use different
mental models inside a rebuilt content frame.

At the GUI-8 exit gate, “one window” means:

- exactly one `tk.Tk` instance;
- zero application-owned `tk.Toplevel` windows;
- Activity is a main-shell page plus a compact global activity shelf;
- native chat is a main-shell page;
- confirmations, errors, warnings, and success notices render inside the
  shell rather than as application-owned message boxes;
- logs open in an in-window drawer;
- setup and management use the same navigation, header, status language,
  action system, and visual components;
- long work does not create a separate progress window;
- switching pages never creates another root or duplicates polling loops.

Explicit handoffs to an external application remain allowed, but must be
clearly labeled as such:

- **Open Open WebUI in browser**;
- **Open terminal chat** under Advanced tools;
- the OS file/folder chooser;
- `pkexec`/`sudo` authorization UI;
- opening a generated support artifact in the desktop file manager.

These are user-requested external tools, not a fragmented primary workflow.

---

## 3. Hard boundaries

This GUI program must not:

- change durable operation semantics, state vocabularies, leases, recovery,
  or compensation rules;
- invent a second worker, executor graph, model activation path, acquisition
  path, runtime lifecycle, backup path, or host-mode path;
- call systemd, Podman, Distrobox, Pacman, rpm-ostree, Tailscale, Vulkan tools,
  or HTTP endpoints directly from a widget module;
- bypass `calculate_fit`, the thermal latch, artifact validation, privilege
  plans, or the `RECOVERY_REQUIRED` resource barrier;
- weaken the exact safety disclaimer or allow any setup mutation before it is
  acknowledged;
- enable model auto-start or change the desktop-on-next-boot invariant;
- persist host-platform observations as durable truth;
- expose Cyan controls on a host that did not report the capability;
- put secrets, prompts, completions, raw server bodies, or credentials into
  logs, operation history, notifications, or support details;
- read an entire GGUF, server log, conversation archive, or operation history
  into RAM;
- add automatic updates, telemetry, advertising, or network font/icon loads;
- claim physical Bazzite/CachyOS qualification without new candidate-bound
  hardware evidence.

The CLI remains a supported peer interface over the same composed services.
The terminal chat remains available. The new GUI is not allowed to become a
new authority that makes CLI behavior diverge.

---

## 4. Current-flow audit

### 4.1 What is already strong and should be reused

- A native tkinter package already opens locally without a browser.
- The same root currently transitions from setup into management.
- Setup is resumable and the mandatory disclaimer is enforced.
- The application has one composed service graph and SQLite source of truth.
- Durable operations already cover activation, acquisition/import, runtime
  update/rollback, model removal, and backup/restore.
- `HomeQueryService` exposes one bounded query-only appliance snapshot.
- `ModelLibraryQueryService`, storage capacity, doctor, support bundles, and
  the Activity control plane already exist.
- Pure presentation contracts exist in `home_ux.py`, `conversation_ux.py`,
  `benchmark_ux.py`, and Activity helpers.
- Chat lifecycle types already define bounded deadlines, cancellation,
  classification, privacy, and retry rules.
- Host detection and mutation are capability-driven through one composed
  `HostPlatformService`/`HostModeService` boundary.

### 4.2 Current usability and resource problems

| Problem | User effect | Technical cause |
| --- | --- | --- |
| Eleven equally weighted setup screens | Setup feels long and mechanical | Canonical implementation phases are exposed directly as navigation |
| Setup log always occupies the bottom of the window | Primary content feels cramped | Log pane is part of the permanent shell |
| Completed dashboard is one long scroll | Important actions compete with expert tools | All management capability renders on one canvas |
| Service cards show every Start/Stop/Restart button | Users must reason about impossible actions | Buttons are not derived from current state |
| Model library is split across installed and catalog tables | Downloaded, installed, active, and verified states are unclear | Separate dashboard sections predate the unified library read model |
| Activity opens a second window | Work status is detached from the action that started it | `ActivityCenterFrame` is hosted in a `Toplevel` |
| Primary chat launches a terminal | Native-GUI users leave the application | No tkinter chat workspace consumes the shared lifecycle contract |
| Message boxes interrupt flow | Errors lose context and confirmations feel abrupt | No in-shell notification/confirmation component |
| Direct imports in GUI modules | Widget code owns too much orchestration | Dashboard/forms import server, model manager, Tailscale, Open WebUI, optimize |
| Thread per action | Repeated actions can create avoidable threads | `_work` starts a new daemon thread for every call |
| Multiple independent timers | Refresh work can duplicate and outlive pages | shell, dashboard, and Activity schedule separate `after` loops |
| Full page rebuild on navigation | Selection/focus is lost and large pages churn widgets | `_clear()` destroys every content child |
| Raw JSON hardware summary | First screen reads like diagnostics | Read model is dumped into a disabled `Text` widget |
| Low-level settings are prominent | New users choose batch/KV/clock values too early | No progressive disclosure or workload-profile layer |
| Logs and exception text dominate failures | Recovery action is not obvious | Inline stable-code guidance is not the default rendering |

### 4.3 Architectural debt exposed by the redesign

The redesign must remove, not merely restyle, these GUI ownership problems:

- `gui/app.py` imports model-manager, server, Open WebUI, optimize, and
  Tailscale implementation modules;
- `gui/steps.py`, `gui/forms.py`, and `gui/dashboard.py` repeat many imports;
- `Wizard` is a large multiple-inheritance surface frozen by a method-name
  contract rather than user behavior;
- the GUI still has a transitional `commit_narrow()` path;
- terminal chat owns transport and local conversation-file behavior directly,
  so a native GUI cannot yet consume a clean composed chat service;
- live service observations are assembled ad hoc rather than through one
  bounded desktop observation facade.

These are conversion targets, but not permission to rewrite the operation
engine or persistence model.

---

## 5. Experience principles

### 5.1 One clear next action

Every page has at most one visually dominant primary action. Secondary actions
use normal buttons or menus. Disabled actions state why they are unavailable.

### 5.2 Plain language first, evidence one click away

Default copy answers:

- What is happening?
- Is the machine safe?
- What can I do now?
- What will change if I continue?
- Can it be undone?

Stable codes, revisions, fingerprints, paths, and raw bounded logs belong in a
Details drawer, not the headline.

### 5.3 Progressive disclosure

The default interface uses workload goals and recommended values. Quantization,
KV format, batch/ubatch, GPU ranges, restart windows, swappiness, and service
trimming remain available under **Advanced** sections with their existing
bounds and fit rechecks.

### 5.4 State is visible, not guessed

Downloaded, validated, installed, active, verified, stopped, busy, paused,
rolled back, and recovery-required are distinct labels. A green status always
names its evidence age. Stale data is labeled stale.

### 5.5 Durable work remains durable

An action that returns an operation ID immediately appears in the global
activity shelf. Closing the GUI does not fabricate cancellation or success.
On reopen, the shell highlights paused/recovery-required work and offers only
actions permitted by `OperationCommandService`.

### 5.6 Safe defaults survive visual polish

No color, animation, convenience toggle, preset, or “quick start” bypasses the
same service and validation path used by the CLI.

---

## 6. Information architecture

Use one persistent shell with a narrow left navigation rail, a header, one
content viewport, a global activity shelf, and an optional bottom drawer.

```text
+-----------------------------------------------------------------------+
| BC250 LLM MODE        Current model / safety status       Activity  1 |
+-------------+---------------------------------------------------------+
| Home        |  Page title                         contextual actions  |
| Models      |---------------------------------------------------------|
| Chat        |                                                         |
| Activity    |                    active page                          |
| System      |                                                         |
| Settings    |                                                         |
| Help        |                                                         |
|             |                                                         |
| Desktop next|                                                         |
| boot: safe  |                                                         |
+-------------+---------------------------------------------------------+
| Operation shelf: Downloading model… 43%        View · Cancel          |
+-----------------------------------------------------------------------+
| Optional drawer: Details / Logs / Confirmation                        |
+-----------------------------------------------------------------------+
```

### 6.1 Persistent regions

- **Navigation rail:** text labels, semantic state dot, keyboard shortcuts.
- **Header:** current page, current model/profile, thermal/recovery warning,
  refresh age, one contextual primary action.
- **Content viewport:** exactly one active page; inactive pages hold only small
  view-state objects, not complete widget trees.
- **Activity shelf:** hidden when idle; shows the highest-severity or most
  recent active operation and opens Activity in the same viewport.
- **Bottom drawer:** mutually exclusive Details, Logs, Confirmation, or Help;
  closed by default and height-bounded.

### 6.2 Navigation availability

Before setup completes:

- Setup is the active route.
- Home shows only a resumable setup summary.
- Activity and Help are available.
- Models, Chat, System mutations, and advanced Settings are disabled with an
  explanation, not silently absent.

After setup completes:

- Home is the default route.
- Setup moves under **System → Repair setup** and reuses the same onboarding
  page stack.
- Every normal capability is reachable without returning to wizard step 10.

### 6.3 Route vocabulary

Use a closed route enum:

```text
SETUP, HOME, MODELS, CHAT, ACTIVITY, SYSTEM, SETTINGS, HELP
```

Deep links may include a selected model, operation ID, system section, or
conversation ID, but they do not create new windows.

---

## 7. Visual system

Create a tiny design-token layer in `gui/theme.py`; do not add an asset or CSS
pipeline.

### 7.1 Tokens

- Spacing: 4, 8, 12, 16, 24, 32 pixels.
- Corner illusion: ttk frames with border/padding; no expensive canvas-rounded
  rectangles.
- Typography: system/Tk default family; 24 px hero, 18 px page title, 14 px
  section title, 11–12 px body, 10 px metadata.
- Minimum actionable height: 34 px; primary controls target 40 px.
- Content line length: approximately 70–90 characters.
- Card padding: 12–16 px.
- Table row height: large enough for keyboard focus and high-DPI displays.

### 7.2 Semantic palette

Provide Light, Dark, and System preference tokens using ttk styles only:

- accent: cool cyan/blue associated with the appliance, used sparingly;
- ready: green;
- busy/attention: amber;
- blocked/thermal/recovery: red;
- inactive/unknown: neutral gray;
- surfaces and text must meet WCAG AA contrast where tkinter can represent it.

Never communicate state by color alone. Every badge includes text such as
`Ready`, `Working`, `Needs attention`, `Stopped`, or `Unverified`.

### 7.3 Motion

- No decorative animation.
- An indeterminate progress bar is allowed only when total work is genuinely
  unknown.
- Coalesce streaming/progress updates; do not redraw for every token or log
  line.
- Respect a reduced-motion preference by disabling pulse effects entirely.

### 7.4 Window sizing

- Initial target: 1100 × 700, centered within the current display work area.
- Minimum: 860 × 600, usable on common 1366 × 768 BC-250 displays.
- At narrow widths, navigation collapses to compact text initials with an
  accessible tooltip; content remains vertically scrollable.
- No page assumes a fixed 820 px wrap length; wrap length follows viewport
  width through one debounced resize handler.

---

## 8. First-run setup flow

Keep the canonical durable setup stages and resumption data, but group them
into five user-facing chapters. The UI must never lie about which underlying
stage is running.

### Chapter 1 — Welcome and machine check

Show:

- detected host profile (Bazzite or CachyOS) and qualification label;
- BC-250 detection, VRAM, host RAM, disk, Vulkan, and 12/4 memory-profile
  result as readable status rows;
- a concise “what this app changes” summary;
- read-only blockers and their exact remediation.

This chapter may run read-only detection before acknowledgment. It performs no
host or database mutation beyond normal application initialization.

### Chapter 2 — Safety acknowledgment

- Render the exact mandatory disclaimer text.
- Preserve all three checkboxes and exact typed `I ACCEPT` requirement.
- Keep Continue disabled until all conditions are true.
- Show that the acknowledgment is saved locally and name the thermal/BIOS/CU
  responsibilities in plain language.
- No system-changing action is available elsewhere in the shell until this
  gate is durable.

### Chapter 3 — Prepare this system

Combine the presentation—not the transaction boundaries—of:

- current-boot LLM Mode;
- platform-specific Tk/dependency/relaunch or reboot state;
- container/toolchain provisioning;
- durable pinned llama.cpp runtime installation;
- Vulkan smoke verification.

Before Start, show a review card:

- commands/capability plan in plain language;
- whether authorization is required;
- whether a reboot/relaunch will be required;
- next boot remains graphical with model auto-start off;
- optional desktop-service trimming is off by default.

While running, show the current durable phase and contextual log excerpt in the
drawer. A reboot-required state becomes a dedicated resume screen, not an
error.

### Chapter 4 — Choose how you will use it

Ask for a goal before expert controls:

- **Everyday assistant** — balanced quality and speed;
- **Long documents** — smaller model, larger context;
- **Several users** — smaller KV footprint and multiple slots;
- **Best quality on this card** — larger model, conservative context;
- **Advanced/custom** — exposes quant/context/slots directly.

Each goal produces two or three fitting recommendations from the existing
catalog/library logic. The user can still browse all models. Every suggestion
shows:

- installed/download required;
- expected disk use;
- weights + KV + overhead fit explanation;
- context per user and parallel slots;
- FITS/TIGHT/NO-FIT;
- validated/expected support tier;
- standard-layout-only guarantee.

### Chapter 5 — Review, install, and verify

One review screen lists the chosen model/profile and every planned operation.
The primary action is **Install and start**. Optional Open WebUI is a secondary
choice, off unless already installed.

The UI then follows durable acquisition/import, preparation, activation,
health, and inference evidence. It must distinguish:

- downloading;
- validating;
- installed;
- active;
- inference verified.

Success ends on a Ready screen with **Start chatting** as the primary action,
**Open Open WebUI in browser** secondary, and **View system details** tertiary.

### Setup resume mapping

Add a pure mapping from canonical `setup_stage`/phase/active operations to the
five chapters. Tests cover every existing stage, reboot/relaunch requirement,
paused operation, recovery barrier, and completed setup. Never infer progress
from widget position.

---

## 9. Returning-user Home

Home is a decision page, not a dump of every control.

### 9.1 Hero decision

Derive one headline and one primary action from the composed home snapshot:

| Highest-priority truth | Headline | Primary action |
| --- | --- | --- |
| Thermal latch stopped | Cooling required | View thermal safety |
| Recovery required | This appliance needs attention | Open recovery activity |
| Setup incomplete | Finish setting up this BC-250 | Resume setup |
| Operation active | Work is in progress | View activity |
| Model active and verified | Ready to chat with `<model>` | Start chatting |
| Known-good model stopped | `<model>` is ready but stopped | Start model |
| No installed model | Choose your first model | Browse models |
| Stale/unknown evidence | Status needs refreshing | Run checks |

Safety/recovery always outranks convenience.

### 9.2 Compact health cards

Render the existing home dimensions as five default cards:

- Model & inference;
- Temperature & memory;
- Active work;
- Storage;
- Remote access.

Host/runtime/backup/identity detail remains available under **More details**.
Stale cards show their age and never render green.

### 9.3 Quick actions

Limit Home to four contextual shortcuts, for example:

- Start/Stop model;
- Browse models;
- Open Open WebUI;
- Run benchmark.

Runtime updates, rollback, repair, host mode, logs, Tailscale daemon controls,
and optimizations move to their task-specific pages.

---

## 10. Unified Models page

Replace the separate installed-model and catalog sections with one library
fed by `ModelLibraryQueryService` plus catalog fit projections.

### 10.1 Layout

- Search/filter row: Recommended, Installed, Long context, Multi-user, All.
- Left/main list: name, family, size, lifecycle state, fit badge.
- Right/detail pane: description, provenance/support, quant, storage, fit
  breakdown, context/slots controls, and actions.
- On narrow layouts, detail opens below the list in the same page.

### 10.2 Lifecycle language

Every entry uses a closed presentation state:

```text
AVAILABLE, DOWNLOADING, VALIDATING, INSTALLED, ACTIVE, VERIFIED,
QUARANTINED, REMOVING, RECOVERY_REQUIRED
```

Do not label a file “installed” merely because it was discovered. Do not label
an active server “verified” without current evidence.

### 10.3 Actions

- Remote candidate: **Install and start** primary, **Install only** secondary.
- Installed inactive: **Start this model** primary.
- Active verified: **Open chat** primary.
- Quarantined: **View validation issue**.
- Busy: **View activity**, not another mutation.
- Remove: preview exact managed bytes and recovery policy; never delete a
  source outside managed storage.

Selection never mutates state. Context/slot edits are drafts until the user
chooses Apply; activation still uses the one durable fit-gated workflow.

---

## 11. Native Chat page

The lightweight native chat is the primary local conversation surface.
Open WebUI stays optional and terminal chat remains available under Advanced.

### 11.1 Shared service extraction

Before adding widgets, extract the terminal-owned transport and local history
behavior into composed, frontend-neutral services:

```text
ChatSessionService       bounded OpenAI-compatible streaming + cancellation
ConversationService      atomic local save/load/list/rename/archive/delete
ChatObservationService   active model/context/slots + server readiness
```

Both terminal and native clients consume the existing `chat_lifecycle.py` and
`conversation_ux.py` contracts. There is one timeout, retry, privacy, token
budget, and classification policy.

### 11.2 Layout

- Optional narrow conversation list with New/Search/Archived.
- Main transcript using a read-only `tk.Text` with role tags.
- Compact model/context/slots indicator.
- Multi-line composer and **Send** / **Stop generating** action.
- Streaming status: waiting, tokens, tok/s, first-token time.
- Inline notice when the active model changed since the prior message.

### 11.3 Rendering and memory bounds

- No embedded browser or full Markdown engine.
- Render plain text plus lightweight tags for code blocks, headings, and
  emphasis; links require explicit click before opening externally.
- Coalesce token updates to at most 20 UI flushes per second.
- Bound the live widget transcript by message count and encoded size; load
  older messages on demand.
- Never copy prompt/completion text into application logs, operation events,
  support bundles, or metrics.
- Stop uses `ChatCancellation`; closing/navigating asks whether to stop an
  active stream but never kills the model service.

### 11.4 Failure behavior

Map lifecycle classifications to inline recovery:

- server unavailable -> Start model / View system;
- model mismatch -> Refresh profile / Re-send;
- thermal stop -> View thermal safety;
- timeout -> Retry only if no token was emitted;
- malformed response -> Copy redacted diagnostics;
- cancelled -> preserve the user message and mark the response stopped.

---

## 12. Integrated Activity and logs

### 12.1 Global activity shelf

The shelf consumes `OperationQueryService.active_summary()` through the single
refresh coordinator. It shows one operation at a time using severity-first,
then recency ordering. It never performs a mutation itself except via a button
whose availability comes from the operation summary flags.

### 12.2 Activity page

Move `ActivityCenterFrame` into the main content viewport. Preserve its pure
headline/progress/message/action functions, but improve the layout:

- active/needs-attention/recent filters;
- operation list with human title, state, progress, and updated age;
- detail pane with phase timeline and stable evidence;
- action bar generated only from `OperationCommandService`;
- support-details copy remains bounded and path/secret safe.

Delete the `Toplevel` host and its independent polling timer.

### 12.3 Log drawer

Replace the permanent setup log pane with one bottom drawer:

- sources: current operation, server, setup, worker;
- default last 200 lines, maximum 2000 retained in widget memory;
- append batches, trim oldest lines, and pause follow when the user scrolls;
- search is bounded to loaded lines;
- raw logs are never added to durable operation detail;
- file reads occur through a typed, read-only bounded log-tail service, not
  `tail` constructed in the GUI.

Errors show stable guidance inline and offer **Open relevant log** in the
drawer. The log is evidence, not the primary explanation.

---

## 13. System, Settings, and Help pages

### 13.1 System

Organize by user task:

- **Model server:** current service state, Start/Stop, Restart under More;
- **Web interface:** Open WebUI install/state/open action;
- **Remote access:** Tailscale and authenticated HTTPS gateway status;
- **Host mode:** current boot mode, desktop-next-boot guarantee, enter LLM
  mode, return to desktop;
- **Thermals:** current sensor, latch, thresholds, degraded governor status;
- **Runtime:** pinned llama.cpp build, update, retained rollback target;
- **Backups & recovery:** latest backup, create/verify/restore flows.

Each card derives available actions from current state. Do not show Start and
Stop as equal choices simultaneously.

### 13.2 Settings

Default sections:

- Workload profile;
- Chat defaults;
- Model folders;
- Appearance and reduced motion;
- Notifications.

Advanced sections:

- llama.cpp runtime settings;
- GPU tuning when capability exists;
- thermal/restart safeguards;
- memory and optional service trimming.

Every advanced edit is staged, validated, fit-projected, and applied with a
summary of restarts/host changes. **Discard changes** is always available.

### 13.3 Help

- Quick start and explanation of Desktop vs current-boot LLM Mode;
- platform status and qualification label;
- doctor findings;
- create redacted support bundle;
- copy diagnostic summary;
- open README/support URLs explicitly;
- Advanced tools: terminal chat and CLI command reference.

Repair and destructive actions belong in a visually separated recovery area,
never alongside everyday controls.

---

## 14. In-shell notifications and confirmations

Replace `messagebox` usage with a shell-owned notice system:

```text
Notice(level, title, message, action?, details?, dismissible?)
Confirmation(title, consequence, recovery, confirm_label, destructive)
```

- Notices render below the page header and remain associated with the action.
- Only one confirmation occupies the bottom drawer at a time.
- Destructive confirmations name exact scope and recovery.
- Typed safety confirmations remain typed where required.
- Errors include stable code, operation ID when applicable, and a direct route
  to Activity/log details.
- Unexpected exception text is sanitized before display and never persisted.
- Toast-like success can auto-dismiss; warnings/recovery never auto-dismiss.

OS authorization dialogs and file choosers remain external exceptions.

---

## 15. GUI architecture

### 15.1 Target module layout

Keep the existing `bc250_llm_mode.gui` package flat to avoid unnecessary
subpackage/packaging overhead:

```text
gui/
  __init__.py           run_gui + compatibility export
  shell.py              the only Tk root and persistent regions
  theme.py              design tokens and ttk styles
  routes.py             closed routes + navigation state
  view_state.py         immutable page/shell presentation records
  tasks.py              bounded action/query/chat execution lanes
  refresh.py            one adaptive refresh coordinator
  widgets.py            cards, badges, notice, drawer, empty/error states
  setup_page.py         five-chapter onboarding over canonical stages
  home_page.py          hero decision + compact health
  models_page.py        unified model library
  chat_page.py          native bounded streaming chat
  activity_page.py      embedded operation control plane
  system_page.py        services/platform/thermal/runtime/backup
  settings_page.py      basic + advanced staged settings
  help_page.py          doctor/support/advanced tools
```

The exact split may be adjusted to keep modules cohesive, but no new monolith
may replace the old mixins.

### 15.2 Root and page contract

```text
ApplicationWindow(tk.Tk)
Page.mount(parent)
Page.enter(route_context)
Page.refresh(snapshot)
Page.leave()
Page.dispose()
```

- Only the active page owns widgets.
- `leave()` cancels page-local callbacks and preserves a small draft if safe.
- `dispose()` releases bindings, variables, and transcript/table references.
- Page navigation is UI state, not durable appliance state.
- `Wizard` remains a temporary alias/wrapper for compatibility, then the old
  method-name freeze is replaced by behavior contracts and removed.

### 15.3 Presentation boundary

Add pure view-model builders for every page. They accept composed read models
and typed command results and return frozen records. Widget modules do not
interpret database rows, parse logs, calculate fit independently, or infer
operation permissions.

### 15.4 Command boundary

Widget modules call only composed services or one narrow desktop command
facade that delegates to those services. The final architecture guard forbids
GUI imports of:

```text
server, openwebui, tailscale, sharing, llmmode, desktop, optimize,
model_manager, repositories, db, unit_of_work, subprocess, sqlite3
```

If a missing typed command is discovered, add it to the owning domain service
outside `gui/`; do not hide a direct module call in a callback.

### 15.5 Transitional persistence removal

Eliminate GUI `commit_narrow()` by moving remaining preferences/setup choices
to typed services with optimistic revisions. Do not reintroduce generic state
saves or writable JSON. No schema migration is expected because `settings`
already stores typed keys; if a migration becomes necessary, stop and review
it separately rather than smuggling it into a visual refactor.

---

## 16. Refresh, concurrency, and lifecycle

### 16.1 Bounded execution lanes

Replace “one daemon thread per click” with three bounded lanes:

1. **Action lane:** one worker; serializes foreground GUI commands.
2. **Observation lane:** one worker; live status/home/model/log queries.
3. **Chat lane:** at most one active stream.

No unbounded executor queue. Repeated refresh requests coalesce to the newest
request. Repeated mutation clicks are rejected while the action is pending.
The durable operation engine remains authoritative; these lanes merely keep
blocking work off the Tk event loop.

### 16.2 One refresh coordinator

Exactly one `after` token drives refresh scheduling:

- active operation/chat: 1 second for status, with progress events coalesced;
- idle visible window: 5 seconds;
- minimized/unmapped: 30 seconds or event-only;
- inactive page details are not queried;
- service/network probes use the observation lane and never overlap the same
  probe;
- window close cancels the timer, rejects new work, and disposes lanes in a
  bounded order.

Tk calls happen only on the main thread. Background results cross through a
bounded event queue and are ignored if their page generation is stale.

### 16.3 Close behavior

- No active work: close immediately.
- Durable operation active: explain that work is durable and whether the
  external worker will continue; offer Close or View Activity.
- Foreground-only operation paused by closing: state this honestly.
- Chat streaming: offer Stop and close, or remain.
- Never mark an operation cancelled merely because the window closed.

---

## 17. Resource budgets

### 17.1 Deterministic structural limits

- One `Tk`, zero application `Toplevel`.
- Maximum three GUI-owned background threads.
- One refresh timer.
- Event queue maximum 512 items; merge duplicate refresh/progress/log events.
- Activity list maximum 100 visible rows; page older records on demand.
- Model list uses bounded page/filter results and lazy details.
- Log widget maximum 2000 lines and 2 MiB encoded text.
- Live chat widget maximum 500 messages or 4 MiB encoded text; older history
  remains on disk and loads on demand.
- Support/diagnostic text retains existing 8 KiB bound.
- No image larger than a small packaged icon set; initial plan uses text and
  ttk styling only.
- Destroy inactive heavy pages and release bindings/references.

### 17.2 Physical BC-250 performance targets

Measure on both Bazzite and CachyOS candidates:

- first meaningful window paint within 1.5 seconds after tkinter is available;
- idle GUI RSS at or below 90 MiB after two refresh cycles;
- idle CPU below 1% average over 60 seconds with no active operation/chat;
- no more than 5 MiB retained growth after 100 route changes;
- UI-thread callbacks below 50 ms p95 during idle/status refresh;
- token streaming remains responsive while a model is generating;
- setup/download/build progress stays responsive under 4 GiB host pressure;
- window remains usable at 860 × 600 and common 125%/150% scaling.

These are physical evidence targets, not flaky generic-CI wall-clock tests.
Developer tests enforce the structural causes; qualification records the live
measurements and tools used.

---

## 18. Accessibility and keyboard behavior

- Stable tab order follows visual order.
- `Ctrl+1..7` navigates major pages; `Ctrl+L` opens logs; `Ctrl+K` focuses the
  page search/action field; `Esc` closes drawer/confirmation.
- Every button has a unique accessible text label; icon-only actions are not
  used for essential controls.
- Focus moves to the page heading after navigation and to inline error text
  after failed validation.
- Tables support arrow keys, Enter for primary action, and a visible focus
  indicator.
- Status changes update a textual live-status label, not color alone.
- Font scale respects Tk/system scaling; layout tests cover enlarged text.
- Safety disclaimer remains fully keyboard operable.
- Do not depend on hover for required information.

---

## 19. Implementation sequence

### GUI-1 — Freeze experience and presentation contracts

Add pure tests and records before widgets:

- route enum and navigation availability;
- home primary-action priority;
- five-chapter setup mapping;
- service-card action availability;
- model lifecycle labels/actions;
- notice/confirmation copy;
- close behavior;
- resource-limit constants and queue coalescing policy;
- Light/Dark/System semantic style tokens.

No production window change in this boundary.

### GUI-2 — Introduce the persistent shell

- Add `ApplicationWindow`, theme, navigation rail, header, viewport, activity
  shelf, and bottom drawer.
- Mount the existing wizard/dashboard inside a temporary Legacy route so the
  shell can land without losing behavior.
- Create one refresh coordinator and bounded task lanes, initially adapting
  legacy callbacks.
- Add one-root/no-Toplevel/timer/thread contract tests.

No workflow or domain behavior changes.

### GUI-3 — Convert first-run setup

- Implement five chapters over existing canonical setup stages.
- Preserve exact disclaimer, hardware gate, platform plans, reboot/relaunch,
  LLM Mode safety, and resumability.
- Replace raw JSON with readable status rows and an expandable technical view.
- Route acquisition/runtime/activation operation IDs into the global shelf.
- Replace setup message boxes with inline notices/confirmations.
- Delete converted legacy setup renderers immediately; do not keep two paths.

### GUI-4 — Convert Home and Models

- Implement hero decision and compact health cards from `HomeQueryService`.
- Implement one library from `ModelLibraryQueryService` and catalog fit data.
- Move context/slot activation, acquisition/import, remove, and benchmark
  actions behind composed services.
- Remove installed/catalog sections from the legacy dashboard as each lands.
- Add route deep links from Home failures and operations.

### GUI-5 — Integrate Activity, System, Settings, Help, and logs

- Host Activity in the main viewport and delete `Toplevel` behavior.
- Add the activity shelf and one coordinator-driven refresh.
- Add bounded log-tail service and drawer; remove permanent setup log.
- Convert service/platform/thermal/runtime/backup/network actions.
- Convert optimization form to staged Basic/Advanced settings.
- Add doctor/support Help page.
- Remove direct infrastructure imports from all GUI modules.

### GUI-6 — Add native chat

- Extract shared chat transport/session/conversation services from terminal
  code without changing lifecycle/privacy semantics.
- Migrate terminal chat to the shared services first or in the same commit so
  only one transport policy exists.
- Add conversation list, transcript, composer, Send/Stop, status, errors,
  model-change notice, and bounded history.
- Make **Start chatting** route here; keep terminal/Open WebUI explicit extras.

### GUI-7 — Resource, accessibility, and lifecycle hardening

- Enforce queue/list/transcript/log bounds.
- Coalesce resize, token, progress, and refresh work.
- Add narrow-window/high-DPI/keyboard/focus/reduced-motion tests.
- Add deterministic no-timer-multiplication and stale-result tests.
- Add slow Linux/Xvfb smoke where available.
- Measure developer-host RSS/thread/timer trends; reserve physical thresholds
  for qualification.

### GUI-8 — Remove legacy surface and qualify the package

- Delete old mixins/monolithic dashboard and `Wizard` method-name freeze.
- Keep a documented `Wizard = ApplicationWindow` compatibility alias only if
  an external import still requires it; otherwise remove it deliberately.
- Update README, Architecture, screenshots/manual steps, packaging lists, and
  command audit.
- Build clean wheel/sdist and prove GUI package completeness.
- Run full default/slow/focused suites.
- Recollect physical Bazzite and CachyOS evidence for the new package commit.

Stop if physical evidence is unavailable; report developer-qualified GUI with
evidence pending rather than claiming release qualification.

---

## 20. Commit boundaries

Suggested independently green commits:

```text
docs(GUI): define unified native window implementation plan
test(GUI-1): freeze desktop experience contracts
feat(GUI-2): add persistent lightweight application shell
feat(GUI-3): unify resumable setup experience
feat(GUI-4): add task-oriented home and model library
feat(GUI-5): integrate activity system settings help and logs
feat(GUI-6): add native bounded streaming chat
perf(GUI-7): bound refresh rendering and desktop resources
refactor(GUI-8): remove legacy wizard dashboard surface
docs(GUI-8): record qualification evidence and handoff
```

Do not mix schema, release-policy, host-platform, operation-engine, or model
catalog changes into these commits unless a separately reviewed blocker proves
they are required.

---

## 21. Test strategy

### 21.1 Pure presentation tests

- every home state maps to exactly one primary action;
- recovery/thermal always outrank chat/start convenience;
- every setup stage maps to one chapter and honest progress;
- model states/actions are closed and mutually coherent;
- service cards never offer impossible peer actions;
- notices and confirmations state consequence/recovery;
- stale evidence never renders ready;
- advanced-control availability follows platform capability;
- exact disclaimer gate remains unchanged.

### 21.2 Headless widget contracts

- real `ApplicationWindow` constructs with tkinter stubs;
- every route mounts, enters, refreshes, leaves, and disposes;
- navigation does not leak page widgets or duplicate bindings;
- one root, no application Toplevel, no messagebox ownership;
- activity shelf opens Activity in-place;
- logs open/close in the drawer;
- focus and shortcuts route correctly;
- no widget module imports persistence/host infrastructure;
- no GUI callback performs a whole-state commit.

Replace the old frozen method-name list with behavioral assertions. A refactor
should be free to rename private methods while preserving the experience.

### 21.3 Bounded concurrency tests

- repeated refresh signals coalesce;
- queue never exceeds its cap;
- stale page-generation results are ignored;
- a double-click cannot enqueue the same mutation twice;
- route changes do not multiply timers;
- close cancels timers and drains lanes without Tk calls off-main-thread;
- active durable operations remain visible after GUI reconstruction;
- chat cancellation and operation cancellation stay distinct.

### 21.4 Domain-routing tests

- model start/context/slots -> one durable activation command;
- model install/import -> one acquisition command;
- runtime update/rollback -> one lifecycle command;
- backup/restore -> one backup command;
- Activity actions -> `OperationCommandService`;
- host actions -> one `HostModeService` and platform capability gate;
- Open WebUI/sharing/gateway -> their composed typed services;
- no direct `systemctl`, Podman, Tailscale, Pacman, rpm-ostree, or HTTP call in
  GUI source.

### 21.5 Native-chat tests

- bounded streaming success;
- first-token/status coalescing;
- Stop classification and partial-response handling;
- timeout before tokens may retry once; after tokens never retries;
- thermal/model mismatch/server unavailable guidance;
- model-change notice;
- atomic history save/load/rename/archive/delete;
- prompt/completion canaries absent from logs, operation rows, support bundle,
  and diagnostics;
- terminal and GUI clients share identical lifecycle classifications.

### 21.6 Packaging and platform tests

- wheel contains every new GUI module;
- installed entry point opens the same shell contract;
- Tk bootstrap remains Bazzite/CachyOS capability-driven;
- no Tk import is required for pre-composition `platform status|plan`;
- headless invocation fails with clear local-display guidance;
- clean-wheel schema init, status, worker, and GUI import smoke pass;
- Bazzite and CachyOS physical window launch, setup resume, host-mode, model,
  chat, thermal, runtime rollback, backup/restore, and soak evidence are bound
  to the exact candidate.

---

## 22. Verification cadence

At each implementation commit:

```bash
PYTHONPATH=. .venv/bin/pytest -q <focused tests>
python3 -m compileall -q bc250_llm_mode tests
git diff --check
```

At GUI-3 onward, also run setup/bootstrap/platform and durable-operation tests.
At GUI-6 onward, run the complete chat/privacy battery. At GUI-8:

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/pytest tests --collect-only -q
.venv/bin/pytest -q -m slow
python3 -m compileall -q bc250_llm_mode tests
git diff --check
```

Also require:

- source/editable/clean-wheel collection parity;
- wheel and sdist build/verify;
- GUI architecture guards;
- release documentation-consistency and release-qualification tests;
- tracked-tree cleanliness while preserving the nine owner-controlled
  untracked files;
- physical resource measurements and end-to-end evidence on both advertised
  host profiles before any qualification claim.

The test collection hook is authoritative. Never infer totals from dots or
carry 1174 forward after tests are added.

---

## 23. Acceptance criteria

### First run

- A single attractive native window appears and stays open through setup.
- The user understands host/hardware readiness without reading JSON.
- The exact disclaimer blocks all mutations until acknowledged.
- Setup is presented as five chapters while preserving every canonical stage.
- Reboot/relaunch resumes at the correct chapter.
- A recommended model can be installed, activated, health-checked, and
  inference-verified without using a terminal or reading a log.
- Completion routes directly to native chat.

### Returning use

- Launch opens Home, not the final wizard step.
- Home shows one correct next action.
- Models, native chat, Activity, System, Settings, and Help are reachable in
  the same window.
- Activity never opens a second window.
- Logs are contextual and hidden until requested.
- Open WebUI and terminal chat are optional, clearly external alternatives.

### Safety and correctness

- One service owner, one durable operation graph, fit gate, thermal latch,
  desktop-next-boot, and recovery barriers remain intact.
- GUI widgets import no host/persistence implementation modules.
- No raw secret/prompt/completion/log material leaks into durable evidence.
- Platform-specific behavior always comes from the recomputed capability
  profile.
- Closing the app never fabricates operation success/cancellation.

### Resource use

- One root, zero app Toplevel, one refresh coordinator, at most three GUI
  background threads.
- All lists, queues, logs, transcripts, and copied details are bounded.
- Physical first-paint/RSS/CPU/navigation-growth targets pass on Bazzite and
  CachyOS or are reported honestly as evidence pending.

---

## 24. Release/evidence consequence

This plan is documentation-only and does not invalidate the package-code
candidate at `ccd1777`. Implementing GUI-1 through GUI-8 will change packaged
code and therefore creates a new candidate. All candidate-bound physical,
artifact, provenance, clean-wheel, security, soak, and acceptance evidence
must bind to that new commit. No prior artifact digest or hardware PASS record
may be reused as if it identified the redesigned GUI.

Before implementation starts, the owner must choose one of two honest paths:

1. finish C4 qualification on the current `ccd1777` package candidate, then
   reopen development for the GUI and qualify the later candidate again; or
2. deliberately supersede the current candidate, implement this plan, and
   collect C4 only for the resulting GUI candidate.

The application version is not bumped, tagged, published, or declared 1.0 by
this planning work.

---

## 25. Exact implementation checklist

1. Confirm package-code baseline `ccd1777` and preserve all owner-controlled
   untracked files.
2. Record the owner's candidate/evidence decision from §24.
3. Run authoritative default/slow/focused/compile/diff baseline.
4. Read this plan, End-User Experience plan, ADR 007, and current GUI tests.
5. Land GUI-1 pure contracts before changing widgets.
6. Introduce the shell around the legacy route; prove one root/timer/thread
   limits.
7. Convert and delete setup renderers chapter by chapter.
8. Convert Home and Models; delete their dashboard duplicates.
9. Integrate Activity and logs; delete Toplevel/permanent log behavior.
10. Convert System/Settings/Help through typed composed services.
11. Extract shared chat services; migrate terminal client and add native page.
12. Remove all GUI infrastructure imports and `commit_narrow()`.
13. Harden bounds, accessibility, close behavior, and stale-result fencing.
14. Delete the legacy mixin/dashboard surface and method-name freeze.
15. Run full/default/slow/clean-wheel/package/release gates.
16. Update README/Architecture/manual evidence instructions.
17. Collect exact-candidate physical Bazzite and CachyOS UX/resource evidence.
18. Stop and report evidence pending if either host is unavailable.

### Completion report format

- HEAD/package-code candidate and tracked/untracked status;
- commits in order with GUI plan IDs;
- routes/pages and legacy modules removed;
- one-window/root/timer/thread evidence;
- setup resume/disclaimer/platform evidence;
- composed-service routing and architecture guards;
- native-chat lifecycle/privacy evidence;
- resource bounds and physical measurements;
- authoritative default/slow/focused counts;
- source/editable/wheel/sdist/compile/diff results;
- Bazzite and CachyOS candidate-bound physical results;
- any deferred evidence or release blockers, with no fabricated PASS claim.
