# EXP-8 candidate-bound appliance experience qualification

This is the authoritative journey and resource worksheet for the appliance
experience. It is an execution protocol, not a result. **Status: PENDING — no
EXP-8 physical journey, resource, security, or human-acceptance PASS record is
present in this repository.** Automated tests can qualify structural contracts;
they cannot qualify an AMD BC-250, a Linux desktop, a phone client, or a person.

Run this protocol against the exact built candidate after every package-code
change. Evidence from `ccd1777`, a development checkout, another commit, another
artifact inventory, or another host cell is invalid. Never edit a failed run
into a pass; retain it locally, open a tracked issue with privacy-safe facts, and
run a new identified attempt after the fix.

## Candidate and run identity

Before starting, record these fields in the controlled local evidence folder:

- candidate version, full 40-character source commit, protected source ref,
  repository, policy digest, artifact-inventory digest, wheel filename and
  SHA-256, release-set digest, and installed-wheel digest;
- host cell (`bazzite-fresh`, `bazzite-upgraded`, `cachyos-fresh`, or
  `cachyos-upgraded`), distribution/version, kernel, Mesa/RADV, Python/Tk,
  desktop, firmware, 40-CU observation, and 12 GiB GPU / 4 GiB host UMA split;
- pseudonymous run ID, participant role, independent operator/reviewer role,
  UTC start/end, procedure revision, and contained attachment locations;
- starting profile/schema and, for an upgraded cell, the exact prior version,
  source commit, schema, and supported migration path.

Do not record names, credentials, API keys, authorization headers, hostnames,
tailnet names, addresses, usernames, home paths, prompts, completions, raw
remote bodies, raw exception text, release-note content, or update nonces.
Screenshots and logs must be scrubbed before they become attachments.

## Required execution matrix

Run J01–J14 independently in all four host/profile cells. A run may share a
prepared candidate artifact, but it may not reuse an outcome, screenshot, or
resource measurement from another cell.

| Cell | Host | Starting profile | Status |
| --- | --- | --- | --- |
| B-F | Bazzite | fresh user profile | PENDING |
| B-U | Bazzite | supported upgraded profile | PENDING |
| C-F | CachyOS | fresh user profile | PENDING |
| C-U | CachyOS | supported upgraded profile | PENDING |

For every journey and cell record: `outcome`, stable result/error code,
completion state, elapsed seconds, time-to-first-safe-action, wrong-turn count,
whether a terminal was required, unclear labels, recovery actions, resource
sample IDs, safety defects, privacy defects, accessibility defects, and the
tracked issue IDs for every defect. Use `PASS`, `FAIL`, `BLOCKED`, or `PENDING`
only after the run; `BLOCKED` is not a pass or waiver. Journey notes remain
local and are not telemetry.

## Scripted journeys

### J01 — Install and application-menu launch

Install the exact clean wheel as the regular desktop user, install the owned
desktop entry from its preview, and launch from the application menu. Launch a
second time and confirm it activates the same window: one Tk root, one refresh
owner, and one bounded worker set. Confirm there is no login autostart and no
model, gateway, Open WebUI, Tailscale, or update check starts implicitly.

### J02 — Safety and five-chapter setup

From an unacknowledged profile, prove the exact heat, 40-CU, and 12/4 UMA
warning blocks every mutation until three checkboxes and `I ACCEPT` are
present. Complete This machine, System mode, Runtime, Model, and Ready. Relaunch
during one resumable step and reboot only where instructed. Confirm current-
boot LLM Mode never changes the graphical/no-model next-boot policy.

### J03 — Import an existing model

Select a pre-existing standard-layout GGUF from an approved managed or
configured external directory. Verify provenance, quantization, tensor layout,
fit, and alias presentation. Confirm fused/MAX/imatrix-MAX, symlink escape,
duplicate identity, low disk, and low-VRAM cases refuse safely. The import must
stream or mmap; it must not copy the whole artifact into host RAM.

### J04 — Apply a profile and start the model

Preview and apply a named workload profile against the imported model. Confirm
Context per user, Concurrent user slots, KV cache, overhead, 12 GiB fit,
thermal readiness, evidence class, and exact rollback identity. Start only
through the one systemd owner and require service, `/health`, `/v1/models`, and
bounded inference verification before showing Active.

### J05 — Native chat and stop

Send and stop a streaming response in native Chat, then send another turn.
Confirm Stop ends only the request, the UI remains responsive, focus is not
stolen, transcript rendering stays bounded, and saving is explicit. Stop the
model from System and confirm Chat reports an actionable unavailable state
without spawning a competing server.

### J06 — PocketPal setup and stream

With Funnel off and port 8080 still loopback-only, create one named phone
client and use the exact displayed `https://<node>.<tailnet>.ts.net:10000/v1`
base URL, one-time key, and public model alias in PocketPal. Verify models and
streaming chat, then run missing/wrong/revoked credential negative probes.
Record classifications and timings only; follow
`connection-physical-qualification.md` for the complete matrix.

### J07 — Second client and independent revocation

Add a second generic OpenAI/SSE client. Prove both clients work concurrently,
revoke the first, and prove the first fails while the second continues. Then
exercise emergency Disable all while llama.cpp is unhealthy. No client label,
key, authorization header, request body, prompt, completion, address, or raw
response may enter durable evidence.

### J08 — Interrupted-operation recovery

Interrupt an effecting durable operation after intent and after external
effect, separately. After lease expiry, recover through Activity and prove
probe-before-repeat, revision/generation fencing, one external effect, honest
terminal evidence, and stale-worker refusal. An ambiguous result must remain
`RECOVERY_REQUIRED` with its resource barrier until explicit recovery.

### J09 — Low-storage cleanup, quarantine, and Undo

Seed eligible terminal-operation staging plus every protected class. Confirm
the cleanup dry run excludes active, known-good, external, backup, credential,
conversation, log, profile, runtime, application-slot, recovery, symlink,
device, socket, and mount-crossing data. Quarantine an eligible target, perform
the exact evidence-bound Undo, then verify expired purge is separate,
permanent, and refuses stale or partial evidence.

### J10 — Backup and restore

Create and verify a profile backup, inspect its digest-bound restore plan, and
restore through the live Linux atomic-exchange path. Verify schema integrity,
profile state, retained prior profile, server health, and post-restore
inference. Exercise an interrupted exchange/recovery case and follow the
separate `BACKUP_RESTORE_HARDWARE` evidence contract.

### J11 — Signed application update and rollback, if permitted

If and only if an evaluator-eligible signed release and reviewed production
trust root exist, complete online check, offline import, preview, update,
replacement-process acknowledgment, migration, restart, rollback, profile
restore, and retention from `application-update-physical-qualification.md`.
Otherwise record the stable unavailable/refused outcome; never substitute a
source checkout, arbitrary wheel, fixture signature, mutable branch, or bypass.

### J12 — Desktop mode and reboot with no model

Use Desktop mode, inspect the effective boot target, and reboot. Confirm the
normal graphical desktop appears and no model, gateway, Open WebUI, sharing, or
Tailscale component was auto-started by the application. Relaunch from the menu
and prove an explicit model start still works.

### J13 — Redacted support bundle after a seeded failure

Seed a stable-code failure, show the corresponding Maintenance/Activity/Repair
path, and create a local support bundle. Self-verify its manifest and scan it
for all privacy canaries. Confirm it is bounded, contains no raw exception or
secret content, and is never uploaded automatically.

### J14 — Uninstall preserving models, reinstall, and rediscover

Record model identities, uninstall without `--remove-models`, remove only the
owned desktop entry, and verify app-owned service/host integration is reverted
while managed/external models and the Open WebUI volume survive. Reinstall the
exact candidate, rerun setup, rediscover models without duplicate bytes, and
activate one through the normal verified path.

## Participant coverage

The complete matrix must include these five observed passes; one participant
may cover multiple roles only when independence requirements still hold:

1. a Linux user unfamiliar with llama.cpp completing first-run tasks from the
   end-user guide;
2. an existing BC-250 owner exercising daily model/profile/chat work;
3. a recovery operator handling interruption, repair, backup, and rollback;
4. a keyboard-only pass at 100%, 125%, 150%, 175%, and 200% scale, with the
   available Linux screen reader and documented Tk limitations;
5. a mobile-client pass using PocketPal plus a second OpenAI/SSE client.

The `HUMAN_ACCEPTANCE` issuer must be a non-developer. Participant feedback
becomes tracked issues and may not waive thermal, fit, credential, recovery,
desktop-next-boot, or release-verification requirements.

## Resource measurement protocol

Measure on both hosts in fresh and upgraded cells using one declared toolset
and sampling interval. Store raw samples only in contained, scrubbed local
attachments. Do not add a telemetry, crash-upload, tray, or watcher path.

1. Start from no application process. Capture launch-to-first-meaningful-paint
   with a monotonic clock; target **≤1.5 seconds** after tkinter is available.
2. After two idle refresh cycles, sample GUI RSS and CPU for 60 seconds. Target
   **≤90 MiB idle RSS** and **<1% settled CPU**.
3. Count GUI-owned background threads by their `bc250-gui-` names. Target
   **≤3** (action, observation, chat); record total process threads separately.
4. Record open file-descriptor and socket counts at settled idle, after 100
   route changes, after chat start/stop, after notices enabled/tested/disabled,
   and after another settled interval. Counts must return to a stable bound;
   unexplained monotonic growth fails.
5. Route through every primary page 100 times. Target **≤5 MiB retained RSS
   growth**. Confirm one refresh timer, no overlapping equal probe, and no
   inactive-page polling.
6. Measure main-thread callback duration during idle/status refresh. Target
   **<50 ms p95**. Verify minimized/unmapped cadence is 30 seconds, idle is 5
   seconds, and active operation/chat status is 1 second.
7. During streamed generation, record UI responsiveness, GUI peak RSS/CPU,
   queue depth, rendered message count, and stop latency. During setup,
   download, and runtime build under normal 4 GiB host pressure, record peak
   GUI RSS plus host MemAvailable/OOM events. Any multi-GiB GUI allocation,
   OOM, swap storm attributable to the GUI, full-model host read, or use of
   `--no-mmap` fails.
8. Record bounded-list maxima: GUI/result queues 512 each, Models 100 visible,
   Activity 100 visible, Maintenance 5, logs 2,000 lines/2 MiB, and chat 500
   messages. Inspect behavior beyond each bound; do not merely quote constants.
9. Repeat idle measurements with notifications disabled and enabled. Record
   process/thread/FD/socket deltas and delivery-failure behavior; notices must
   create no polling thread or authoritative state.
10. Run the policy-required 24-hour minimum soak (and the separate mixed
    eight-hour GUI/chat/API/Maintenance scenario) with thermal monitoring.
    Record scalar duration, temperatures/latches, resource trends, failures,
    and recovery outcomes—never prompt or completion content.

## Result and release rule

Automated suite output may support `DEFAULT_TEST_SUITE`,
`SLOW_SECURITY_STRESS`, `CLEAN_WHEEL_SMOKE`, or `UPGRADE_MATRIX` only when the
release workflow binds and verifies it. Real device runs support
`HARDWARE_QUALIFICATION`, `SOAK_TEST`, and `BACKUP_RESTORE_HARDWARE`; the
independent review and observed participant runs support `SECURITY_REVIEW` and
`HUMAN_ACCEPTANCE`. Every record must use evidence schema v2 and pass the
attestation verification boundary described by `release/evidence/README.md`.

Any failed threshold, wrong host/profile cell, stale candidate identity,
privacy leak, second root, unsafe next boot, raw/public backend, Funnel,
unrecovered operation, or missing participant leaves EXP-8 **PENDING** or
failed. A plan, unit test, screenshot, old evidence record, or this worksheet
is never proof of physical or human qualification. The sole release evaluator
decides eligibility after every required verified record exists.
