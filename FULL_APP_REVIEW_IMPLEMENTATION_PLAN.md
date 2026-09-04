# BC250 LLM MODE — full application review implementation plan

**Status:** Corrective implementation is in the working tree and local
qualification passed. Hosted CI, candidate freeze, and physical/external RV-10
gates remain pending.
Created 2026-09-04. See [implementation status](docs/review-implementation-status.md)
for the delivered changes, operating details and qualification scope.

**Review:** [APP_REVIEW_2026_09_04.md](APP_REVIEW_2026_09_04.md).

**Baseline:** `6422cb7beeca6cb124bb5be6f53f2d675b3d549e` plus the working-tree
changes present during review; version `0.9.0.dev0`, schema 14. This composite
checkout is not a release candidate. The initial diff was inventoried and
preserved; a future candidate must bind the complete reconciled commit.

**Relationship to existing plans:** This is a corrective sequence over the
implemented GUI-1…8, EXP-1…8, and EUF-0…9 work. It does not replace their product
decisions or restart completed milestones. The findings reopen specific
developer gates. EUF-10 and candidate-bound C4/C5/C6/C8 follow the corrections.
Earlier green test totals remain historical evidence for their own snapshots.

## Intended outcome

A user can install the app in a dedicated venv, launch it from the desktop,
start a fitting model, receive a complete native response, optionally connect a
named client, recover from failures, and restore a backup without breaking the
installation or losing access to retained data. Safety and readiness labels
must describe observed behavior. The release evaluator must consume evidence
for the exact code and artifacts that actually passed qualification.

Preserve the native Tk architecture, one root, bounded worker lanes, one
refresh coordinator, SQLite authority, durable operation engine, standard GGUF
and fit gates, approximately 12/4 UMA policy, latched thermal protection,
desktop-next-boot behavior, private gateway topology, named revocation,
content-free diagnostics, and explicit update/publication decisions.

No tray daemon, telemetry, public Funnel/raw backend exposure, automatic
updates, on-the-fly UMA changes, generic repair button, or GUI framework rewrite
is part of this plan. New runtime policy supervision is tied to an explicitly
started current-boot inference session; it creates no login/boot autostart.

## Priority and delivery order

P0 findings affect safety or data integrity. P1 findings block the new release
candidate. P2 improvements may be deferred explicitly if they are not required
by an existing advertised journey; their limitations must remain visible.

| Phase | Work | Findings/additions | Depends on | Effort/risk |
| --- | --- | --- | --- | --- |
| RV-0 | Reconcile baseline and make CI execute its inventory | F13, A08 | None | Medium |
| RV-1 | Thermal enforcement and actual runtime policy ownership | F01, F14, A02, activity part of A04 | RV-0 | Large; safety-critical |
| RV-2 | Verify actual backup bytes and refuse false inclusion claims | F02, F05 | RV-0 | Medium |
| RV-3 | Complete profile restore, exclusion, recovery, and rollback | F03, F04 | RV-1, RV-2 | Largest; publication/data-critical |
| RV-4 | Bound gateway I/O and preserve request reservations | F06, F07, remainder of A04 | RV-0 | Large; security-sensitive |
| RV-5 | Repair desktop launch and unify identity/readiness | F08, F12, F15 | RV-1, RV-4 | Medium |
| RV-6 | Make chat finish correctly on every path | F09, F10, F11 | RV-5 | Large |
| RV-7 | Make conversation history bounded and recoverable | F16, A05 | RV-6 | Medium/large |
| RV-8 | Complete native recovery and real UI lifecycle/accessibility | F17, A01, A03, A06, A07 | RV-3, RV-5, RV-6, RV-7 | Large; includes real Tk QA |
| RV-9 | Bind release jobs, provenance, dependencies, and decisions | F18, remaining A08 | RV-0; finalize after package fixes | Large |
| RV-10 | Freeze and qualify the exact candidate | Existing EUF-10, EXP-8, C4/C5/C6/C8 | RV-1…9 required gates | Hardware/human/external |

Recommended execution is the table order. Resolve each phase's exit gate before
claiming it complete. The effort column is relative planning guidance, not a
delivery-date commitment; RV-3 and physical qualification dominate uncertainty.

## RV-0 — Establish a reproducible working baseline and trustworthy CI

**Deliverable:** a reconciled implementation branch and a CI inventory that
cannot silently omit newly added tests.

1. Inventory the current tracked diff and untracked files. Preserve all
   owner-controlled plans and scratch files. Review ongoing UX/integration
   edits as changes in their own right; never stage the entire directory or
   assume they are already accepted release inputs.
2. Record exact source identity, Python/dependency environment, selected test
   IDs, skipped IDs/reasons, and results. Keep review diagnostics distinct from
   release evidence. The current 1,655 default / 52 slow inventory is a
   checkpoint, not a hardcoded future target.
3. Replace the manual file-pattern list in `.github/workflows/ci.yml` with one
   authoritative pytest collection and deterministic node-ID sharding, or a
   full run if runner limits permit it. Prove union equality, disjointness,
   nonempty expected shards, and failure on a missing/killed shard.
4. Add explicit slow/security/clean-wheel checks. Keep permission scopes
   read-only in PR CI. Preserve the supported Python minimum/current matrix.
5. Add a small current-state index linking active plans and superseded
   checkpoints. Retain historical AGENTS/plan records but mark their authority
   accurately; do not overwrite owner-untracked planning documents.

**Primary files:** `.github/workflows/ci.yml`, test collection/report tooling,
`tests/conftest.py`, current-state documentation.

**Acceptance:** adding a new test file automatically schedules its tests;
deliberately deleting a shard result fails CI; missing tests and unexpected
skips are visible in machine-readable output. Run the reconciled default and
slow suites once after implementation and retain their authoritative results.

## RV-1 — Make thermal safety and idle policy operational

**Deliverable:** one enforceable safety boundary and an observed current-boot
policy owner, independent of the active GUI page.

1. Amend the thermal contract to separate a persistent stop latch/intent from
   observed process inactivity. Preserve the latch until an explicit safe
   reset. Store bounded stop-attempt/outcome evidence, not arbitrary exception
   text. A failed stop remains actionable and is retried with a bounded policy.
2. Place the authoritative start precondition at the composed model-service
   boundary and recheck it immediately before the host effect. Cover direct
   CLI start/restart/ensure, GUI Restart, activation, runtime recovery,
   calibration restoration, and every controlled restart. Stale caller state
   cannot clear or bypass a newer latch. Stops remain possible during recovery.
3. Prevent the service restart policy from re-enabling inference after a
   thermal failure. Prove that an active invocation under a latch is observed
   and stopped, including process death after intent and before effect.
4. Add a bounded policy supervisor tied to an explicit inference-session
   start. When thermal monitoring is enabled, verify its process/heartbeat and
   readable sensor before presenting monitoring as active. Keep disabled and
   unavailable states distinct. Poll fresh committed settings/latch state.
5. Integrate idle policy only after adding a content-free request-activity
   contract: active count, last request start/end, model/config generation,
   observation time, and unknown/stale status. Include native/terminal clients
   and gateway clients; account for any permitted direct local clients.
6. `STOP_AFTER` must not stop an in-flight request or operation. Unknown
   activity suppresses idle stop. Evaluate the applied profile revision, not a
   newly edited profile's un-applied settings. Clarify desktop-session versus
   GUI-close semantics before wiring `STOP_ON_DESKTOP`; merely being on the
   desktop must not immediately stop interactive chat.
7. Surface Monitoring active/disabled/unavailable, last poll, thermal stop
   pending/confirmed/failed, idle policy, and its next safe action on System
   and Home. Keep notifications fixed and privacy-safe.

**Primary files:** `thermals.py`, `system_services.py`, `server.py`,
`activation_adapter.py`, `app.py`, `idle_policy.py`, `services.py`, relevant
repositories/migration, profile/GUI projections, CLI.

**Acceptance:** tests cover stop returning active, stop raising, death before
and after stop, restart under latch, stale settings, missing sensor, supervisor
death, idle during SSE, unknown activity, and a concurrently edited profile.
All affected start surfaces refuse before invoking systemd while latched. A
normal reboot still has desktop as target and inference/supervision inactive.
Actual temperatures and restart behavior require RV-10 physical evidence.

## RV-2 — Verify backups before trusting them

**Deliverable:** one production archive inspector used by create, verify,
restore preview, restore execution, and takeover.

1. Reject `include_models` / `include_runtime` with stable unavailable codes
   until each is implemented. Never mark bytes included based on a request
   flag. Keep encryption's existing explicit refusal.
2. Inspect an archive through a held regular-file descriptor; bind its digest
   and filesystem identity to the receipt/preview. Stream hashes with size,
   member-count, total-byte, time, and cancellation bounds. Apply limits before
   loading large JSON or database payloads.
3. Require an exact allowlisted member set matching the manifest. Refuse
   duplicates, undeclared members, missing members, symlinks/hardlinks, devices,
   FIFOs, sparse abuse, traversal, ambiguous names, oversized metadata, and
   digest/size mismatches. Do not rely on Python-version-specific tar defaults.
4. Connect the existing pure restore validator to inspected facts, including
   schema compatibility, actual space reservations, permissions, topology, and
   identity. A status command may use a recorded verification with explicit
   freshness; an explicit Verify/Restore must inspect current bytes.
5. Extract only validated regular files into a private owned staging tree.
   Revalidate staged identities before publication and on recovery. Release
   reservations through the existing durable cleanup semantics.
6. A legacy `verified` backup row is historical until its archive passes the
   new inspector. Preserve the archive; mark corrupt or unavailable evidence
   without deleting it. Add schema fields only through the next ordered
   migration determined after RV-0.

**Primary files:** `backup_adapter.py`, `backup_command.py`, `backup_manifest.py`,
`backup_restore.py`, `backup_lifecycle.py`, `operations/backup.py`, storage
capacity and repository contracts, ADR 006.

**Acceptance:** changing `state.db` under an unchanged manifest fails Verify,
Preview, and Restore before publication—even when SQLite integrity passes.
Missing/extra/duplicate/link members, low disk, interrupted staging, and
archive replacement after preview are refused. Unsupported inclusion requests
produce no archive and no success receipt. Tests exercise the composed adapter.

## RV-3 — Restore a complete usable profile and recover every publication boundary

**Deliverable:** an amended ADR 006 and a complete production restore path.
This is the largest prerequisite for trusting backups or dependent update work.

1. Freeze a preservation/replacement matrix before coding. For each of
   application venv/slots/pointers, database, model artifacts, runtime/handoff,
   conversations/drafts, logs, backup inventory, credentials, broker ownership,
   and migration receipts, specify the source and post-restore owner.
2. Keep atomic whole-profile exchange unless a reviewed ADR deliberately
   changes it. Under the retained design, stage a complete profile: restore
   verified archived data and preserve required current app/data assets
   locally. Do not copy secrets into plaintext backup artifacts. Refuse when
   safe preservation cannot be established.
3. Preserve monotonic safety facts across historical restoration. Restoring an
   older database must not clear a current thermal latch, revive a revoked key,
   rewind a credential generation, or falsely reactivate an old operation.
   Reconcile current secret files with restored non-secret metadata explicitly.
4. Establish an effective exclusion/ownership mechanism outside the database
   being exchanged. Every profile writer must respect it: GUI, worker, CLI,
   conversation writer, gateway usage updater, notification producer, and
   updater. Quiesce through composed services; no shell or systemd calls in
   widgets. Keep the exclusion through post-verification and rollback.
5. Record exact prior/candidate identities and exchange intent before the
   syscall in stable recovery evidence. Probes must distinguish staged,
   exchanged, verified, rolled back, and ambiguous states using identity, not
   file existence. Never perform a blind second exchange on takeover.
6. Carry required operation/lease/event lineage across publication without
   cursor collisions or competing copies of authority. Reopen all database
   connections against the published identity; stale connections cannot keep
   writing the retained prior tree.
7. Verify schema and foreign keys, application launcher/import, model/runtime
   references, credential reconciliation, handoff regeneration, and the
   authorized bounded inference journey. Preserve the intended stopped/running
   state; do not invent a model start merely to satisfy an unrelated restore.
8. On failed verification, perform a proven reverse exchange and verify the
   prior profile. If proof is unavailable, retain both trees, keep exclusions,
   and expose a precise `RECOVERY_REQUIRED` action. Only verified rollback can
   produce a rolled-back result.
9. Requalify signed-application update backup/restore dependencies and cleanup
   exclusions. Keep the app executable even when the restore replaces the
   directory from which it was launched. Make retained-prior discovery usable
   after a restart and do not automatically purge it.

**Primary files:** `backup_adapter.py`, `profile_exchange_helper.py`,
`backup_command.py`, `operations/backup.py`, operation leases/recovery,
`app.py`, `paths.py`, application-update restore integration, ADR 006.

**Acceptance:** a clean installed wheel/venv can create, restore, close, and
relaunch with the intended model and retained conversations. Prior/current
keys follow the approved revocation policy. Inject death at every exchange and
receipt boundary, use simultaneous writers, and force post-check failures.
Prove one terminal outcome, no unverified success, bounded recovery, and
retention of all uncertain trees. Run real atomic exchange on Linux. A fake
rename sequence remains a unit fixture, never physical qualification evidence.

## RV-4 — Bound the gateway from accepted socket to upstream completion

**Deliverable:** consistent resource and authorization enforcement for all HTTP
paths, including unauthenticated, slow, malformed, and disconnected clients.

1. Keep the existing policy/authentication core and secret custody. Add global
   admission before request-thread creation and bound connection backlog,
   active handlers, per-client requests, and upstream work independently.
2. Define a strict HTTP framing contract: finite header/body/read/write/total
   deadlines; nonnegative bounded `Content-Length`; duplicate-header handling;
   explicit rejection of unsupported chunked/ambiguous framing and invalid
   `Expect` behavior. Reject header-only unauthorized requests before waiting
   for a large body when possible.
3. Stream buffered-response reads into a bounded collector. Bound decompressed
   bytes as well as wire bytes. Keep incremental SSE flushing and stop upstream
   work on downstream disconnect or timeout.
4. Replace rollover-dependent in-flight counters with exact reservations.
   Time-window rate/usage counters may roll over; active reservations may not.
   Release once in `finally`, including auth/backend failure and disconnect.
5. Apply any retained token/byte allowance on both response modes and define
   strict accepted types/defaults for generation limits. A declared cap must
   have an executing enforcement path. Prune inactive limiter metadata safely.
6. Publish only bounded content-free activity needed by RV-1 and the UI. Do not
   store prompts, completions, headers, or bearer values. Recheck credential
   revocation on subsequent requests and define the in-flight revocation policy.

**Primary files:** `gateway.py`, `gateway_runtime.py`, `gateway_service.py`,
`connection_setup.py`, client compatibility and problem contracts.

**Acceptance:** real socket tests cover partial headers/body, negative and
duplicate lengths, unsupported encoding, slow upload/download, huge backend
body, silent SSE, disconnect, limiter rollover, and shutdown. Assert hard
thread/connection/memory limits and return to baseline. Existing named-scope,
auth-negative, SSE, and independent-revocation journeys remain green. Bound the
tests themselves and use only synthetic local traffic.

## RV-5 — Make launch, identity, and readiness agree

**Deliverable:** the installed app launches in the correct environment, and
“ready” means the same selected runtime is usable by the requested journey.

1. Preserve the absolute venv Python entry when rendering desktop launchers;
   resolve paths for containment/ownership checks without substituting the
   interpreter used for execution. Preserve valid signed-slot precedence.
2. Introduce or reuse one immutable resolved model identity with internal
   installation ID, public API alias, artifact/runtime/config identity, and
   live invocation. Avoid another mutable model registry.
3. Use the public alias in protocol requests and the bound identity in
   verification. Share the mapping across native/terminal chat, server probes,
   gateway backend observation, Connections, activation, and calibration.
4. Make a fresh negative observation dominate older positive evidence. Unknown
   observations remain unknown. No timestamp-less observation becomes fresh
   just because a projection is rebuilt. A failed refresh expires the previous
   ready state according to the documented bound.
5. Check staleness and invocation/config changes before send/start/handoff, not
   only at render time. Keep explicit SSE verification separate from lightweight
   refresh. Keep Open WebUI and remote readiness optional for native chat.

**Primary files:** `desktop_integration.py`, `server.py`, `runtime_handoff.py`,
`chat_service.py`, `appliance_readiness.py`, `connection_setup.py`, gateway
identity observation, GUI state projections.

**Acceptance:** execute the generated launcher from a clean installed venv
outside the checkout. Test internal ID ≠ display alias, duplicate display
labels, model switch/restart, expired verification, failed live observation
after READY, and credential rotation. No mismatched model is accepted and no
correct friendly alias is rejected. Native and external cards remain consistent.

## RV-6 — Finish the chat lifecycle and transport

**Deliverable:** every request reaches a truthful terminal state and leaves the
UI usable, with its draft/partial response recoverable.

1. Use the same prompt-budget calculation and generation reserve in GUI,
   terminal, and transport. Validate before clearing the composer or dropping a
   previous response. Bound UTF-8 bytes as well as token estimates; present the
   reason and a safe edit action when the prompt cannot fit.
2. Move request ownership out of disposable page state. A request ID/generation
   identifies one stream and one cancellation path. Same-route navigation is
   idempotent; different-route navigation follows the existing stop/leave
   confirmation, and late presentation callbacks cannot lose persisted results.
3. Implement bounded SSE decoding before line accumulation: limits on line,
   frame, total bytes and malformed frames; supported content/reasoning/usage
   shapes; explicit terminal marker; error frames and unexpected EOF. Reasoning
   metadata must not be mistaken for a visible answer or an error.
4. Make cancellation interrupt a silent upstream read. Enforce one monotonic
   total deadline across connect, retries, first token, streaming, and finish.
   Retry only pre-output transient failures as already contracted.
5. Finalize busy/streaming/button state in all paths, including preflight,
   transport exceptions, page disposal, rejected lane submission, and failed
   conversation writes. A persistence failure keeps text in memory with an
   explicit Save/Export recovery action; it cannot become a fake successful save.
6. Preserve partial text with its result classification. Retry/regenerate
   retains the previous answer until a replacement is accepted and saved.
   Mark approximate token estimates distinctly from server-reported usage.

**Primary files:** `chat_service.py`, `chat_lifecycle.py`, `chat.py`,
`gui/chat_page.py`, `gui/tasks.py`, `gui/app.py`, `gui/shell.py`.

**Acceptance:** empty HTTP 200, EOF before DONE, error frames, oversized line,
invalid prompt, wrong model, callback/storage exception, same-route click,
window close, and cancellation during silence all finish predictably. No stuck
Send/Stop controls, silent data loss, duplicate retry after output, or unbounded
buffer. Set and measure a Stop response target of ≤2 seconds under a silent
local fixture without adding an unbounded polling loop.

## RV-7 — Make conversation storage and rendering match the resource promise

**Deliverable:** fast bounded history browsing and safe multi-surface edits.

1. Add a private bounded metadata index with revision, title, archive status,
   update time, model identity, message count, and file identity. Keep message
   content in conversation files and out of diagnostic databases/exports.
   Rebuild the index through cancellable incremental discovery when needed.
2. Use indexed search/pagination and read only the selected conversation.
   Bound file reads before allocation, reject special files, and preserve
   invalid files with a visible recovery state. Do not silently hide everything
   after the first 200 lexical filenames.
3. Choose an honest storage policy: retain arbitrary existing history with
   bounded pagination and explicit quota, or refuse creation at a documented
   cap with archive/export guidance. Never automatically delete older chats to
   enforce the cap. Update Privacy Center copy to the actual behavior.
4. Protect saves/renames/archive/drafts with per-conversation revision checks or
   an equivalent lock across GUI and terminal clients. Atomic replace remains
   required but is insufficient by itself. Handle conflicts without overwriting
   another client's messages.
5. Add bounded draft recovery using the existing lifecycle/coordinator; clear
   storage semantics and an explicit privacy choice must accompany any change
   from leave-only saving. Migrate legacy formats without content loss.
6. Incrementally append the active response instead of re-rendering up to
   4 MiB of transcript each refresh. Preserve user scroll/selection, provide a
   follow-output control, and keep the full history export path explicit.

**Primary files:** `conversation_service.py`, `gui/chat_page.py`,
`conversation_ux.py`, `privacy_center.py`, optional conversation-index migration
outside operational/diagnostic content stores.

**Acceptance:** 201+ conversations remain discoverable; search does not read
every body; oversized files stop at the byte bound; concurrent GUI/terminal
writes do not lose messages; legacy files migrate safely; failed save/export
preserves recovery options. Measure discovery, keypress responsiveness, and
stream rendering against the existing GUI resource budget.

## RV-8 — Complete the native appliance flows and real UI behavior

**Deliverable:** a usable native recovery journey, responsive lifecycle, and
physical-test-ready screens.

1. Add a Backup & Restore page only after RV-2/RV-3 pass. Reuse the typed
   command service and in-window drawer. Present what is included, what is
   preserved, exact preview identity, required space, replacement effects,
   retained-prior location, and recovery/rollback status. No bare JSON is the
   primary user flow.
2. Move close-time host probes/stop into the bounded action lifecycle. While
   closing, prevent competing starts, coordinate active durable work, wait for
   the defined safe boundary, then verify stop before destroying the window.
   Failed/unknown stop keeps the app open with a concrete action. Do not mark a
   durable operation cancelled just because the window closes.
3. Fix broker ACK latency and fairness within one-root/no-listener-thread
   constraints. Use a supported Tk file-readiness event on Linux or a reviewed
   bounded coordinator integration. Count all connection attempts, bound work
   per dispatch, and parse partial messages safely. Reopening an idle/minimized
   app must activate it without spurious failure or a second owner.
4. Finish Connections around the already implemented Doctor/cards: one next
   action, explicit verification target, copy-safe URL/model, one-time key
   reveal, and independent revocation. Qualify actual PocketPal/Open WebUI
   versions; do not claim readiness from a generic protocol fixture.
5. Finish model decisions using existing workload cards and fit authority.
   Show current profile, disk reservation, model/load progress, and measured
   versus estimated performance. Account for optional web/gateway host RAM in
   preflight/qualification; do not fabricate throughput estimates.
6. Make Profiles/System and long forms stack or scroll at minimum window size
   and 100–200% scale. Wheel events over child widgets and Tab traversal must
   reveal their targets. Preserve focus after refresh and show enabled/disabled
   reasons. Implement actual system-theme observation or rename the fallback.
7. Add real Tk event/layout tests on Linux in addition to stubs. Verify every
   primary/secondary route, drawer, failure notice, keyboard shortcut, and
   stream state. Keep screen-reader qualification as a human/physical gate.
8. Consolidate common user-facing message ownership; retain stable error codes
   and technical details separately. Split large page/CLI/composition modules
   only as touched by these fixes, with no new service owners or blanket rewrite.

**Primary files:** `gui/shell.py`, `gui/refresh.py`, `instance_broker.py`,
`gui/widgets.py`, Profiles/System/Chat/Connections/Models/Help pages,
`gui/theme.py`, copy catalogs, documentation, real Tk test harness.

**Acceptance:** full first-run → first-response, backup → restore → relaunch,
connect → verify → revoke, and error → repair journeys run in one window.
Navigation/typing remains responsive during slow host probes. Minimum-size and
200% scale screenshots show reachable controls and visible focus. Record RSS,
idle CPU, active CPU, and thread counts using the existing GUI/EXP worksheets;
do not relax budgets merely to make the tests pass.

## RV-9 — Complete release identity and reproducible artifact/evidence flow

**Deliverable:** a nonpublishing pipeline rehearsal that consumes one immutable
candidate and persists the real verified decision.

1. Resolve `candidate_ref` once in validation and export a full commit SHA.
   Pin build, tooling, verification, evaluation, and publication preparation to
   that SHA. Validate ref/version inputs and pass them as environment/structured
   data, not interpolated shell/Python source. Retain the human-readable ref as
   bound metadata, with explicit behavior if it moves.
2. Use a reviewed release dependency resolution with hashes for direct and
   transitive packages and build tools. Keep library dependency ranges where
   appropriate, but record the exact build/install environment and container
   inputs. Generate an SBOM for actual resolved dependencies and bind it to the
   wheel/source subjects. Do not add a dependency upgrade merely as cleanup.
3. Verify provenance against the approved repository, signer workflow, source
   digest/ref, and subject digests. Persist verification output as bounded
   evidence; passing `--repo` alone is insufficient for the stronger declared
   workflow/source claim. Use a reviewed trusted-builder model if required to
   attest a source ref different from the dispatch workflow ref.
4. Save the evaluator's actual decision JSON and digest. Define how a final
   eligible manifest is emitted, signed/attested, and added without creating a
   circular self-inventory. Reuse the existing manifest/inventory authorities;
   do not invent a second eligibility predicate.
5. Make downstream jobs consume that exact decision/evidence bundle and
   reverify all bindings. Fail if the bundle still contains only the original
   blocked draft. Approvals cannot override evaluation.
6. Run a local/offline fixture rehearsal with eligible test-only evidence and
   negative twins: moved ref, wrong signer/source, changed wheel, changed SBOM,
   mismatched decision, missing gate, and reused historical evidence. Test
   fixtures never become real release PASS records.
7. Reconcile README, ARCHITECTURE, help, operator/runbook, current-state index,
   and release workflow descriptions. Preserve pending physical/security/human
   labels. Keep upload and production trust activation unavailable until C8.

**Primary files:** `.github/workflows/release.yml`, `tools/release/*`, release
manifest/evidence/artifact modules, dependency/build metadata, release docs and
workflow tests.

**Acceptance:** every job reports the same source SHA and artifact inventory;
the emitted decision really is the decision evaluated; wrong-source/signer
evidence fails before approval; no rebuild happens after evidence/signing; the
negative pipeline remains blocked. The rehearsal performs no publication.

## RV-10 — Freeze, qualify, and hand off one candidate

**Deliverable:** real candidate-bound evidence or an accurate blocked decision.

1. Finish all P0/P1 corrections and any already-advertised mandatory journey.
   Record explicit deferrals for optional P2 additions. Review the integrated
   diff, freeze the exact commit, and build one clean source → sdist → wheel
   artifact set outside any stale checkout build tree.
2. Run the reconciled default suite, complete slow/security/clean-wheel gates,
   real Tk checks, compile checks, and installed CLI/worker/runtime/launcher
   journeys. Retain authoritative IDs/results and dependency/host identity.
3. Execute the existing four-cell Bazzite/CachyOS fresh/upgraded worksheet and
   all 14 appliance journeys, adding the newly discovered failure cases rather
   than duplicating qualification documents. Include small and 9B standard
   models, both advertised clients where applicable, reboot/no-autostart,
   actual thermal stop and failed-stop recovery, profile idle behavior,
   backup/restore/relaunch, runtime/application rollback, and resource bounds.
4. Collect the required hardware soak and independent C5 security review.
   Give the reviewer explicit emphasis on gateway pre-auth resource controls,
   privilege/start paths, archive restore, credentials across restoration,
   publication recovery, and release provenance.
5. Run C6 acceptance with the participant roles required by EXP-8. Record task
   completion, intervention, misunderstood states, focus/scale/accessibility,
   and whether a person can recover without SSH or filesystem surgery. Store
   no participant chat content in diagnostic evidence.
6. Feed only verified evidence for this candidate and artifact inventory to the
   existing evaluator. Missing or failed evidence remains blocked. Any package
   fix creates a new candidate and invalidates affected evidence; recollect it.
7. Prepare the concrete signed/reviewable release bundle and final decision.
   Actual publication, production trust root/channel activation, and final tag
   follow existing explicit owner authorization at C8. This plan grants none.

**Existing handoffs:** `release/EVIDENCE_HANDOFF.md`, `release/RUNBOOK.md`,
`docs/appliance-experience-physical-qualification.md`, GUI, connections, profiles,
notification, repair, and application-update qualification worksheets.

**Acceptance:** the final decision identifies one exact candidate, inventory,
policy, and verified evidence set. A public completion claim occurs only after
the real external gates and authorized publication are complete.

## Regression and migration rules across phases

- Add tests for each reproduced failure before changing its implementation.
  Exercise production composition and adapters where the defect crossed those
  boundaries. Keep pure contract tests, but do not substitute them for the
  launcher/socket/restore behavior they are meant to protect.
- Run focused tests per change, then default + required slow/package gates at
  phase/integration boundaries. Repeat broad tests only after relevant changes
  or unresolved failures. Do not hardcode current counts as success criteria.
- Introduce schema migrations only when the design requires new durable facts;
  allocate versions from the reconciled branch, preserve legacy input, and
  cover interrupted migration, downgrade refusal, and recovery. Never silently
  rewrite old evidence as newly verified.
- Keep all production mutations in existing typed services/operations. GUI
  worker errors carry closed results, not secret-bearing tracebacks. No prompt,
  completion, draft, token, or bearer value enters logs/events/support/metrics.
- Preserve all uncertain backups, candidates, prior trees, and user history.
  Refuse safely before publication if a required preservation/identity check is
  unavailable. A safety refusal must identify the safe next step.
- Implemented, developer-qualified, physically qualified, release-eligible,
  and published remain separate claims. Update status documentation only to
  the level supported by newly collected evidence.

## Initial commit-sized work queue

1. **RV-0.1:** reconcile the current diff and publish collected-test inventory
   coverage; preserve all owner files.
2. **RV-0.2:** replace stale CI file lists and enforce executed-inventory parity.
3. **RV-1.1:** regression fixtures for failed thermal stop, latch-bypassing
   start/restart, and death-before-stop; freeze the new reconciliation contract.
4. **RV-1.2:** enforce the authoritative latch/start/stop boundary.
5. **RV-2.1:** refuse unsupported backup inclusion and add the unchanged-manifest,
   altered-database regression through the real engine.
6. **RV-2.2:** implement the shared bounded archive inspector and wire all callers.
7. **RV-3.1:** amend ADR 006 with preservation, exclusion, monotonic safety, and
   exchange-identity decisions; add failing complete-profile relaunch tests.

Later commits follow the phase acceptance gates. This queue makes the first
actions concrete while keeping the risky restore redesign reviewable before
changing its publication mechanism.
