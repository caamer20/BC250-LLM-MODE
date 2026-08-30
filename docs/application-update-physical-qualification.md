# EXP-6 physical application update and rollback qualification

This worksheet collects candidate-bound evidence for one exact, evaluator-
eligible signed BC250 LLM MODE release. Adding or editing this document records
no physical result. **Status: PENDING — no eligible signed production release,
reviewed production trust root, or Bazzite/CachyOS round-trip evidence is
present in this repository.**

Run the complete worksheet independently on both advertised native profiles:

- Bazzite in its normal local KDE desktop session;
- CachyOS in its normal local KDE desktop session.

Record the candidate's 40-character commit, protected tag, wheel/sdist/SBOM/
inventory/manifest/release-set digests, signature trust-root ID, installed
wheel digest, host-platform observation, kernel/filesystem identity, starting
and target database schemas, UTC start/end time, and evidence issuer. Do not
record credentials, nonce values, user paths, hostnames, addresses, prompts,
completions, raw logs, or exception text. Evidence for one commit, artifact
set, platform, or trust root does not qualify another.

## Preconditions

- [ ] The release evaluator reports final eligibility with no blocking codes,
      and the decision, manifest, inventory, checksums, SBOM, provenance,
      evidence, compatibility document, plain-text notes, and detached
      signature all bind the exact release set.
- [ ] The reviewed production trust root is package-owned and its identifier
      matches the release envelope. Developer fixture trust is absent.
- [ ] A verified profile backup is created and independently restorable before
      update testing. A separate recovery channel remains available.
- [ ] `current` names the verified starting slot, `previous` is observed, the
      desktop launcher resolves `current`, next boot is `graphical.target`, and
      model auto-start is disabled.
- [ ] The thermal watchdog is nominal and no model, Open WebUI, sharing, or
      Tailscale process is started by update smoke or post-update mode.

## Signed check, preview, and offline import

1. Run `bc250-llm-mode update status`; record only its stable outcome/code and
   redacted installation identities. Confirm normal GUI refresh makes no
   channel request.
2. Explicitly run `update check` and preview the exact version in both CLI and
   the one-window **Maintenance · Updates** page. Confirm commit/ref/digests,
   sizes, platform/schema range, backup/restart/rollback plan, expiry, and
   literal plain-text notes agree. Confirm Markdown, HTML, terminal escapes,
   images, and links are not interpreted.
3. Import the same signed release through offline transport. Confirm online and
   offline verification produce the same release-set identity. On disposable
   copies, independently mutate repository/ref/decision/manifest/inventory/
   checksum/SBOM/provenance/evidence/compatibility/signature/notes and confirm a
   stable refusal before staging or execution.
4. Exercise traversal, absolute and non-normalized names, Unicode/case-folded
   duplicates, duplicate JSON keys, non-canonical JSON, symlink/hardlink,
   device/FIFO/socket/sparse members, unknown extras, count/size expansion, and
   source mutation. Confirm no partial published bundle or unbounded host-RAM
   allocation remains.

## Update and restart acknowledgment

1. Apply only the fresh preview/confirmation pair. Prove a changed release,
   installation revision, pointer generation, free-space observation, recovery
   barrier, or expired preview refuses before effect.
2. Confirm the candidate is installed offline into an operation-owned sibling
   venv, `pip check` and mandatory imports pass, a throwaway schema initializes,
   and the running venv remains byte-identical before pointer publication.
3. Verify the exact profile backup identity/digest/source schema is checkpointed
   before the pointer critical section.
4. Interrupt after `previous` replacement and before `current` replacement.
   After lease expiry, resume and prove the prepared pointer state converges to
   one exact candidate/current and prior/previous pair without duplicate install
   or lost working slot.
5. Interrupt before/after every release, staging, backup, pointer, replacement-
   process acknowledgment, database migration, health, and final-record
   checkpoint. Prove takeover uses actual fingerprints/receipts, stale workers
   are fenced, and every external effect occurs once.
6. Confirm the replacement process runs from the new `current` slot, binds its
   operation/release/nonce-digest/target schema, runs integrity, foreign-key,
   composition, snapshot, model-library, and platform probes, and starts no
   managed service or model. The raw nonce must be absent from SQLite, receipts,
   logs, process output, support bundles, and retained evidence.
7. Close and relaunch from the desktop menu and terminal. Confirm the stable
   launcher selects the new slot, duplicate GUI launch activates the existing
   window, and Home/Models/Chat/Connections/Profiles/Maintenance/System remain
   usable without an LLM starting automatically.

## Exact rollback and profile restore

1. Preview rollback and confirm the prior slot still verifies, can read the
   current schema or has the exact restorable pre-update backup, and is the
   recorded `previous` identity. A symlink alone is not evidence.
2. Apply the fresh rollback confirmation. Confirm a new durable
   `APPLICATION_UPDATE/ROLLBACK` operation creates/re-verifies backup evidence,
   publishes the prior slot, obtains a bounded acknowledgment from it, verifies
   application health, and records the new generation.
3. For a schema-changing candidate, force post-migration health failure. Verify
   exact pointer restoration and `BACKUP_RESTORE v1` of the bound pre-update
   profile. Confirm the prior application reads the restored profile.
4. Interrupt pointer rollback and profile restore separately. Any ambiguous
   pointer/profile/receipt state must end `RECOVERY_REQUIRED`, retain both app
   slots plus backup, and start nothing automatically.
5. Run `update cleanup --dry-run`. Confirm current, previous, the sole schema-
   readable app, pending/recovery identities, verified backup, acknowledgments,
   and unverified legacy installation are excluded. Only explicitly eligible
   unreferenced quarantine is listed; dry run deletes nothing.

## Resource, desktop, and privacy closeout

- [ ] Record idle and active GUI RSS, CPU, threads, file descriptors, sockets,
      task-queue depth, visible list counts, and refresh cadence. Confirm no
      update thread, timer, network check, tray, daemon, or watcher exists.
- [ ] Run a mixed eight-hour GUI/chat/API/Maintenance soak after update and
      after rollback. Record bounded metrics and stable operation codes only.
- [ ] Reboot after update and rollback. Each boot returns to the normal desktop
      with no LLM running; explicit model start still works afterward.
- [ ] Generate a local support bundle and scan SQLite, receipts, logs, process
      argv/output, notification records, and support output for secret/path/
      hostname/address/note canaries. Upload nothing automatically.
- [ ] Attach verified evidence through `release/EVIDENCE_HANDOFF.md`. The sole
      release evaluator—not this checklist—decides eligibility.

Until every item passes on both host profiles for the exact candidate and
artifacts, EXP-6 physical qualification remains **PENDING** and application
self-update must remain honestly unavailable in production.
