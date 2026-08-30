# ADR 012 — Guided repair, durable cleanup, and bounded undo

**Status:** Accepted for EXP-5 implementation

## Context

BC250 LLM MODE already exposes read-only Doctor findings, a pure Repair Center
catalog, dry-run storage suggestions, durable operation recovery, model
quarantine, runtime rollback, desktop-return, credential, sharing, and support
bundle services. Those pieces are individually safe, but the user-facing
repair path is incomplete: a catalog entry is only a route string, previews do
not bind later execution to the state that was inspected, storage cleanup
cannot reclaim anything, and no service can prove when an Undo button is
truthful.

EXP-5 adds one typed command boundary. It does not add a database-reset
shortcut, a generic command runner, a generic Undo action, or permission for a
widget to mutate the host. Existing operation fencing, profile leases, thermal
safety, model identity, graphical-next-boot, credential privacy, and support
bundle redaction remain authoritative.

## Decisions

### D1 — Closed repair actions and conditions

`RepairActionId` is a closed vocabulary. The initial IDs and their only owning
mutation boundaries are:

| Stable action ID | Owning service or durable operation | Mutation summary |
| --- | --- | --- |
| `retry-legacy-import` | legacy import repair entrypoint | retry the unchanged legacy source without deleting it |
| `upgrade-newer-schema` | application update query only in this build | refuse reset and show verified-upgrade/backup guidance |
| `inspect-verified-backup` | `BackupCommandService` | inspect a verified backup before corruption/schema recovery |
| `reclaim-orphaned-content` | `STORAGE_CLEANUP v1` | quarantine only proven abandoned app staging |
| `release-expired-worker-locks` | fenced worker-lock repository | reclaim only a provably expired owner generation |
| `recover-durable-operation` | `OperationCommandService.recover` | resume an interrupted operation only when policy and leases permit |
| `regenerate-runtime-handoff` | `RuntimeConfigurationService` | render service handoff from verified current state |
| `restore-known-good-lineage` | `RUNTIME_ROLLBACK v1` | restore the verified known-good runtime/model/config lineage |
| `rotate-gateway-credentials` | `ConnectionCredentialCommandService` | rotate one named client credential without exposing secret bytes |
| `revoke-gateway-credentials` | `ConnectionCredentialCommandService` | revoke one named client credential |
| `disable-unsafe-sharing` | `SharingService` | disable remote publication; raw llama remains loopback-only |
| `quarantine-invalid-model` | `MODEL_REMOVE v1` | detach one invalid managed alias and retain its bytes in quarantine |
| `rebuild-service-launcher` | composed component/runtime services | regenerate only app-owned launcher/service artifacts from verified state |
| `return-to-desktop` | `HostModeService` | request the normal graphical next boot and preserve models |
| `rebuild-support-bundle` | `SupportBundleService` | create and self-check a local redacted bundle; never upload it |

No GUI or CLI caller supplies a module name, route string, executable, shell
fragment, or arbitrary callback. Composition constructs an exhaustive mapping
from each ID to one typed adapter. A missing mapping is a startup/test failure;
an unknown ID is `UNKNOWN_REPAIR_ACTION`.

Availability is derived from a closed `RepairConditionCode` set, including
legacy-import failure, newer/corrupt schema refusal, verified backup presence,
abandoned app staging, expired worker ownership, interrupted/recoverable
operation, stale handoff plus verified runtime, verified known-good lineage,
active client credential, unsafe sharing, invalid managed model, stale
app-owned service artifacts, supported desktop return, and support-bundle
capability. Every declared precondition must be true. Doubt or observation
failure makes the action unavailable with one closed reason code.

Database corruption or a schema newer than this application never offers
reset/reinitialize/delete. The only choices are bounded inspection, verified
backup restore, or an eligible compatible application upgrade.

### D2 — Complete typed action descriptor

Each `RepairAction` contains only bounded serializable fields:

- stable ID, title/message keys, owner kind and owner ID;
- ordered precondition codes and a closed unavailability reason;
- an ordered mutation preview (maximum 16 steps), privilege class, resource
  keys, cancellation policy, estimated duration class, and estimated byte
  delta/range;
- reversibility class (`EXACT_UNTIL`, `COMPENSATED_BY_OWNER`, or
  `IRREVERSIBLE`), success probe ID, closed failure map, and support relevance;
- target kind and whether the prior working state is retained.

Free-form exception text, raw command output, secret material, absolute paths,
hostnames, addresses, prompts, and responses are not descriptor fields. UI
copy is selected from bundled keys. Duration and space values are estimates,
never progress or capacity promises.

### D3 — Preview is an optimistic-concurrency capability, not decoration

`RepairCommandService.preview(action_id, target)` performs bounded observation
and returns a `RepairPreview` with schema/contract version, action/target
identity, ordered mutation steps, closed warnings, privilege/cancellation/
reversibility details, success probe, expected revision map, evidence digest,
expiry, and a confirmation token.

The token is SHA-256 over canonical JSON containing the contract version,
action and target identities, expected durable revisions/lease generations,
mutation plan, evidence digest, and expiry. Raw evidence and secrets are not
embedded. Preview rows are not persisted; the caller supplies the preview and
token back to `run`.

Immediately before mutation, `run` reconstructs the preview from current
truth. Action ID, target, expected revisions, mutation steps, evidence digest,
expiry, and token must match exactly. A changed revision, lease, target,
credential generation, runtime lineage, access state, or filesystem identity
returns `PREVIEW_STALE` and performs no mutation. Confirmation cannot bypass a
failed precondition. Actions marked destructive or irreversible require an
explicit token even in the GUI.

`RepairResult` contains the stable action ID, outcome/result code, owning
operation ID when applicable, post-effect probe result, whether prior state
survives, support relevance, and bounded offline recovery commands selected
from a closed table. A successful underlying command without a successful
probe is not reported as repaired.

### D4 — One durable `STORAGE_CLEANUP v1` workflow

Cleanup joins the one frozen registry as `STORAGE_CLEANUP`, request version 1,
recovery policy version 1, using the existing operations/steps/events/leases
tables. EXP-5 adds no schema migration; migration 014 remains reserved for
application installations in EXP-6.

The request is closed and bounded to 32 targets. Each target carries a stable
target ID, kind, relative identity below an approved root, expected streaming
tree digest, expected byte/file counts, action, and retention/deadline facts.
The allowed modes are:

- `QUARANTINE`: move proven abandoned entries from the app-owned model staging
  root to `<model quarantine>/cleanup/<operation-id>/` using same-filesystem,
  no-replace renames and an fsynced bounded receipt;
- `RESTORE`: the exact inverse, from a verified cleanup receipt to its original
  still-empty app staging identity before the retention deadline;
- `PURGE`: permanently delete only expired cleanup-quarantine entries whose
  receipt and identity still verify.

The operation holds `model-storage` and `storage-cleanup` resources in
lexicographic order. It resolves and re-verifies exclusions, records pre-effect
free space, applies the effect, probes source/destination identities, and then
records post-effect free space. Cancellation is safe before the mutation step
and after a complete reversible quarantine/restore. It is refused while the
critical rename/purge step is `COMMITTING`; permanent deletion has no Undo.

A crash between any two target effects is recovered by inspecting each exact
source, destination, digest, and receipt. An exact completed effect is
checkpointed once; an exact not-yet-applied effect continues; collision,
partial identity, missing receipt, or conflicting source/destination becomes
`RECOVERY_REQUIRED`. Recovery never guesses from directory age or a stale
step row.

### D5 — Cleanup eligibility and exclusions are fail-closed

The default selection contains only app-owned staging whose operation identity
is known and whose operation is terminal or absent with positive abandoned
evidence. Age alone is insufficient. Quarantine entries become purge
candidates only after seven full days, a verified cleanup receipt, and an
explicit irreversible selection. Managed artifacts may be reported as
unreferenced but are not selected by `STORAGE_CLEANUP v1`; model removal owns
their lifecycle.

Cleanup never targets:

- active, queued, paused, interrupted, or `RECOVERY_REQUIRED` operation data;
- the active model, known-good model/runtime/config lineage, active or rollback
  runtime trees, newest verified backup, sole verified backup, current/prior
  application release, database, credential files, conversations, or logs;
- content outside the exact app-owned staging and cleanup-quarantine roots;
- symlinks, mount crossings, special files, unknown receipt versions,
  mismatched identities, or an externally managed/model path.

The dry run lists exact opaque identities, closed reasons, estimates, default
selection, exclusions, preview digest, and confirmation token. It reads model
files in bounded streaming chunks and never loads a tree or GGUF into RAM.
`--apply` requires that exact unexpired token. Post-purge success requires both
absence of every selected identity and observed free space no lower than the
pre-effect value (allowing a documented filesystem tolerance); otherwise the
result retains evidence and requires recovery.

### D6 — Undo is derived evidence, never a generic inverse

`UndoService.list` derives candidates from successful durable operations and
their verified effects. It shows an item only when all are true:

1. the workflow version declares one exact inverse;
2. the prior identity and receipt still exist and verify;
3. the deadline has not passed;
4. no later operation, revision, lease generation, runtime promotion,
   credential generation, or access-state change superseded it;
5. all required resource leases are currently available; and
6. the inverse's current preconditions pass.

The initial mechanically supported Undo is cleanup `QUARANTINE` → cleanup
`RESTORE`. It enqueues a new `STORAGE_CLEANUP v1` child operation bound to the
source operation and receipt digest. It never rewrites history. Rotation after
its overlap window, credential revocation, permanent purge, support-bundle
creation, desktop return, service regeneration, and repair/recovery commands
are not labeled Undo. Runtime and model recovery remain their named durable
workflows, not a misleading generic inverse.

`undo preview` uses the same canonical preview/revision/token rule as repair.
An expired or superseded candidate disappears from the available list and a
direct invocation refuses with `UNDO_EXPIRED` or `UNDO_SUPERSEDED` before
mutation.

### D7 — Support handoff is local, bounded, and self-verifying

Every unavailable/failed repair exposes stable condition, action, result, and
probe IDs plus whether the prior working state survives. It may offer the
existing redacted support-bundle builder. The bundle is generated only on an
explicit local request, is bounded and cancellable, self-checks its manifest
and file digests, and is never uploaded by the app.

Offline recovery commands come from a closed argument/template table and may
contain only validated opaque operation/action identifiers. They never contain
credentials, authorization headers, model paths, arbitrary exception text, or
shell syntax. Support output retains existing redaction guarantees and adds
only closed repair/cleanup/undo codes and aggregate counts.

### D8 — GUI and CLI share the same composed services

The Maintenance repair page consumes typed list/preview/result views. It uses
the existing in-window confirmation/log drawer and bounded task lane. Widgets
do not call host commands, filesystem mutation, repositories, or operation
adapters. No route string is executable.

CLI parity is:

```text
bc250-llm-mode repair list
bc250-llm-mode repair preview <action-id> [target]
bc250-llm-mode repair run <action-id> [target] --preview <digest> --confirm <token>
bc250-llm-mode repair verify <action-id> [target]
bc250-llm-mode undo list
bc250-llm-mode undo preview <undo-id>
bc250-llm-mode undo run <undo-id> --preview <digest> --confirm <token>
bc250-llm-mode storage cleanup --dry-run
bc250-llm-mode storage cleanup --apply --preview <digest> --confirm <token>
```

Machine-readable output is bounded, uses stable codes, and never prints a
credential or raw support content. Headless callers cannot bypass disclaimer,
privilege, confirmation, revision, lease, or recovery barriers.

### D9 — Result and failure vocabulary

Closed command outcomes are `AVAILABLE`, `UNAVAILABLE`, `READY`, `ACCEPTED`,
`SUCCEEDED`, `REFUSED`, `FAILED_SAFE`, and `RECOVERY_REQUIRED`. Common refusal
codes include `UNKNOWN_REPAIR_ACTION`, `PRECONDITION_UNMET`, `TARGET_REQUIRED`,
`PREVIEW_STALE`, `CONFIRMATION_REQUIRED`, `PRIVILEGE_REQUIRED`, `LEASE_HELD`,
`RECOVERY_BARRIER_MANUAL`, `UNSUPPORTED_PLATFORM`, `IDENTITY_MISMATCH`,
`EXTERNAL_PATH_EXCLUDED`, `UNDO_EXPIRED`, and `UNDO_SUPERSEDED`.

Exceptions and subprocess output are mapped to closed codes. They may be sent
through existing bounded/redacted logs, but are never copied into notices,
receipts, command results, or support summaries. Delivery/notification failure
does not change a repair or cleanup result.

## Rejected alternatives

- executing the old `routes_to` string, arbitrary commands, dynamic imports,
  or widget-owned subprocesses;
- a one-click “repair everything”, automatic repair/cleanup, or repair during
  refresh;
- database reset, deleting a newer database, or silently recreating state;
- age-only cleanup, recursive deletion of arbitrary paths, following symlinks,
  deleting external models, or deleting active/known-good/newest-backup data;
- immediate deletion when a reversible quarantine can preserve evidence;
- a generic Undo button, an Undo based only on elapsed time, or history edits;
- uploading a support bundle, including secrets/raw logs/prompts, or inventing
  hardware evidence from a developer test.

## Verification and evidence

Deterministic tests must prove one typed composition mapping for every action,
closed preconditions and failures, preview/execution revision equality,
confirmation and privilege refusal, no dynamic route execution, exact cleanup
selection/exclusions, symlink/special-file/mount rejection, bounded streaming,
all crash points around quarantine/restore/purge/checkpoint, lease ordering,
post-effect probes/free-space checks, undo expiry/supersession, support-bundle
privacy canaries, CLI/GUI parity, and no schema change before EXP-6.

Physical Bazzite and CachyOS qualification must exercise an interrupted cleanup
and recovery, runtime rollback, credential rotation/revocation, gateway safety
disable, desktop return, model quarantine/retention, cleanup restore and purge,
and a locally self-checked support bundle on the exact candidate. This ADR and
developer tests claim none of that physical evidence.
