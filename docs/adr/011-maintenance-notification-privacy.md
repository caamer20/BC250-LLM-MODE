# ADR 011 — Maintenance notification privacy and restart-safe deduplication

**Status:** Accepted for EXP-4 implementation

## Context

The appliance now has a bounded, query-only Maintenance snapshot. Long model
operations, thermal stops, backup failures, and storage pressure can still
finish or occur while the native window is closed or on another page. A local
desktop notification can help, but it must not become a second monitoring
loop, disclose what the user asked a model, expose a client or network
identity, or report an operation before its durable terminal event commits.

Notification delivery is best-effort presentation. Durable operations,
thermal safety, backup truth, authenticated sharing, and update eligibility
remain authoritative even when the desktop has no notification capability.

## Decisions

### D1 — Closed, opt-in categories

Migration 013 stores preferences only for these stable categories:

| Category | Eligible event | Default urgency |
| --- | --- | --- |
| `OPERATION_SUCCESS` | a long durable operation commits `SUCCEEDED` | normal |
| `OPERATION_FAILURE` | a long operation commits `FAILED_SAFE`, `FAILED_ROLLED_BACK`, or `RECOVERY_REQUIRED` | normal or critical for recovery |
| `THERMAL_WARNING` | the existing watchdog commits a throttled latch transition | normal |
| `THERMAL_STOP` | the existing watchdog commits a stopped latch transition | critical |
| `STORAGE_CRITICAL` | a bounded check observes free space below the reviewed critical floor | critical |
| `BACKUP_FAILURE` | backup/restore commits a failure terminal | normal or critical for recovery |
| `BACKUP_STALE` | an opted-in bounded maintenance check proves the freshness bound exceeded | normal |
| `REMOTE_SAFETY_DISABLE` | the sharing safety path durably disables remote access | critical |
| `APPLICATION_UPDATE` | the release verifier records an eligible signed application update | normal |

Unknown categories fail closed. Normal application setup, model/server start,
chat responses, tokens generated, client connections, ordinary service
status, and recommendations never notify.

Notifications are optional and disabled by default for a profile that has no
explicit prior preference. Migration may honor a durably stored legacy
`notifications_enabled=true` as a deliberate master opt-in; the former
in-memory default is not evidence of consent. A master switch and each
category switch are independent. Disabling the master stops delivery without
rewriting category choices. Safety behavior continues regardless of all
notification preferences.

### D2 — Fixed privacy-safe presentation

The title is always `BC250 LLM MODE`. The body is selected only from a bundled
category/result-code message table. Permitted bodies are generic statements
such as “A model operation finished,” “An operation needs recovery,” “The LLM
server stopped for temperature safety,” or “Model storage is critically low.”

Titles, bodies, command arguments, receipt rows, debug logs, events, tests, and
support bundles must never contain:

- model or filesystem paths, repository names, client labels, hostnames,
  addresses, URLs, ports, usernames, or machine identifiers;
- prompts, completions, conversation titles/content, remote request/response
  bodies, model-generated text, or benchmark prompt text;
- credentials, fingerprints, authorization headers, environment values, or
  secret-file identities;
- exception messages, command output, log tails, arbitrary operation detail,
  or user-entered free text.

Only a closed category, closed result code, urgency, generic action route, and
non-sensitive count may select presentation. No caller-supplied title or body
exists on the production adapter API. Notification activation routes to the
one existing application window and a bundled route; it never embeds event
content in an activation request.

### D3 — Migration 013 persistence boundary

Migration 013 adds two tables:

`notification_preferences`

- one row per closed category plus one reserved master row;
- enabled boolean, created/updated timestamps, and revision;
- expected-revision compare-and-swap for preference changes.

`notification_receipts`

- a 64-hex versioned deduplication key as primary key;
- closed category and closed source class;
- delivery state `DELIVERED`, `SUPPRESSED`, or `FAILED`;
- closed reason code, first/last-attempt timestamps, occurrence count, and
  adapter class;
- no title/body, operation ID, model ID, path, address, version string,
  exception, or arbitrary detail.

The migration is forward-only and atomic, preserves every v12 row, and never
delivers a notification. It seeds explicit preferences but no receipts. At
most 256 receipts are retained; insertion prunes oldest terminal receipts by
timestamp and key in the same transaction. An active safety condition is
represented by domain truth, never kept alive by a notification row.

### D4 — Versioned event identity and cross-restart deduplication

The receipt key is SHA-256 over a canonical, versioned tuple assembled inside
the owning service. Raw tuple components are not stored. Eligible identities
are:

- operation: category + operation ID + committed terminal state + terminal
  revision;
- thermal: category + durable latch transition timestamp + resulting latch;
- storage/backup staleness: category + evidence fingerprint + UTC six-hour
  window;
- remote disable: category + durable access-state revision;
- application update: category + verified release/inventory identity.

Retries reuse the exact key. A delivered receipt suppresses all later attempts
for that key across process restart. A failed adapter attempt may be retried at
most twice within its original rate-limit window; failure never changes or
replays the domain event. Changed evidence produces a new key only through the
closed identity builder.

### D5 — Rate limits and collapse policy

Delivery is bounded to three notifications per rolling hour per profile and
one per category per ten minutes. `THERMAL_STOP`, `REMOTE_SAFETY_DISABLE`, and
a new `RECOVERY_REQUIRED` terminal may bypass the per-category ten-minute
cooldown once, but never the cross-restart identical-key dedupe or the global
hourly bound. Additional occurrences become `SUPPRESSED` receipts with a
closed reason and incremented bounded count; they do not queue for later.

Success events for several operations in one ten-minute category window
collapse into the generic operation-success notice. Failure and success are
never collapsed together. Thermal warning and thermal stop remain distinct.
The application does not create a backlog or replay old notices at login.

### D6 — Capability detection and delivery adapter

The production adapter detects the current desktop-session capability and the
fixed `notify-send` executable. It installs nothing, opens no D-Bus service of
its own, invokes no shell, and accepts no caller-provided command. Delivery is
one fixed argument vector containing only the bundled title/body, reviewed
urgency, application icon/name, and bounded expiry. The adapter has a short
fixed timeout and bounded stdout/stderr capture; captured content is discarded
and never persisted.

No capability yields `UNAVAILABLE`, not an exception that changes domain
state. Nonzero exit, timeout, closed session bus, or desktop refusal yields a
redacted `FAILED` receipt and generic GUI/CLI status. The app never silently
installs a notification package.

### D7 — No tray, daemon, duplicate watcher, or GUI polling loop

There is no tray icon, resident GUI, new sensor reader, new operation watcher,
or notification thread. Producers call the composed notification coordinator
only at these existing authority boundaries:

- after the durable operation terminal event transaction commits;
- from the existing thermal watchdog after its latch write commits;
- after an explicitly enabled bounded maintenance/systemd check commits its
  redacted observation;
- after the authenticated sharing safety-disable commit;
- after the future signed updater commits verified availability.

The native GUI may run a user-requested **Test notification** through its
existing bounded task lane. It must not poll solely to deliver notices.
Closing the GUI neither starts nor stops notification monitoring.

### D8 — Failure isolation, ordering, and cancellation

Notification delivery always happens after domain commit and outside that
transaction. It never holds an operation resource lease, delays compensation,
changes a terminal result, clears a thermal latch, enables sharing, or affects
update eligibility. Receipt failure is best-effort: if the receipt transaction
also fails, the domain result remains unchanged and the coordinator returns a
generic failure status to its caller.

Operation success cannot be announced before `SUCCEEDED` and its terminal
event are durable. A cancellation announces only through the operation-failure
category when it requires user attention; ordinary safely completed
`CANCELLED` work is silent. `FAILED_ROLLED_BACK` copy states explicitly that
the prior state was restored; they never expose failure detail.

### D9 — Query, preferences, CLI, and support behavior

`NotificationPreferenceService` owns master/category status and CAS updates.
`NotificationCoordinator` owns eligibility, identity, dedupe, rate limiting,
fixed-copy selection, adapter delivery, and receipt recording. Queries are
bounded, show capability and preferences, and expose only aggregate delivered/
suppressed/failed counts plus last generic category/time.

CLI parity is:

```text
bc250-llm-mode notifications status
bc250-llm-mode notifications test
bc250-llm-mode notifications set <category|all> on|off
```

`test` requires the master preference on, produces fixed test copy, carries no
domain event, and is rate-limited but not retained as a domain receipt. Support
bundles include capability, preference booleans, aggregate counts, and closed
failure codes only. The Maintenance inbox is useful with notifications off.

## Rejected alternatives

- notifications enabled merely because a code default was true;
- caller-provided notification titles/bodies or exception interpolation;
- storing rendered notification bodies, raw event identities, paths, client
  labels, addresses, or operation details;
- delivery inside an operation transaction or before terminal-event commit;
- a tray daemon, resident GUI, duplicate thermal loop, polling notification
  thread, backlog, or login replay;
- shell command construction, silent package installation, cloud push, mobile
  push, telemetry, email, SMS, or public webhook delivery;
- unlimited receipts/retries or in-memory-only dedupe;
- turning a notification failure into an operation/backup/safety failure.

## Verification and evidence

Deterministic gates must cover v12→v13 preservation and interruption,
explicit legacy opt-in migration, category/CAS bounds, canonical receipt-key
stability, cross-restart dedupe, the hourly and category rate limits, critical
bypass limits, terminal-event ordering, adapter unavailability/timeout/failure,
receipt cap/pruning, fixed argv/no shell, no threads/tray/poll loop, and domain
result isolation.

Privacy canaries representing paths, hostnames, addresses, labels, prompts,
responses, credentials, headers, exceptions, and log bodies must be absent
from SQLite, adapter arguments, application logs, events, notices, CLI JSON,
and support bundles. Physical KDE qualification on Bazzite and CachyOS must
exercise enabled, disabled, unavailable-session, dedupe, thermal-stop, and
delivery-failure behavior against the exact candidate. No such physical
evidence is claimed by this ADR.
