# EXP-5 physical repair, cleanup, and Undo qualification

This checklist collects candidate-bound evidence on a real AMD BC-250. It is
not a developer-test substitute, does not qualify a different commit, and does
not grant release eligibility. No physical result has been recorded by adding
this document.

Run the complete matrix on both advertised native host profiles:

- Bazzite in its normal local KDE desktop session;
- CachyOS in its normal local KDE desktop session.

Record the exact 40-character commit, installed wheel SHA-256, host-platform
observation, kernel, Mesa/RADV/Vulkan identity, filesystem type, desktop
session type, and UTC start/end time. Start from a verified profile backup and
keep a second recovery channel available. Never retain credentials, hostnames,
addresses, model paths, prompts, completions, exception text, or raw logs in
the evidence record.

## Clean-wheel and one-window preflight

1. Build once from the exact clean candidate, hash the wheel, install that
   wheel into a fresh user-local virtual environment, and run `pip check`.
2. Launch from the desktop menu and from `bc250-llm-mode repair`. Confirm the
   existing window activates, no second Tk root appears, and the
   **Maintenance · Repair** page contains Repairs, Storage cleanup, and Undo.
3. Confirm the disclaimer was previously acknowledged, normal next boot is
   still `graphical.target`, and the model service remains disabled for boot.
4. Capture idle/active GUI RSS, CPU, threads, file descriptors, and sockets.
   Confirm the page creates no watcher, tray process, sensor loop, or network
   request while idle.

## Typed repair matrix

For every action that is naturally available, save only its stable action ID,
preview digest, closed precondition/result/probe codes, owner operation ID,
and `prior_state_survives` flag. Do not preserve the confirmation token.

1. Preview an unavailable repair and prove confirmation cannot bypass its
   precondition.
2. Change one observed revision after preview and prove `PREVIEW_STALE` occurs
   before any effect.
3. Regenerate a deliberately stale runtime handoff and verify its active
   runtime fingerprint/revision.
4. Interrupt a recoverable durable operation, wait past its lease TTL, and
   recover it through the Repair page. Prove a current lease or a
   `RECOVERY_REQUIRED` barrier cannot be stolen.
5. With a verified known-good runtime/model/config lineage, exercise the named
   rollback workflow and bounded inference probe. Confirm the prior tree is
   retained and history is not rewritten.
6. Rotate one disposable client credential, save the new key outside the
   evidence record, and prove the old generation follows the configured
   overlap. Revoke the disposable client and prove other clients continue to
   work. Confirm the one-time key disappears from the GUI after 30 seconds and
   is absent from SQLite, logs, support output, and process argv.
7. Enable only a disposable safe tailnet publication, invoke **Disable unsafe
   sharing**, and verify Funnel/raw llama exposure is absent while local model
   state survives.
8. Exercise invalid managed-model quarantine with a disposable model alias.
   Verify active and known-good model identities are refused and external
   model paths are never moved.
9. Rebuild app-owned launcher/service files and verify their identity without
   enabling model start at boot.
10. Invoke **Return to desktop**. Verify the current model can be stopped,
    next boot remains graphical, sleep/display-manager state is restored, and
    models/profile history remain. Bazzite may remove only the app-owned
    kernel argument; CachyOS must report external boot arguments without
    editing them.

Database-corrupt/newer-schema cases must be exercised against disposable
profile copies. The app must offer verified backup inspection/restore or a
compatible signed update path—never reset, delete, or silently recreate the
database.

## Cleanup death, restore, and purge matrix

Run the focused installed-candidate cleanup integration test on each Linux
host so the real `renameat2(RENAME_NOREPLACE)` path—not the non-Linux developer
fallback—is exercised. Also complete the visible journey below on disposable
app-owned staging from a terminal durable operation:

1. Run `bc250-llm-mode storage cleanup --dry-run`. Confirm only proven
   terminal-operation staging is default-selected. Active/queued/paused/
   recovery-required work, symlinks, mount crossings, special files, external
   models, active/known-good/runtime/backup/application/profile/credential/
   conversation/log data must be absent.
2. Apply the exact unexpired preview token. Interrupt at each protocol
   boundary, including after one no-replace rename but before its receipt and
   between two target effects. After lease expiry, resume with a fresh worker.
   Confirm one destination per identity, one verified receipt, no duplicate
   effect, and either `SUCCEEDED` or honest `RECOVERY_REQUIRED`.
3. Confirm the quarantined item produces one Undo candidate with a seven-day
   deadline. Preview and run it. Verify a new child `STORAGE_CLEANUP/RESTORE`
   operation, exact bytes at the original empty staging identity, a RESTORED
   receipt, and unchanged source history.
4. Quarantine another disposable item and advance/use a controlled clock past
   retention. Confirm Undo disappears and direct preview returns
   `UNDO_EXPIRED`. Explicitly preview PURGE, type its exact token, and verify
   absence plus post-effect free space within the documented tolerance.
5. Force one partial-delete failure on disposable bytes. Confirm the operation
   and both resource leases stop at `RECOVERY_REQUIRED`; it must never claim
   `FAILED_SAFE` or free the recovery barrier.
6. Enqueue a competing restore/cleanup child or change a lease generation
   after Undo preview. Confirm `UNDO_SUPERSEDED`, `LEASE_HELD`, or
   `PREVIEW_STALE` occurs before mutation.

## Local support and privacy

1. Run a deliberately refused and a recovery-required repair. Confirm the
   handoff contains only stable action/result/probe/operation IDs, the prior
   state flag, and bounded argv from the closed table.
2. Explicitly build the local support bundle, verify every manifest digest,
   and confirm the application performs no upload.
3. Seed distinct privacy canaries shaped like an HF token, bearer header,
   hostname/address, model path, prompt, completion, and exception message.
   Inspect SQLite, receipts, GUI details, CLI JSON, logs, process argv, and the
   support bundle. Every canary must be absent.
4. Run equivalent CLI and GUI previews for repair, cleanup, and Undo. Confirm
   matching stable identities, revisions, digests, outcomes, and probes.

## Pass boundary

PASS requires the full matrix on both host profiles, exact candidate/artifact
binding, stable resource measurements, all negative cases, privacy inspection,
and independent reviewer sign-off. Until those records exist, report EXP-5 as
**developer-qualified; physical evidence pending**. Any package-code change
invalidates the candidate-bound results and requires recollection.
