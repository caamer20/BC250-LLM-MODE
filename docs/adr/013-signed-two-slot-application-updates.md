# ADR 013 — Signed two-slot application updates and exact rollback

**Status:** Accepted for EXP-6 implementation; production channel unavailable
until an eligible signed release and its physical evidence exist

## Context

BC250 LLM MODE has a release evaluator that is the sole authority for release
eligibility, a content-bound release manifest and inventory, verified evidence
records, durable backup/restore operations, and crash-recoverable workflows.
It does not yet have an application updater. The current repair guidance is
deliberately query-only: it refuses to reset a newer database and cannot
recommend an untrusted package command.

An application update is more dangerous than a llama.cpp runtime update. It
can replace the process that is performing the update and can migrate the
only durable profile. A successful wheel install is therefore not sufficient
evidence. The update must prove release trust, preserve an executable prior
application, create and verify a profile backup before any schema change, and
recover by inspecting actual filesystem and database identities after death.

EXP-6 adds that boundary. It does not create an automatic updater, trust a
branch or a “latest” URL, add a package-index install path, invent a signing
key, or claim the currently blocked development candidate is eligible.

## Decisions

### D1 — The release evaluator remains the sole eligibility authority

`ApplicationReleaseVerifier` accepts one complete, immutable release set and
returns a typed `VerifiedApplicationRelease` only when every condition below
passes:

1. the repository identity is the package-owned expected repository;
2. the source commit is a full immutable SHA and the source ref is exactly
   `refs/tags/v<version>`;
3. the decision document is schema v3, is internally canonical, names the
   same candidate and inventory, and says `eligible_for_1_0_0=true` with no
   blocking codes;
4. the release manifest is schema v3, has `release_status=QUALIFIED`, has a
   valid canonical manifest digest, and names that exact decision/candidate;
5. the inventory is schema v2, has the required unique artifact roles, and
   its canonical digest and every size/hash match the bounded files on disk;
6. the checksums file agrees with the inventory and contains no extra or
   missing subject;
7. the CycloneDX SBOM is valid and its subject is the exact wheel digest;
8. approved build provenance binds the repository, immutable ref, commit,
   builder policy, and complete subject set;
9. a detached release signature verifies the canonical release-set envelope
   against a package-owned, reviewed trust-root ID;
10. verified evidence used by the eligible decision includes the advertised
    host platform and migration/backup qualification; and
11. the candidate declares a database compatibility interval containing the
    installed schema and an output schema supported by its migration plan.

The updater re-validates the evaluator-produced decision; it never constructs
an eligible `ReleaseDecision`, accepts a caller boolean, or weakens the
evaluator policy. Cryptographic adapters return only a verified typed value;
string fields such as `verified=true`, a certificate name, or a CI URL are
not trust.

The initial production build contains no reviewed update signing root and no
eligible channel descriptor. `update check`, preview, GUI Updates, and the
newer-schema repair action therefore return
`SIGNED_UPDATE_CHANNEL_UNAVAILABLE`. Tests may inject a local trust adapter
and evaluator-produced fixtures, but those fixtures are never installed as
production trust material and never generate physical/release evidence.

Branches, mutable release pages, redirects, arbitrary URLs, package-index
resolution, `pip install <name>`, unsigned wheels, raw GitHub artifacts, and
locally edited decision/manifest files are never authorities.

### D2 — One bounded release-set and channel contract

The release-set format version is 1. A bundle contains exactly these logical
roles, each once: wheel, sdist, checksums, CycloneDX SBOM, inventory, final
release manifest, final release decision, verified-evidence envelope,
provenance envelope, detached signature, compatibility document, and signed
plain-text release notes. File names are taken from the signed inventory or a
closed metadata map; no member name becomes a filesystem path without strict
validation.

The signed canonical envelope binds:

- format and verifier-policy versions;
- repository, version, source commit, exact protected tag, and publication
  timestamp;
- release-manifest, decision, inventory, evidence-set, provenance,
  compatibility, and release-note digests;
- every non-signature member name, role, byte size, media type, and SHA-256
  digest (the detached signature cannot recursively sign its own digest); and
- signing trust-root ID and signature mechanism.

Documents are bounded before parsing (individual metadata 1 MiB, combined
metadata 8 MiB, notes 32 KiB, member count 32, path length 240, wheel/sdist
sizes bound by the signed inventory). JSON rejects duplicate keys, non-UTF-8,
non-canonical values where canonical form is required, unknown schema
versions, unknown fields, and unbounded depth/strings.

An online `ReleaseChannel` is a package-owned immutable endpoint policy with
an expected repository and trust-root ID. It may return only a signed channel
index whose candidate entries bind an exact immutable release-set digest and
URL. HTTPS transport is defense in depth, not identity. Redirects, cross-host
fetches, credentials in URLs, mutable aliases, and a server-selected
destination are refused. Query is explicit, bounded, timeout-limited, and
read-only; refresh never contacts it.

Offline import accepts the same format and runs the identical verifier. The
archive reader rejects absolute/traversal/non-normalized names, case-folded
or Unicode-normalized duplicates, links, devices, FIFOs, sockets, sparse or
encrypted members, unknown extras, decompression/size/count limit breaches,
and mutation between verification and staging. Offline never means unsigned.

### D3 — Status and preview are typed, complete, and safe to render

`ApplicationUpdateQueryService.status()` reports the installed application
version, source commit, release-set/content digest, slot identity, database
schema, configured channel ID, current/previous pointer identities, pending
operation, and recovery barrier. Missing legacy provenance is labeled
`UNVERIFIED_LEGACY_INSTALL`; it is not filled with guessed values.

`check()` returns a bounded candidate summary or one closed refusal reason.
`preview(version)` re-verifies the release set and reports:

- immutable version/ref/commit/release-set identity, publication date, and
  bounded download/stage/retained byte estimates;
- platform qualification and source/target database compatibility;
- signed plain-text notes, rendered as literal text only (never Markdown,
  HTML, links, images, terminal escapes, or widget markup);
- required free space, verified-backup plan, restart/post-update
  acknowledgment plan, migration plan, retained rollback slot, and whether
  exact profile restore would be required;
- expected installation/profile revisions and a canonical preview digest;
  and
- a closed reason code for every disabled action.

Apply requires the exact unexpired preview digest and confirmation token. A
candidate, channel, current pointer, previous pointer, profile schema,
installation revision, active update lease, backup status, or free-space
change makes the preview stale before any effect.

### D4 — Migration 014 records installation truth, not trust assertions

Migration 014 adds three revision-fenced tables:

`application_installations` records one content-addressed staged/published
release: installation ID, version, commit, ref, repository, release-set,
manifest, inventory and wheel digests, source/min/max/target schema, platform
qualification identity, release directory identity, state
(`STAGED`, `CURRENT`, `PREVIOUS`, `QUARANTINED`), smoke result, created/
published/last-verified timestamps, and revision.

`application_installation_state` is a singleton carrying current and previous
installation IDs, pointer generation, pending update operation, recovery
barrier, last acknowledged installation/process nonce, and revision. Foreign
keys retain history; deleting an installation referenced as current/previous
is impossible.

`application_update_imports` records only the opaque release-set digest,
source class (`CHANNEL` or `OFFLINE`), verification policy/trust-root IDs,
verification time, bounded state, and revision. It never stores URLs,
credentials, signatures, release notes, raw provenance, absolute paths, or
unverified claims.

Migration is atomic and preserves all v13 rows. It does not seed a fake
installation from the running package. Legacy installs remain explicitly
unverified until a separate observation binds package metadata and a stable
launcher layout without claiming release eligibility.

### D5 — Two immutable slots and a stable launcher

The user-local installation root is:

```text
releases/<release-set-sha256>/venv/
current  -> releases/<release-set-sha256>/
previous -> releases/<release-set-sha256>/
```

The identity is the lowercase 64-character release-set SHA-256. Releases are
immutable after staging. A stable, package-owned launcher resolves `current`
without shell evaluation and executes that slot's fixed console entry point.
No desktop entry, service, or user command embeds a versioned venv path.

Staging occurs in an operation-owned sibling on the same filesystem. It
creates a fresh venv, installs the exact verified wheel from the held bundle
with dependency/index access disabled, verifies installed file identities,
runs `pip check`, imports every mandatory package surface, initializes a
throwaway database through schema 014, runs read-only composition/doctor
smoke, and starts no model, service, gateway, WebUI, or Tailscale process.
Failure removes only the operation-owned uncommitted staging directory after
identity checks.

Publication uses one minimal `ApplicationPointerPublisher`. It accepts only
validated release-root-relative identities and a digest-pinned request,
re-opens and verifies both slots, creates sibling temporary symlinks, fsyncs
the directory, and atomically replaces `previous` then `current` using
same-filesystem rename. A receipt binds pre/post link targets, pointer
generation, release digests, helper implementation digest, and operation ID.
It refuses non-symlinks, paths outside the owned root, cross-filesystem state,
unknown existing targets, a changed digest, or a concurrent generation.

No updater writes the running venv in place.

### D6 — Profile backup and post-update acknowledgment are mandatory

Before a migration-capable launch, the workflow creates a `BACKUP_CREATE v1`
child operation, waits for its terminal result without holding the backup
resource, and independently re-verifies its manifest, archive digest, source
schema, and restore compatibility. The newest backup setting is not enough.
The backup identity is checkpointed before pointer publication.

After the pointer switch, the stable launcher starts `current` in a bounded
`--post-update <operation-id> <nonce>` mode. Only the new slot may write the
acknowledgment. It verifies its own release-set identity, opens the profile
through the normal migration boundary, runs SQLite integrity/foreign-key/
schema checks plus composition, application snapshot, model-library read,
and host-platform observation, starts no model, and writes an atomic bounded
ack containing the operation/nonce/slot/schema and probe codes. The old
worker accepts it only when all identities match, then re-opens the database
and verifies the target schema itself.

Timeout, process death, identity mismatch, migration failure, or smoke failure
initiates rollback. The pointer returns to the exact prior slot. If the
profile schema changed, rollback also runs `BACKUP_RESTORE v1` against the
exact verified pre-update backup before the prior application is launched.
Pointer and profile rollback are independently verified. If either outcome is
ambiguous, the operation ends `RECOVERY_REQUIRED`, retains both releases and
backup/staged profiles, and starts nothing automatically. A database is never
reset or silently recreated.

### D7 — One durable `APPLICATION_UPDATE v1` workflow

`APPLICATION_UPDATE` joins the frozen registry with request version 1 and
recovery-policy version 1. Its closed request selects mode `APPLY` or
`ROLLBACK`, release-set identity, expected current/previous installation IDs,
preview digest, confirmation token, and surface. It carries no URL, path,
signature, release note, command, secret, or arbitrary argument.

The forward apply checkpoints are:

1. re-verify the held release set and compatibility;
2. reserve space and stage immutable candidate;
3. run isolated install and smoke;
4. enqueue/wait for/re-verify the profile backup;
5. enter `COMMITTING` and atomically publish pointers;
6. launch the bounded post-update process and verify its acknowledgment;
7. verify migrated profile and composed health with no model running;
8. commit installation/database records and release reservations; and
9. retain current plus the one verified prior release.

Rollback uses the same workflow in `ROLLBACK` mode and the same pointer,
compatibility, backup/restore, acknowledgment, and verification rules. It is
not a symlink-only shortcut.

The resources are `application-installation` and `profile-publication`, always
acquired lexicographically. The workflow does not hold a resource while
waiting for a child backup/restore operation that needs that resource.
Cancellation is safe before staging and after an uncommitted staging result.
It is refused from backup commitment through pointer/migration/health
resolution. Concurrent apply/rollback/cleanup refuses with `UPDATE_BUSY`.

At every crash boundary—verification, staging, smoke, backup, pointer switch,
new-process acknowledgment, schema migration, health, rollback switch,
profile restore, and cleanup—takeover compares actual pointer targets,
immutable release identities, pointer receipt/generation, profile schema,
backup/restore receipts, and acknowledgment nonce. It checkpoints an exact
completed effect once, continues an exact absent effect, performs the defined
rollback when safe, and otherwise stops at `RECOVERY_REQUIRED`. Step rows are
never treated as proof of external state, and stale workers are fenced from
every mutation.

### D8 — Retention and cleanup preserve the last readable profile

Normal retention keeps exactly the current release and one fully verified
prior release. A staged failed candidate is quarantined and may be offered to
the typed storage-cleanup service only after no operation, pointer, receipt,
backup, or acknowledgment references it. Update cleanup defaults to dry-run
and cannot remove either pointer target, the only release able to read the
current schema, an unresolved recovery release, or an unverified legacy
installation. Destructive cleanup requires a fresh digest/confirmation and
has no Undo after purge.

A downgrade or rollback is available only when the prior slot declares the
current schema readable or an exact verified pre-update backup remains
restorable. “Older version installed” is not compatibility evidence.

### D9 — GUI and CLI share one composed command/query boundary

The in-window Updates page consumes only typed status/check/preview/result
views and uses the existing confirmation/log drawer and bounded mutation lane.
Widgets do not fetch URLs, verify signatures, open archives, run pip, switch
pointers, migrate databases, or spawn the replacement process. Refresh calls
status only; channel checks are explicit user actions.

CLI parity is:

```text
bc250-llm-mode update status
bc250-llm-mode update check
bc250-llm-mode update preview <version>
bc250-llm-mode update apply <version> --preview <digest> --confirm <token>
bc250-llm-mode update import-bundle <path>
bc250-llm-mode update rollback --preview <digest> --confirm <token>
bc250-llm-mode update cleanup --dry-run
```

Machine output is bounded and uses the same stable codes. The bundle path is
accepted only by the offline-import adapter, canonicalized below an explicitly
selected existing file, and never persisted. Headless callers cannot bypass
trust, backup, space, preview, confirmation, compatibility, lease, or recovery
barriers.

### D10 — Closed outcomes and honest production availability

The closed top-level outcomes are `AVAILABLE`, `UNAVAILABLE`, `READY`,
`ACCEPTED`, `SUCCEEDED`, `ROLLED_BACK`, `FAILED_SAFE`, and
`RECOVERY_REQUIRED`. Refusal/failure codes include:

`SIGNED_UPDATE_CHANNEL_UNAVAILABLE`, `CHANNEL_POLICY_INVALID`,
`CHANNEL_FETCH_FAILED`, `REDIRECT_REFUSED`, `UNTRUSTED_REPOSITORY`,
`IMMUTABLE_REF_REQUIRED`, `RELEASE_DECISION_INELIGIBLE`,
`MANIFEST_MISMATCH`, `INVENTORY_MISMATCH`, `CHECKSUM_MISMATCH`,
`SBOM_MISMATCH`, `PROVENANCE_INVALID`, `SIGNATURE_INVALID`,
`PLATFORM_EVIDENCE_MISSING`, `DATABASE_INCOMPATIBLE`, `BUNDLE_MALFORMED`,
`BUNDLE_MEMBER_REFUSED`, `UNKNOWN_BUNDLE_MEMBER`, `NOTES_INVALID`,
`INSUFFICIENT_SPACE`, `VERIFIED_BACKUP_REQUIRED`, `PREVIEW_STALE`,
`CONFIRMATION_REQUIRED`, `UPDATE_BUSY`, `STAGING_FAILED`, `SMOKE_FAILED`,
`POINTER_IDENTITY_MISMATCH`, `POST_UPDATE_ACK_TIMEOUT`,
`POST_UPDATE_ACK_INVALID`, `PROFILE_MIGRATION_FAILED`,
`PROFILE_RESTORE_FAILED`, `ROLLBACK_UNAVAILABLE`, and
`RECOVERY_BARRIER_MANUAL`.

Raw verification exceptions, URLs, signatures, certificate content, host
output, filesystem paths, environment variables, credentials, and release
notes are never written to operation details, receipts, notifications, or
support summaries. The existing bounded/redacted setup log may carry safe
diagnostics. An application-update notification is emitted only after this
verifier records an eligible candidate identity; notification failure never
changes update truth.

## Rejected alternatives

- `pip install --upgrade`, installing into the running venv, or resolving
  dependencies from a package index;
- trusting HTTPS, GitHub, a tag-shaped string, a checksum alone, an unsigned
  offline bundle, or a caller-provided “verified” flag;
- auto-check during refresh, automatic download/apply, a background updater,
  tray daemon, or update-at-boot;
- rendering remote Markdown/HTML or following release-note links;
- copying application trees across filesystems, replacing pointers without a
  digest/generation fence, or deleting the prior slot before acknowledgment;
- running a model, gateway, Open WebUI, or Tailscale during smoke;
- migrating the only profile without a verified backup, rolling a pointer
  back while leaving an unreadable schema, resetting a database, or guessing
  recovery from step state;
- treating developer fixtures, old candidate evidence, or a local wheel as
  physical/release qualification.

## Verification and evidence

Deterministic tests must cover every trust-property mutation independently,
duplicate/oversize/non-canonical documents, signature/provenance/evaluator
binding, literal note rendering, online redirect/host refusal, every offline
archive negative, migration 014 atomicity and old-row preservation, CAS and
concurrency, source/editable/wheel parity, stable launcher behavior, isolated
staging, no-autostart/no-model smoke, two-slot parity, backup binding, every
crash point and stale-worker fence, schema migration/rollback/profile restore,
retention exclusions, GUI/CLI parity, secret-free database/support output, and
architecture guards preventing mutable update sources or a second evaluator.

Physical Bazzite and CachyOS qualification must use one exact eligible signed
candidate and exercise normal update, restart acknowledgment, migrated profile
integrity, application rollback, exact profile restore, interrupted recovery,
desktop-next-boot behavior, and no model autostart. Until eligible signed
artifacts, reviewed trust material, and candidate-bound physical backup/
restore evidence exist, production update remains honestly unavailable. This
ADR and developer tests claim none of those external results.
