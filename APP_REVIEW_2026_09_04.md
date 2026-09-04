# BC250 LLM MODE — application review

Reviewed 2026-09-04. Implementation plan: [FULL_APP_REVIEW_IMPLEMENTATION_PLAN.md](FULL_APP_REVIEW_IMPLEMENTATION_PLAN.md).

This document preserves the original findings. Their corrective implementation
and current verification status are recorded in
[the implementation report](docs/review-implementation-status.md).

## Assessment

The app has a substantial foundation: one native window, typed services, SQLite
transactions, durable operations, model-fit checks, immutable runtime updates,
named client credentials, local conversations, and explicit release gates. I
would retain that architecture. The most valuable next work is closing gaps
between those contracts and the actual production paths, before expanding the
feature set.

**There are new developer-remediable release blockers.** The existing green
tests do not establish thermal-stop enforcement, archive-content verification,
complete profile restoration, or safe HTTP resource consumption. These issues
must be fixed before beginning qualification of a new candidate. Earlier
statements that only external evidence remains are historical, not a conclusion
of this review.

## Scope and evidence

- Baseline: `main`, HEAD `6422cb7beeca6cb124bb5be6f53f2d675b3d549e`, version
  `0.9.0.dev0`, database schema 14, **including the existing working-tree edits**.
- The checkout started with 47 modified tracked files and 14 untracked files.
  It includes ongoing UX, acquisition, activation, connection, and Open WebUI
  changes. This is a review of that composite working tree, not qualification
  of HEAD alone or of a reproducible release artifact.
- Reviewed the GUI routes and lifecycle, setup and desktop launch, model and
  runtime flows, chat transport/storage, gateway and credentials, readiness,
  thermal/idle policy, backup/restore, repair/cleanup, application update,
  privacy/support, tests, CI/release workflows, and current plans/ADRs.
- This was a risk-based source and behavioral review across the app. It is not
  a claim that every line received an independent security audit.
- Local default run: `.venv/bin/python -m pytest -q` — exit 0; authoritative
  selected inventory **1,655**. Platform skips occurred. The double-quiet
  output does not provide a separate pass/skip summary; no historical count is
  substituted for that run.
- Local slow run: `.venv/bin/python -m pytest -q -m slow` — exit 0;
  authoritative selected inventory **52**. This includes the existing
  security/stress/clean-wheel gates.
- Compileall and `pip check` passed. Initial `git diff --check` was clean.
- Additional probes used synthetic prompts, fake host effects, temporary
  profiles, and a simulated directory exchange. No real model, service,
  credential, backup, system setting, or release was changed.
- No physical Bazzite/CachyOS GUI, inference, reboot, thermal, screen-reader,
  second-device, soak, or human-acceptance qualification was performed.

In the findings below, **reproduced** means an additional local probe exercised
the behavior; **source-confirmed** means the production call path was traced;
**qualification gap** means the property needs real-host or human evidence.
P0 means safety/data-integrity remediation first; P1 means required before a
release candidate; P2 means product quality or bounded follow-up work.

## Findings requiring fixes

### F01 · P0 · Thermal stop is recorded without proving the model stopped

**Reproduced.** In
[thermals.py:114](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/thermals.py:114),
the stop branch commits `stopped`, calls `stop_service`, and ignores whether the
returned service status is still active. Subsequent polls return `latched`
without observing or reconciling the service. A failed stop, or death between
the committed intent and the host effect, therefore prevents further stop
attempts while inference can remain active.

A probe returning `{"active": true}` from the stop adapter produced a first
result of `state=stopped`, followed by `state=latched`; the stop adapter was
called only once. Separately,
[ModelServerService.start](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/system_services.py:18)
reached its host start adapter with a stopped thermal latch. The CLI's direct
`llm start/restart/ensure` routes and GUI Restart do not share the activation
adapter's latch precondition.

**Fix:** distinguish stop intent from observed stopped state. Reconcile an
active or unknown service under a retained latch, with bounded retries and an
explicit failure state. Enforce the latch at every start/restart boundary,
including self-heal and durable takeover; only a safe explicit reset permits
inference again.

### F02 · P0 · Backup verification does not verify archived payloads

**Reproduced through the composed application and real operation engine.**
[backup_command.py:124](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/backup_command.py:124)
and [backup_adapter.py:292](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/backup_adapter.py:292)
validate the manifest's own digest, without comparing the archived file bytes
against the manifest's per-file sizes and hashes. The recorded confirmation
digest therefore does not currently establish that the restored database is
the database the user approved.

After creating a backup, the probe replaced only `state.db` with a valid SQLite
database containing an additional synthetic table. The original manifest was
unchanged. `verify_backup` returned `valid=true`; restore returned `RESTORED`
and `post_verify_state=passed`; the added table was present afterward.

**Fix:** one streaming archive verifier must validate the exact member set,
types, count, size, modes, names, and hashes, and bind the verified archive
identity to preview, execution, and recovery. A valid SQLite file is not proof
that it is the approved SQLite file.

### F03 · P0 · Restore replaces the entire application directory with a database-only tree

**Reproduced.**
[backup_adapter.py:134](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/backup_adapter.py:134)
stages only `state.db` and the manifest.
[publish_exchange](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/backup_adapter.py:405)
then swaps that tree with all of `AppPaths.app_dir`. The documented source
installation places `app-venv` under that directory; conversations, model
storage, credentials, update slots, and launcher receipts also live there.

Synthetic `app-venv`, conversation, and credential markers disappeared from
the active profile after a reported successful restore. They remained in the
retained prior tree; this is not proof of permanent deletion, but the live
installation and its references no longer have those files at their expected
paths. The current test considers disappearance of an arbitrary post-backup
marker sufficient evidence of a successful restoration.

**Fix:** define a complete preservation manifest before publication. Retain the
atomic exchange design, but stage a complete usable profile, with an explicit
policy for application slots, model bytes, conversations, and credentials.
Preserve secrets locally without adding them to a plaintext backup. Verify the
normal launcher and required file identities after restoration.

### F04 · P1 · Restore recovery and publication checks are weaker than ADR 006

**Source-confirmed.**
[probe_restore_published](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/backup_adapter.py:436)
accepts an active database plus an existing candidate directory—conditions
that also hold before exchange. The adapter has no composed model/gateway/GUI
quiescence controller, and post-restore verification only runs SQLite
`integrity_check`. `promote_or_rollback` implements promotion but no actual
reverse exchange. The pure `validate_restore` contract is not called by this
production adapter/command path.

**Fix:** bind probes to pre/post tree identities and durable exchange receipts;
hold an effective profile-wide exclusion across every writer and the exchange;
reopen/verify the actual runtime and model path; implement verified exchange
back or retain both trees as `RECOVERY_REQUIRED`. Add death tests against the
production adapter, not only fake workflow ports.

### F05 · P1 · Backup inclusion options claim work they do not perform

**Reproduced.** `create_backup(include_models=True, include_runtime=True)`
returned `CREATED`, but the archive still contained only the manifest and
`state.db`. The manifest nevertheless set `model_bytes_included=true`.
See [backup_command.py:61](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/backup_command.py:61).

**Fix:** immediately refuse unsupported options with a stable explanation, or
implement bounded inclusion with verified inventories. Derive claims from the
actual archived member set. Refusal is the smaller first fix.

### F06 · P1 · Gateway ingress and buffered responses are not bounded at the I/O boundary

**Source-confirmed, with a negative-length reader probe.**
[gateway.py:829](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/gateway.py:829)
reads the body before authorization. A negative `Content-Length` reaches
`read(-1)`. Accepted sockets have no explicit header/body deadline, and the
threaded server creates request threads before the per-client rate limiter.
The socket backlog of 16 is not a cap on active handler threads. Slow or
incomplete unauthenticated requests can retain resources. The buffered proxy
loads `response.content` before checking its size.

**Fix:** enforce global admission, request framing validation, finite ingress
and write deadlines, maximum header/body bytes, and incremental upstream reads
before allocation. Reject ambiguous duplicate lengths and unsupported transfer
encoding. Preserve loopback/managed-bridge binding and tailnet-only exposure.
Python documents the production limitations of this server; the specific
findings above follow from this adapter's code, not just that warning.
([Python HTTP server documentation](https://docs.python.org/3/library/http.server.html))

### F07 · P1 · Gateway concurrency resets while requests are still active

**Reproduced.**
[RateLimiter._row](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/gateway.py:386)
replaces the entire client record when its 60-second window expires, including
the in-flight count. Four admitted requests still running across the boundary
did not prevent four more requests from being admitted. Later releases can
also decrement the replacement record rather than their original reservation.
`charge_tokens` exists but has no production request-path caller.

**Fix:** separate request-window counters from lifetime reservations. Release
an exact reservation once, preserve active counts across windows, bound stale
client records, and either enforce the documented usage allowance on both
streaming and buffered paths or remove the unsupported claim.

### F08 · P1 · A model can pass readiness and then fail native chat's identity check

**Reproduced.**
[server.py:27](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/server.py:27)
accepts the selected installation's friendly `display_name` as its public
alias, matching the generated launcher. However,
[ChatSessionService.stream](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/chat_service.py:143)
uses the internal `current_model` identifier and compares the SSE model field
literally. With `local-123` / `Friendly model`, readiness accepted the identity
but chat returned `RESPONSE_MODEL_MISMATCH` before emitting text.

**Fix:** use one resolved identity object containing installation ID, public
API alias, artifact/runtime/config identity, and invocation. All chat,
activation, gateway, connection-card, and readiness checks must consume it.
Do not weaken identity checking to accept arbitrary friendly names.

### F09 · P1 · Chat validation and storage failures can leave the page stuck streaming

**Reproduced.** A 25,000-character prompt fits the GUI's 32 KiB composer limit
and survives its history trim, but exceeds the transport's prompt token cap.
Transport validation raises before the result-classification `try` block.
The [GUI task error handler](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/gui/app.py:104)
shows a notice without finalizing the page's streaming state. A probe confirmed
`_streaming` stays true after that chat worker error. A storage exception in
`_finish` can similarly happen before control state is reset.

**Fix:** validate with the same response reserve before consuming the draft;
return a closed validation result; finalize request state and controls for
every success/failure path. Keep unsaved content available if persistence fails.

### F10 · P1 · Empty or truncated SSE is reported as completed

**Reproduced.** An empty HTTP 200 stream returned `COMPLETED` with no response.
A single content frame followed by EOF, without `[DONE]`, also returned
`COMPLETED`. The same transport accumulates lines/chunks without an explicit
response-byte/frame cap; request `max_tokens` is not a local memory bound.
Cancellation is checked between received lines, so a silent upstream can delay
Stop until the 120-second read timeout. See
[chat_service.py:143](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/chat_service.py:143).

**Fix:** implement a bounded SSE state machine with explicit terminal evidence,
closed error frames, content-type checks, response/frame limits, a monotonic
total deadline, and cancellation that interrupts blocked reads. Preserve partial
text without calling a truncated response successful.

### F11 · P1 · Clicking Chat again can discard the active page during a response

**Reproduced with the real router and headless widgets.**
[shell.py:168](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/gui/shell.py:168)
asks `request_leave` only when the target route differs. Navigating Chat → Chat
still disposes the page and increments the generation. The old request's finish
callback is then suppressed. Its draft/partial-response leave protections are
not run through the normal stop-and-leave confirmation.

**Fix:** make same-route navigation idempotent unless a validated context change
requires it; keep active request state under a lifecycle owner independent of
widget replacement. Persist cancellation/completion even when presentation is
generation-fenced.

### F12 · P1 · Desktop launcher resolves away the Python virtual environment

**Reproduced for the documented symlink-based venv installation.**
[desktop_integration.py:98](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/desktop_integration.py:98)
calls `Path(sys.executable).resolve()`. In the review environment this replaced
`.venv/bin/python` with the base interpreter in Homebrew. The source-install
fallback launcher therefore bypasses the venv where the app was installed.
This also affects normal Linux venvs using interpreter symlinks; a future valid
`current/venv/bin/python` slot can mask the fallback issue.

**Fix:** retain the absolute venv entry path while validating its ownership and
existence. Test a real clean installed venv with a symlinked Python, outside the
repository and without `PYTHONPATH`, by executing the generated launcher.

### F13 · P1 · Normal CI collects all tests but executes an outdated subset

**Reproduced by expanding workflow file patterns.**
[ci.yml:53](/Users/cameronamer/Documents/BC250_LLM_MODE/.github/workflows/ci.yml:53)
explicitly executes patterns matching only **86 of 183 test files**. **97 files
are omitted**, including gateway, backup/restore, release authority, native
chat, readiness, profiles, notifications, and much of the unified GUI. The
collection command does not run those tests. Normal CI also has no explicit
slow battery. The manually dispatched release workflow does run the full
default and slow suites; that does not protect each ordinary PR.

**Fix:** derive shards from the collected node IDs, verify their union equals
the inventory with no overlaps, and retain machine-readable results. Make the
slow/security/package battery an explicit required check at the agreed cadence.

### F14 · P1 · Configured thermal and idle policies lack a production polling owner

**Source-confirmed.** `run_watchdog_once` / `watch_loop` have production callers
only in the explicit `thermals once/watch` CLI flow. Setting
`thermal_watchdog_enabled` does not start a supervisor. The composed
[IdlePolicyService](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/idle_policy.py:37)
has no production caller of `enforce_once`; a selected `STOP_AFTER` profile
therefore does not enforce its timer. No cross-client in-flight activity source
is wired to that idle service.

**Fix:** give enabled current-boot policy one explicit runtime owner, started
with the authorized inference session and observed separately from its settings.
Keep it independent of which GUI page is open. Before wiring idle effects,
account for in-flight requests, unknown activity, operations, and revision
changes. Keep the no-boot-autostart/no-tray/no-telemetry decisions.

### F15 · P1 · Old readiness can override a failed live observation

**Reproduced.**
[ChatObservationService](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/chat_service.py:336)
uses `(durable_ready or live_ready)`. A non-stale saved READY card combined with
a current `healthy=false` / mismatched live result still returned `ready=true`.
Also, [ChatPage.observation_failed](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/gui/chat_page.py:229)
changes notice text without invalidating the previous ready observation.

**Fix:** fresh negative observations take precedence. Carry observation identity,
timestamp, expiry, and failure state through Home/Chat/Connections; distinguish
unknown from ready, and do not renew old evidence by repainting it.

### F16 · P2 · Conversation limits hide data and do not bound discovery or writes

**Reproduced/source-confirmed.**
[ConversationService.list](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/conversation_service.py:265)
sorts every filename, selects the first 200 lexically, then searches/sorts by
recency. Creating `c000` through `c200` succeeded, but searching for `Chat 200`
returned nothing although the file could be loaded by ID. There is no create
limit despite the Privacy Center saying “up to 200 files.” Each list reads full
message files; `load` calls unbounded `read_bytes()` before checking size. Search
runs synchronously on each keystroke in Chat. Atomic replacement prevents torn
files but does not prevent GUI/terminal lost updates to the same conversation.

**Fix:** add a bounded metadata index and pagination, enforce an honest retention
policy without deleting user history, bound reads before allocation, move
discovery off Tk, and use revisions or a per-conversation write lock. Preserve
existing files during migration. Retry should retain the prior response until a
replacement succeeds; automatic crash recovery for drafts should be opt-in or
clearly described.

### F17 · P2 · Shutdown, instance activation, and some layouts need real event-loop work

**Source-confirmed, physical layout qualification pending.**
[shell.py:497](/Users/cameronamer/Documents/BC250_LLM_MODE/bc250_llm_mode/gui/shell.py:497)
performs host/service queries and privileged stop synchronously in the close
callback. It does not coordinate a concurrently finishing activation before
the final stop. The broker waits 0.5 seconds for ACK while the coordinator polls
at 5 seconds idle / 30 seconds unmapped. Its poll bound counts accepted requests,
not all attempted connections. This creates avoidable failure and stall paths.

The new scroll container and compact navigation are useful, but Profiles and
System still have dense fixed multi-column layouts. Wheel bindings apply to the
canvas rather than all descendants; keyboard focus does not automatically
scroll hidden form controls into view. `system` appearance is hardcoded to the
light palette. Passing token contrast checks or permissive widget stubs does not
prove actual Tk focus, layout, theme, or assistive-technology behavior.

**Fix:** asynchronous close with explicit operation coordination, bounded broker
event delivery, real Tk event/layout tests, scale-aware stacking/scrolling,
focus visibility, and truthful theme behavior. Measure the existing resource
budget on both target desktops before declaring accessibility complete.

### F18 · P1 · Release workflow identity and evidence handoff are incomplete

**Source-confirmed.** Each job independently checks out the supplied ref; a
branch may move between validation and build. Attestation verification passes
only `--repo` despite the comment claiming workflow identity verification.
`final-evaluation` runs the evaluator but uploads the original draft manifest
under the name `release-decision`; evaluator stdout is not persisted there.
Publish downloads the original candidate bundle. These are evidence and
reproducibility gaps, not proof of a release bypass: the evaluator remains
fail-closed and the upload step is deliberately absent.

**Fix:** resolve once to a commit, pin all subsequent jobs to it, carry explicit
builder/source identity and verified attestation output, and emit/consume the
actual decision artifact. The GitHub CLI supports signer workflow and source
digest/ref constraints.
([GitHub attestation verification documentation](https://cli.github.com/manual/gh_attestation_verify))

The release dependency environment also remains floating (`>=` requirements,
upgraded pip/build); the SBOM is based on declared dependency ranges. Add a
reviewed resolved release environment and inventory of the actual installed
dependencies. This is a reproducibility improvement, not a claim that a
particular current dependency is vulnerable. Publication and production trust
root activation remain owner-gated C8 work.

## Improvements and additions I would make

These build on the existing GUI/EUF work; they are not a request to reimplement
features that are already present.

| ID | Priority | Improvement/addition | Concrete outcome |
| --- | --- | --- | --- |
| A01 | P1 after F02–F05 | Native Backup & Restore page | Create, verify, inspect exact replacement/preservation, restore, and locate retained prior profile from the one window. The current System backup card is only a count. |
| A02 | P1 with F01/F14 | Runtime policy status | Show monitoring active/inactive/unavailable, last successful poll, latch/stop outcome, current idle policy, and the next safe action. Settings alone must not imply protection. |
| A03 | P2 | Connection verification journey | Retain the new Connection Doctor, guided cards, and named keys; show exactly which device/URL/model/key generation was verified and which step needs attention. Keep raw keys out of QR codes and persistent diagnostics. |
| A04 | P2 | Local active-request summary | Bounded content-free count, occupied slots, current request state, and cancellation guidance across native chat and gateway clients; support reliable idle suppression. No prompts or per-user surveillance. |
| A05 | P2 | Dependable conversation controls | Indexed history, visible context budget, recoverable drafts, preserved retry alternatives, clear partial-response state, follow-output toggle, and copyable code/text without rebuilding the entire transcript. |
| A06 | P2 | Finish model-choice guidance | Keep the new workload decision cards; tie recommendations to exact artifact/runtime/profile evidence, show disk reservations and optional integration RAM overhead, and distinguish measured speed from estimates. |
| A07 | P2 | Complete the small-screen/keyboard journey | Stacked forms, persistent focus, usable scroll/selection, clear unavailable actions, and a real system-theme choice or an honestly named fallback. |
| A08 | P2 | Maintainable contracts and documentation | Consolidate user-facing copy currently spread across `message_catalog`, `problem_details`, and `ux_guidance`; split large CLI/composition/page modules only along established service responsibilities; publish one current-state index. |

I would defer agents/tools/RAG, model conversion, public exposure, cloud sync,
automatic updates, a tray daemon, a framework rewrite, and more catalog breadth
until the dependable appliance journey is qualified. These are not needed to
fix the observed failures. Encryption remains a separate reviewed extension;
it must never silently downgrade to plaintext.

## Coverage by subsystem

| Area | Keep | Remaining work / confidence boundary |
| --- | --- | --- |
| Setup and launch | Five chapters, disclaimer, resume state, no automatic reboot | F12; real fresh/upgraded menu launch and first response |
| Models/acquisition/activation | Immutable identity, standard-layout/fit gates, lease heartbeats, quarantine | F01/F08; qualify current acquisition edits and installed-runtime inference |
| Runtime lifecycle | Staging, atomic exchange, receipts, durable recovery | Extend start-safety checks; retain existing crash tests; physical update/rollback pending |
| Operations/SQLite | One registry, per-command units, leases, typed repositories | F04; cross-writer publication barriers and production-adapter death evidence |
| Chat/history | Local storage, shared transport, new draft/export/retry controls | F08–F11/F15/F16 |
| Gateway/connections/Open WebUI | Named revocation, private topology, durable integration enable, diagnostic cards | F06/F07/F08/F15; pinned-container and second-device journeys |
| Thermal/profiles/idle | Fit preview, profile identity, calibration evidence, latch state | F01/F14; prove enforcement and real hardware behavior |
| Maintenance/repair/cleanup | Typed previews, quarantine/Undo, bounded inbox | Surface actual recovery states; confirm support/cleanup remain safe after restore repair |
| Backup/restore | SQLite backup API, no-replace archive publication, retained prior profile | F02–F05; mechanism needs substantial completion |
| Application updates | Explicit signed channel boundary, hostile bundle checks, two-slot design | Requalify backup dependencies; production trust root remains unavailable |
| Privacy/accessibility | No telemetry, explicit full export, separate secrets, local help | F16/F17; real labels/focus/theme; truthful retention and backup copy |
| CI/release/docs | Candidate-bound evaluator and owner gate | F13/F18; reconcile current docs and re-freeze one candidate |

## Why the green suite missed these issues

The tests are extensive, but several verify a contract or fake adapter rather
than the product promise. Backup tests inspect a manifest digest and database
integrity; they do not change the database payload under an unchanged manifest,
relaunch the restored installed app, or qualify retained model/credential paths.
GUI stubs accept almost any widget call, so they cannot reveal clipping or
event-loop stalls. Existing rate tests do not retain in-flight requests across a
window rollover. The review plan adds these specific adversarial and lifecycle
tests rather than more tests that repeat the implementation.

Archive extraction must also be explicit across supported Python versions:
the default tar extraction filter changed in Python 3.14. An explicit,
application-owned regular-file allowlist and preflight remain necessary even
with the safer default.
([Python tar extraction documentation](https://docs.python.org/3/library/tarfile.html#extraction-filters))

## Completion boundary

This review and its implementation plan are complete. The app has **not** been
fixed by this documentation work. Existing package edits were preserved. Release
status remains **blocked**, now with the developer fixes above preceding
candidate-bound C4 hardware/soak, C5 independent security, C6 human acceptance,
and C8 owner-authorized publication.
