# ADR 006 — Durable Backup Creation and Restore Publication

**Status:** Accepted (Release-closure C2)

Supersedes: the P8 pure-manifest (`backup_manifest.py`) and dry-run
(`backup_restore.py`) contracts remain the validation authority; this ADR makes
backup/restore real, durable, crash-recoverable operations (REL-004).

## 0. Problem

P8 delivered a secret-free manifest model and a fail-closed dry-run restore
gate, but no durable create/restore operations and no atomic profile
publication. The restore PUBLISH step (atomic profile-level swap) and a full
round trip were recorded as hardware-gated pending evidence. For `1.0.0`,
backup/restore publication is a MANDATORY capability (`release_policy.py`): it
must exist as durable operations with a crash-recoverable, atomic, same-
filesystem publication path, verified here against the production-shaped
fake/Linux gate — with the physical BC250 round trip still reserved for C4.

## 1. D1 — Archive format is a versioned, digest-pinned tar container

A backup is ONE regular file: a `tar` archive (Python `tarfile`, maintained
stdlib) with a fixed layout:

```
backup-manifest.json     # the P8 BackupManifest, canonical bytes
<contained relative paths...>
```

- `format_version = 1` is recorded in the manifest and the `backup_sets` row.
- The manifest is the first member and is the authority for containment,
  per-file digests, and identity. Restore re-derives and compares digests.
- Archive filenames are content/identity labels, never overwritten (no-
  replace publication); a collision refuses rather than clobbers.

## 2. D2 — Authenticated encryption is a designed extension, fail-closed until a reviewed dependency exists

`encryption_mode` is a first-class manifest + `backup_sets` field. For this
build the ONLY supported mode is `none`: the archive is a digest-verified tar
of NON-SECRET data (secrets are excluded by construction — the gateway secret
lives in a 0600 file and never in the database, so it is never archived).

The approved maintained mechanism for a future `aes-256-gcm` mode is fixed now
(no custom crypto, §1.3): AES-256-GCM from the maintained `cryptography`
package, key derived from the passphrase with scrypt (salt + N/r/p recorded in
the manifest header, never the passphrase/key). The passphrase is read into
memory only for the duration of the operation and never persisted, logged, or
placed in argv. Until the `cryptography` dependency is reviewed and added
(a C3 supply-chain decision), requesting encryption refuses fail-closed with an
honest `ENCRYPTION_UNAVAILABLE` reason — visibly unavailable, never silently
downgraded to plaintext.

## 3. D3 — Model/runtime bytes are excluded by default, referenced otherwise

Default backups exclude model and runtime bytes (they are large and
re-acquirable). The manifest records model artifact metadata + aliases with
`model_bytes_included = False` and their digests, so a restore can re-verify or
re-acquire them. `--include-models` / `--include-runtime` opt in explicitly and
are bounded by the request's size/count policy.

## 4. D4 — SQLite snapshot uses the maintained backup API

A consistent database snapshot is taken with `sqlite3.Connection.backup()`
(maintained, WAL-safe), never by copying live files. This is the one mechanism
for a hot-consistent snapshot under concurrent readers.

## 5. D5 — Publication is one atomic same-filesystem exchange

Restore publication swaps the ACTIVE profile directory and a fully validated
STAGED candidate directory with ONE atomic exchange, following the runtime
exchange precedent (`runtime_exchange_helper.py`): a fixed, digest-verified
helper, typed argv (no shell), expected path identities, same-filesystem
requirement, and a stable "unsupported" refusal. There is NO cross-filesystem
"copy then replace" path: if the staged candidate is not on the same device as
the active profile, the operation refuses BEFORE any mutation.

## 6. D6 — Profile-exclusive publication barrier and quiescence

Before exchange, restore acquires a profile-exclusive publication barrier lease
and proves no active operation/worker/service can write the profile. Composed
services are stopped/quiesced through their typed controllers (server, gateway,
worker host). The barrier is held across exchange + verification and released
only on a terminal decision.

## 7. D7 — Verification is a chain, and failure exchanges back

Post-exchange, the new database is reopened and verified: schema version,
runtime identity, model identity, handoff/start receipt, health, and a bounded
inference probe. On success the prior profile is retained for rollback
retention. On verification failure the exchange is reversed atomically and the
prior profile re-verified. If any identity/publication state is uncertain, the
operation enters `RECOVERY_REQUIRED`, retains BOTH profiles, holds the barrier,
and emits exact remediation data.

## 8. D8 — Recovery and repair are evidence-driven

The Repair Center gains precondition-gated, idempotent, revision-fenced actions:
resume staged restore, verify candidate/prior profile, complete publish,
exchange back, acknowledge-and-retain-both, and clean an abandoned candidate
ONLY after proof it is neither active nor known-good.

## 9. D9 — Retention and secure cleanup

Partials and prior profiles are retained under labeled, operation-owned 0700
directories for a bounded retention window; cleanup never deletes anything that
is active, known-good, or uncertain. Secure cleanup of sensitive temporaries
overwrites before unlink.

## 10. D10 — Unsupported filesystems and topologies refuse

Cross-device publication, network filesystems that cannot exchange atomically,
and destinations inside a protected active/staging tree are all refused before
mutation with stable codes.

## 11. Hardware evidence required for release

The fake-world + Linux crash matrix proves the mechanism. The physical BC250
full round trip (create → restore → post-restore inference) and the live
publication under real systemd/gateway/worker remain C4 evidence and are NOT
substituted by these tests.

## 12. Consequences

- Backup/restore become durable operations in the ONE frozen registry, driven by
  the shared engine factory; they survive process death and lease takeover.
- Migration 010 adds `backup_sets` and `restore_attempts` (labels + digests +
  states only — never passphrases, raw keys, private paths, prompts, or full
  archive listings).
- The atomic same-filesystem exchange is the only publication path; anything
  that cannot exchange atomically refuses.
- Authenticated encryption is designed and fail-closed, pending a reviewed
  crypto dependency (C3).


## 2026-09-04 corrective amendment (RV-2/RV-3)

The [restore preservation contract](../restore-preservation-contract.md) is the
current replacement/preservation matrix. Database-only archives do not claim
model/runtime inclusion. Actual held archive bytes are inspected for every
explicit verify/restore, with exact member/digest/size bounds. The atomic
whole-profile swap now stages current local assets and preserves operational,
runtime, credential and thermal authority. An external advisory lock and
identity-bound intent receipts survive exchange and process death. A verified
reverse exchange is required before reporting rollback. Physical C4 evidence
for the corrected candidate is pending; older green counts do not qualify it.
