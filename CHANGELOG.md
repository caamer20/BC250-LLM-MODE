# Changelog

All notable changes to BC250 LLM MODE. Format follows Keep a Changelog;
versions are tagged in git.

## [0.9.0.dev0] — unreleased development line

### End-user friendliness remediation

- Added the EUF-0 through EUF-10 implementation plan and ADR 014, freezing
  layered process/protocol/journey readiness, packaged current-boot gateway
  ownership, transactional Open WebUI convergence, and exact-candidate
  qualification without changing the tailnet-only or no-autostart boundaries.
- Fixed the real gateway socket adapter to pass SSE through as
  `text/event-stream` instead of buffering it as JSON. Buffered and streaming
  calls now share authorization, bounds, backend-identity, rate, concurrency,
  audit, and release logic; credentials are no longer forwarded to llama.cpp.
- Added live-socket evidence for first-event delivery before upstream
  completion, terminal `[DONE]`, exact slot release, and content-free audit,
  plus oversized-response failure coverage.
- Added the EUF-1 query-only appliance readiness projection with the closed
  `ABSENT`/`STOPPED`/`STARTING`/`READY`/`DEGRADED`/`BLOCKED`/`UNKNOWN`
  vocabulary. Model, gateway, Open WebUI, Tailscale, Serve, and client status
  now expose process, protocol, and journey truth separately with bounded
  freshness and dependency-identity invalidation.
- Migrated Home, Connections, System, Maintenance checks, Doctor, CLI status,
  and support-bundle metadata to consume the shared readiness projection while
  retaining the prior snapshot fields for compatibility. A running container
  with dead HTTP, a gateway credential without a listener, a mismatched model,
  or stale client verification can no longer render as ready; optional remote
  integrations do not block native Chat.
- Added the EUF-2 package-owned gateway runtime and generated systemd service.
  It resolves and identity-binds the dedicated Podman bridge, listens only on
  loopback plus that RFC1918 bridge address, uses the named-client store,
  verifies the selected backend model, remains disabled at boot, and stops
  after its last explicit current-boot consumer releases it.
- Added `gateway service plan|install|status|start|stop|restart|remove`, exact
  byte-identity migration for the 2026-09-01 live-repair unit/runner, hardened
  unit generation with stable current/installed-slot launch paths, unfamiliar
  unit/listener refusal, and uninstall preservation of models, credentials,
  and Open WebUI data.
- Replaced the destructive Open WebUI stop/remove/recreate path with one typed
  v0.11.1/amd64 container specification and candidate/rollback transaction.
  It verifies the existing data mount before pull, creates a stopped bounded
  candidate, tolerates a post-exit `conmon` stop error only after observing the
  exited state, retains the old container until HTTP/provider/model/SSE checks
  pass, and restores its exact name/running state on failure without ever
  removing a volume.
- Added an app-owned Open WebUI secret-key file, mode-0600 client credential
  mount, private SELinux relabels, numeric file-size limits, no-autostart
  policy, digest/architecture verification, and redacted status fields. The
  pinned provider adapter uses Open WebUI's awaited Config API over stdin,
  preserves unrelated providers, replaces only the app-owned key, and verifies
  the selected model plus a real streamed completion ending in `[DONE]`.
- Added the durable `INTEGRATION_SETUP v1` connection assistant with the fixed
  model → gateway → Open WebUI → private Serve → client-verification order.
  It snapshots pre-effect state, owns a shared exclusion set, recovers an
  interrupted credential publication by exact file identity, and compensates
  only clients and current-boot services created by that operation.
- Connections now begins with Open WebUI, phone/tablet, desktop app, or
  developer intent; creates separate named credentials; runs required blocked
  and authorized models/SSE probes; shows the new external key only once; and
  never labels a blocked or rolled-back outcome as success. The legacy shared
  key can be retired only after both the named Open WebUI and an external
  replacement have independent positive evidence and an exact typed
  confirmation.
- Persisted a bounded, redacted Open WebUI convergence receipt bound to the
  image, provider adapter, and selected model. Status immediately invalidates
  provider/model/stream readiness when any of those identities changes.
- Added the EUF-5 closed problem-detail catalog for readiness, authentication,
  authorization, compatibility, Open WebUI, network, streaming, and recovery
  failures. Connections consumes the same fixed user messages and never paints
  a blocked durable outcome as success.
- Gateway errors are now OpenAI-shaped, redacted, and correlated by a bounded
  `X-Request-ID`. Missing/invalid keys return 401 with `WWW-Authenticate`,
  missing named-client scope returns 403, recognized unsupported inference
  probes return authenticated 404 `ENDPOINT_UNSUPPORTED`, and arbitrary
  management paths remain unproxied 403 denials. Backend exception text is no
  longer reflected to clients.
- Reduced the persistent navigation to five choices: Home, Models, Chat,
  Connections, and More. More keeps Activity, Maintenance, System, Settings,
  and Help in the same window, while Profiles remains available as an advanced
  destination and every prior route remains reachable by deep link and the
  bounded command palette.
- Refocused Home on exactly five outcome cards—Model, Chat, Connections,
  Safety, and Maintenance—while preserving the existing safety/recovery-first
  primary-action policy and contextual links to detailed owners.
- Added one pure EUF-7 model recommendation policy shared by the native Model
  Library and catalog recommendation surfaces. It ranks only standard layout,
  immutable installed identity, selected-workload fit, curated support and
  architecture compatibility, current inference, fresh local measurement, and
  installed state; preview, unsafe-layout, unidentified installed, and no-fit
  entries cannot receive the recommendation label.
- Model details now translate fit to Comfortable/Tight/Does not fit, keep
  quantization and selected context/slots in Details, show local speed or
  temperature only from fresh recorded evidence, otherwise say Not measured,
  and identify provenance/identity evidence. Actions are Install, Start and
  Chat, Switch and Chat, Open Chat, Resolve recovery, or View why it cannot
  start; successful install/start/switch opens native Chat.

### Catalog, WebUI, and desktop lifecycle

- Fixed **System → Enter LLM Mode** so it explicitly confirms, closes the
  graphical desktop, and opens the full-screen tty1 text login through a
  transition that survives display-session shutdown. State is committed
  before the transition, the next boot remains graphical, and persistent
  administrator no-sleep masks are preserved. GUI shutdown now observes the
  real display-manager state so a stale LLM-session record cannot leave a
  model running while the graphical desktop is active. The transition stops
  only the display manager so an explicitly running model and independent
  network services remain active.
- Updated the immutable Linux/AMD64 Open WebUI pin from v0.6.14 to v0.11.1;
  the managed updater still verifies the exact OCI manifest before replacing a
  container and preserves its existing data storage and prior running state.
  `bc250-llm-mode openwebui update` now exposes the same safe updater as the
  System-page button.
- Hid the two conversion-only source entries from Models, first-run setup,
  search, recommendations, and direct CLI installation while the build has no
  pinned verified converter. Existing already-converted local GGUFs remain
  discoverable and usable.
- Added a durable Models-page installation panel with the current model and
  phase, byte totals, percentage, progress bar, terminal result, and a direct
  link to Activity for full operation details.
- Expanded the curated model catalog from 24 to 40 entries across 360M–16B,
  including ultra-small, mature, current, multilingual, reasoning, coding,
  Gemma 3, and MoE options. New entries remain Preview pending physical
  BC-250 Vulkan qualification.
- Replaced wildcard paths on every direct catalog download with exact verified
  GGUF filenames, corrected two stale Qwen repositories, and admitted the
  standard `lfm2` GGUF architecture used by the existing LFM2.5 entries.
- Added a System action that applies the app-approved digest-pinned Open WebUI
  image while preserving its verified named-volume or bounded legacy-bind data,
  gateway credential boundary, and prior running/stopped state.
- Closing the GUI in Desktop mode now stops and verifies the model service
  before exit. Minimize, navigation, and explicit current-boot LLM Mode serving
  remain unchanged.
- The Models table now has an explicit keyboard contract: Up/Down moves the
  visible highlight and Enter invokes that row's same safe primary action,
  including transactional start/switch for an installed model.

### Acquisition transport fixes

- Follow bounded HTTPS redirects while resolving immutable Hugging Face
  revisions and downloading model bodies. Cross-origin redirects permanently
  strip authorization for that chain while preserving safe range-resume
  headers; unsafe targets, missing locations, and redirect loops still fail
  closed.
- Updated the Qwen3.8 9B catalog entry and documentation to Empero's canonical
  `Qwen3.8-9B-Distill-GGUF` and `Qwen3.8-9B-Distill` repositories.
- Recognize the standard `qwen35` GGUF architecture used by Ornith and other
  Qwen3.5-family models instead of quarantining those artifacts as unknown.
- Renew operation leases during every long managed publication, quarantine,
  validation, and recovery hash pass so multi-gigabyte files cannot outlive
  the foreground worker's lease while they are still making progress.

### Activation reliability fixes

- Heartbeat operation leases during slow model start, health, and inference
  verification so a valid long BC-250 load cannot be mistaken for an abandoned
  activation and taken over by another worker.
- Publish context per slot and aggregate context as distinct health fields, and
  verify activation against context-per-slot semantics while retaining the
  bounded legacy aggregate-health compatibility path.

### Appliance experience EXP-7 and EXP-8 documentation

- Centralized stable user-facing result copy with safe unknown-code and
  exception fallbacks, and added a bundled 22-term offline glossary. Help,
  command-palette, glossary, and icon use remains local and performs no network
  fetch.
- Added a bounded local `Ctrl+K` command palette, query-only Privacy Center,
  locale-ready display formatting, persistent 100–200% interface scaling,
  stronger keyboard/focus behavior, selected-row text alternatives, and
  explicit accessibility limitations. Protected actions still require their
  normal preview and confirmation; the palette cannot execute them.
- Added separate end-user and operator guides covering source installation,
  desktop-menu launch, first run, exact remote endpoints, profiles,
  Maintenance/Repair/Updates, offline bundles, privacy, durable recovery,
  uninstall/reinstall, and model preservation.
- Added one candidate-bound EXP-8 worksheet freezing all 14 end-to-end
  journeys in Bazzite/CachyOS fresh/upgraded cells, five participant roles,
  exact usability/safety/privacy capture fields, GUI resource thresholds, and
  release-evidence routing. Every external result is explicitly PENDING.
- Developer accessibility, privacy, terminology, scale, and bounded-resource
  gates pass. EXP-8 physical journeys, participant acceptance, Bazzite/CachyOS
  resource measurements, security review, and release evidence remain pending;
  this documentation does not claim them.

### Appliance experience EXP-6

- Added ADR 013's fail-closed application release verifier and migration 014
  two-slot installation/import state. Candidate identity is evaluator-,
  manifest-, inventory-, checksum-, SBOM-, provenance-, signature-, platform-,
  and database-compatibility-bound; production remains honestly unavailable
  until a reviewed trust root and eligible signed release exist.
- Added isolated exact-wheel staging, digest-pinned `current`/`previous`
  publication, durable `APPLICATION_UPDATE v1` crash recovery, verified profile
  backup, bounded replacement-process acknowledgment, health verification, and
  exact rollback evidence. No smoke path starts a model or managed component.
- Added a hostile-archive-safe offline importer using the identical verifier,
  plus one-window Updates UI and `update status/check/preview/import-bundle/
  apply/rollback/cleanup --dry-run` CLI parity. Checks and mutations are never
  automatic; signed notes render as literal text and the prior readable slot is
  retention-protected.
- Developer tamper/crash/GUI/CLI tests are included. Eligible signed artifacts
  and physical Bazzite/CachyOS update, migration, rollback, restart, and profile-
  restore evidence remain release/owner-gated and are not claimed.

### Appliance experience EXP-5

- Added ADR 012 and a closed 15-action Repair contract. Every action now has a
  typed owner, bounded preconditions/evidence, exact revision-bound preview,
  confirmation token, success probe, closed result code, and explicit
  reversibility/prior-state semantics; route strings and arbitrary commands
  are never executable.
- Added durable `STORAGE_CLEANUP v1` to the one operation registry without a
  schema change. It holds `model-storage` and `storage-cleanup` in sorted order,
  quarantines only verified terminal-operation staging by no-replace rename,
  restores exact retained identities, and permits explicit purge only after a
  seven-day receipt deadline. Partial permanent deletion becomes
  `RECOVERY_REQUIRED` and retains the lease barrier.
- Added evidence-derived Undo for cleanup quarantine only. Undo revalidates the
  receipt/tree/deadline/source revision/child lineage/lease generations and
  creates a child RESTORE operation; expiry, supersession, or stale preview
  refuses before mutation. There is no generic inverse or history rewrite.
- Added a real one-window Maintenance · Repair page with Repairs, Storage
  cleanup, and Undo tabs, plus matching `repair`, `storage cleanup`, and `undo`
  CLI groups. Opening Repair no longer resets setup.
- Added privacy-safe support handoff records containing stable IDs,
  prior-state survival, local-bundle availability, and bounded closed-table
  argv. Credential bytes remain ephemeral and never enter JSON or support
  output; the application never uploads a support bundle.
- Added deterministic death/reclaim, partial-multi-target, cancellation,
  exclusion, preview, expiry/supersession, CLI/GUI parity, and privacy tests.
  Physical Bazzite/CachyOS interruption, rollback, credential, sharing,
  desktop-return, quarantine/restore/purge, and local-support qualification is
  explicitly evidence pending.

### Appliance experience EXP-3 and EXP-4

- Added named built-in/custom workload profiles, exact preview fingerprints,
  durable apply/rollback, a query-only evidence coach, fixed-prompt bounded
  calibration, and stop-only idle policy.
- Added migration 013 notification preferences and bounded redacted receipts,
  disabled by default with atomic master/category CAS updates.
- Added the one-window Maintenance inbox with at most five items in the closed
  safety → recovery → security → integrity → storage → backup → operation →
  update → information order. Normal refresh is query-only and labels live,
  cached, stale, and not-checked evidence.
- Added explicit full checks, cleanup preview, Home surfacing, and CLI parity.
  Fixed-copy `notify-send` delivery is connected only after durable operation,
  thermal-latch, or explicit maintenance-check commits; there is no tray,
  notification thread, duplicate sensor loop, or automatic repair/cleanup.
- Physical Bazzite/CachyOS workload and KDE-notification qualification remains
  candidate-bound evidence pending and is not inferred from developer tests.

### Appliance experience EXP-1 and EXP-2

- Added an atomic user-local application-menu launcher and bounded same-user
  single-instance activation broker; no autostart, tray daemon, or listener
  thread was introduced.
- Added migration 011 named client credentials with case-insensitive bounded
  labels, closed scopes/kinds, revision fencing, independent revoke/rotation,
  optional 1–900 second overlap, a global disable record, and preservation of
  the legacy singleton. Secret bytes remain in separate non-user-named 0600
  files and never enter SQLite.
- Added one authoritative Connection Assistant snapshot, locally versioned
  Open WebUI/PocketPal/OpenAI/curl/Python/SSE cards, exact `:8443/` and
  `:10000/v1` URLs, safe observed model aliases, mandatory positive/negative
  probes, a bounded SSE probe, named gateway principals/scopes, and redacted
  last-used endpoint-class receipts.
- Added a primary native Connections page and matching `connections` CLI for
  status, listing, add/rotate/revoke/disable, instructions, and tests. One-time
  key reveal is TTY-gated in CLI and time-limited/cleared in GUI; emergency
  disable does not depend on llama.cpp health.
- Developer protocol fixtures include a real-socket two-client journey and
  independent revocation. Physical PocketPal, Open WebUI, generic SSE,
  Bazzite, and CachyOS qualification remains evidence pending.

### Unified native appliance window (GUI-1 through GUI-8)

- Replaced the transitional 11-screen/dashboard/second-window experience with
  one lightweight tkinter window: five setup chapters, task-oriented Home,
  one Model Library, native bounded streaming Chat, embedded Activity,
  System, Settings, Help, and a hidden-until-requested log/confirmation drawer.
- Added one refresh coordinator, exactly three lazy bounded task lanes,
  generation-fenced results, bounded events/lists/transcripts/logs, minimized
  backoff, keyboard navigation, focus handling, appearance preferences, and
  reduced-motion behavior.
- Preserved canonical setup stages, the exact safety acknowledgment, durable
  operation ownership, current-boot LLM Mode, desktop-next-boot policy,
  standard-layout/fit gates, thermal barriers, platform capability routing,
  shared native/terminal chat classifications, and privacy bounds.
- Deleted the legacy dashboard and setup mixin hierarchy. `Wizard` remains a
  deprecated import alias for `ApplicationWindow`; it is not a second UI.
- Physical Bazzite and CachyOS UX/resource qualification for the resulting
  package candidate remains evidence pending and is never inferred from
  deterministic tests.
### CachyOS and capability-driven Linux hosts

- Added a bounded, read-only host-platform authority with native Bazzite and
  CachyOS profiles, Arch/Fedora/Debian/SUSE compatibility tiers, systemd and
  boot-manager detection, fixed package plans, and pre-composition
  `platform status|plan` JSON diagnostics.
- Added the CachyOS Tkinter bootstrap (`pacman -S --needed tk`) with a
  fail-closed pending-upgrade preflight. The app never runs `pacman -Sy` or an
  automatic full upgrade; Podman/Distrobox/RADV dependencies remain a
  previewed operator plan, while the existing Fedora Distrobox stays the
  controlled inference guest.
- Removed `rpm-ostree` from CachyOS current-boot LLM Mode. Host-mode now uses
  the systemd display-manager alias, keeps graphical/no-autostart reboot
  safety, and reports external systemd-boot/GRUB/Limine/rEFInd kernel
  arguments without editing them.
- Routed CLI, chat, setup, and dashboard host actions through one composed
  service; added platform data to status, doctor, appliance home, and support
  bundles without persisting host observations.
- Capability-gated Cyan clock tuning. Hosts without the Cyan governor disable
  unsafe clock controls while retaining thermal monitoring and the latched
  emergency stop.
- Fixed the pre-GUI bootstrap stage order: a fresh install now records
  hardware validation before Tk readiness instead of raising a setup-stage
  conflict.
- Accepted ADR 007 (`docs/adr/007-capability-driven-linux-hosts.md`). Physical
  CachyOS BC-250 qualification remains evidence-pending and must not be
  inferred from developer tests.

### Release-gate remediation G0–G5 (audit remediation of the C-series delivery)

An independent audit found release-control defects in the original C-series
delivery (boolean eligibility bypasses, an optional source commit, an
incomplete evidence envelope, unverified attestation references, an ignored
`--artifacts` flag, verify-without-comparison, mutable action refs, and an
unverified post-attestation path). The remediation plan
(`RELEASE_GATE_AND_PIPELINE_REMEDIATION_IMPLEMENTATION_PLAN.md`) closed every
finding test-first; the original C-series records remain in git history with
dated reconciliations. Status vocabulary (G5.1): implemented /
developer-qualified / evidence pending / release blocked / published.

- **G1** — `evaluate_release` is the SOLE eligibility authority: mandatory
  `CandidateIdentity` (version + full commit + ref + repository + policy
  digest) + `ArtifactInventory` inputs; `ReleaseState.evidence_satisfied`
  deleted and `may_tag_1_0_0()` is a non-authoritative constant False;
  final-tag rule (a final version must ride exactly `refs/tags/v<version>`);
  policy-digest binding; decision schema v3; gate codes 19 → 21.
- **G2** — evidence schema **v2**: 18 mandatory envelope fields, unknown
  fields refused, pinned validation order, value-based secret refusal under
  any key, bounds, per-kind measurement contracts, supersession set rules;
  Raw/Validated/Verified boundary — only `verify_evidence_attestation`
  promotes a record, and the evaluator consumes only verified records;
  policy content revision 3 (`release/policy-v3.json`).
- **G3** — artifact-bound tooling: inventory schema **v2** with roles;
  decision-derived release manifest schema **v3** (blocked drafts, final
  refusal, canonical digest); evaluator subject↔inventory binding; CLI
  `evaluate` requires `--artifacts` + `--level`, `verify` performs FULL
  comparison (inventory equality, checksums, SBOM subject == actual wheel
  digest, manifest digest integrity); SBOM tomllib parsing + duplicate-
  component refusal; gate codes 21 → 23.
- **G4** — every workflow action pinned to a network-verified full 40-char
  SHA (checkout v4.1.1, setup-python v5.6.0, upload-artifact v4.6.2,
  download-artifact v4.3.0, attest-build-provenance v2.4.0) with Dependabot
  managing updates; `release.yml` restructured: build once → verify →
  attest → verify attestations (before approval) → final evaluation (the
  evaluator gates approval AND publish) → approval environment → publish
  (exactly named bundle; refusal is the evaluator's final-level exit, never
  a bypassable shell line). Publication still NOT performed (owner-gated).
- **G5** — documentation truth reconciliation: status vocabulary applied
  across README/ARCHITECTURE/CHANGELOG; evidence README rewritten for schema
  v2 + verification boundary; operator runbook (`release/RUNBOOK.md`);
  release-checkout check gains build-input-scoped blocking + diagnostics-only
  mode; documentation-consistency gate extended (policy snapshot ↔ live
  policy, evidence schema doc, mutable action refs, unqualified completion
  claims).
- **G6** — developer qualification checkpoint + external-gate handoff:
  fresh-worktree qualification at the closeout commit (default suite green,
  slow battery 52/52 incl. the NEW frozen qualification gate
  `tests/test_release_qualification_g6.py`, focused release suite green);
  clean-candidate dry run (build once → inventory v2 → checksums →
  subject-bound SBOM → blocked draft manifest v3 → full verify → evaluator)
  proves the ONLY remaining blockers are the genuine external gates
  (C4 hardware/soak/backup-restore, C5 security review, C6 human acceptance,
  limitation acceptance, C8 approval/signing/publication + real-run evidence
  kinds); external-evidence handoff packet `release/EVIDENCE_HANDOFF.md`
  (roles, procedures, pass criteria, expiry, attestation mechanisms, rerun
  rules — empty checklists only, no fabricated measurements).

Release status after G0–G6 (remediation CLOSED): **release blocked** — the
evaluator truthfully reports `eligible_for_1_0_0 = false` and the remaining
blockers are limited to genuine external gates (hardware qualification +
soak, security review, human acceptance, limitation acceptance, and
publication evidence all remain pending; nothing fabricated). C4/C5/C6/C8
stay hardware/human/owner-gated; the next authorized action is C4 evidence
collection per `release/EVIDENCE_HANDOFF.md`, not a version bump or tag.

### Release closure C7 — known-limitation and conversion decision

- Capability classification refined from the C1 3-value draft to the reviewed
  5-value 1.0 scope vocabulary: `MANDATORY_FOR_1_0`, `SUPPORTED_OPTIONAL`,
  `EXPERIMENTAL`, `DEFERRED_NOT_ADVERTISED`, `REMOVED`. A `REMOVED` capability
  is gone (with migration guidance) and needs no limitation acceptance.
- Model conversion scope decision: `DEFERRED_NOT_ADVERTISED`. No pinned,
  verified converter ships in 1.0, so conversion is outside the 1.0 support
  promise and not advertised in product claims or the primary UI. Direct GGUF
  acquisition and local GGUF import remain the supported model-ingestion paths.
  The reviewed scope-decision record is `release/scope-decision-model-
  conversion.md` (bound to release policy v2 + its digest); the matching
  `KNOWN_LIMITATION_ACCEPTANCE` evidence record remains human/owner-gated and is
  not fabricated.
- `backup-restore-publish` reconciled as IMPLEMENTED by C2: it leaves the
  known-unavailable list, and its remaining 1.0 requirement is physical-hardware
  qualification evidence, tracked by the evidence gate (BACKUP_RESTORE_HARDWARE
  / HARDWARE_QUALIFICATION / SOAK_TEST) and by the manifest blocking gaps — not
  by capability unavailability.
- New unclassified-limitation gate: `may_tag_1_0_0()` can never become true
  while a known limitation lacks a scope classification in the release policy.
- Release policy bumped to content revision 2 (`release/policy-v2.json`;
  `release/policy-v1.json` retained as the C1 historical artifact).
- The release evaluator still truthfully reports `eligible_for_1_0_0 = false`
  (hardware qualification, soak, security review, human acceptance, and
  limitation-acceptance evidence all remain pending); no record is fabricated.

### Release closure C3 — supply-chain, SBOM, and release pipeline

- Deterministic CycloneDX 1.5 SBOM generation + fail-closed validation
  (`tools/release/sbom.py`): covers the package itself, its direct runtime
  dependencies (parsed from pyproject), the build backend, and managed
  third-party / external runtime identities. Reproducible digest (injectable
  serial/timestamp default to fixed values); the subject sha256 binds the built
  artifact. Validation refuses a missing package/required dependency, secret-
  like material, non-normalized paths, and a subject-digest mismatch.
- Hardened artifact inventory (`tools/release/artifacts.py`): identity is the
  content sha256 (never the filename), canonical media type per artifact, and
  rejection of symlinks/special files.
- Least-privilege CI: top-level `permissions: contents: read` so CI and any
  pull-request run never receive publish credentials or OIDC publication
  rights; `actions/checkout` pinned to a verified full-length commit SHA.
- A SEPARATE approval-gated release workflow (`.github/workflows/release.yml`):
  validate-candidate → build-once (wheel/sdist/checksums/SBOM/inventory) →
  verify-artifacts (install the exact wheel from the job artifact, smoke
  CLI/worker, verify checksums + SBOM subject) → attest → approval-environment
  → publish. OIDC `id-token: write` exists only on the attest + publish jobs;
  the publish job NEVER rebuilds (it retrieves the exact previously built
  artifacts and re-verifies digests) and is environment/approval-gated.
- Publication has NOT been performed and remains owner-gated (C8); the publish
  step documents the intended Trusted Publishing path and exits non-zero without
  explicit owner authorization.
- Pending evidence (never fabricated): full-length SHA pinning for the remaining
  actions (requires network verification); live attestation generation +
  verification, artifact signing, and any publication are CI/owner-gated and
  have not been performed.

### Release closure C2 — durable backup create/restore publish

- Backup and restore are now REAL durable, crash-recoverable operations
  (REL-004), not just manifest/dry-run contracts. `BACKUP_CREATE v1` and
  `BACKUP_RESTORE v1` are registered in the single frozen registry (now eight
  workflows) and driven by the shared engine factory, so creation, publication,
  and rollback survive process death and lease takeover.
- ADR 006 `docs/adr/006-durable-backup-restore.md` records the decisions: tar
  container `format_version=1`; encryption `none` only this build
  (`aes-256-gcm` designed but refused fail-closed `ENCRYPTION_UNAVAILABLE`
  until a reviewed crypto dependency is accepted; secrets excluded by
  construction); model/runtime bytes excluded by default; `sqlite3` backup()
  hot snapshot; ONE atomic same-filesystem profile exchange; profile-exclusive
  publication barrier; verification chain with exchange-back and
  RECOVERY_REQUIRED; retention + secure cleanup; unsupported-filesystem refusal.
- Migration 010 `backup-restore-lifecycle` (schema 10) adds `backup_sets` and
  `restore_attempts` with CAS revision-fenced repositories
  (`backup_lifecycle.py`).
- `profile_exchange_helper.py`: digest-pinned `renameat2 RENAME_EXCHANGE`
  helper gated to the profile marker + same-filesystem containment (Linux-only
  real swap; the contract is fake-world tested).
- `operations/backup.py` + `backup_adapter.py`: the two frozen workflows and ONE
  production host. Create: hot-consistent snapshot, secret-free manifest,
  no-replace tar publish (collision refuses), digest verify, fenced record +
  secure staging cleanup. Restore: digest-bound source validation, contained
  sibling staging + forward migration, atomic exchange, post-restore integrity,
  promote retaining the prior profile. The restore carries the operation's
  durable rows across the profile swap so the engine's fenced checkpoint
  protocol continues; a publication death converges to RECOVERY_REQUIRED with
  both profiles retained.
- `backup_command.py` + composition + CLI: `BackupCommandService`
  (create/list/verify + restore inspect/start) composed into the application;
  `bc250 backup create/list/verify` and `bc250 restore inspect/start/status`
  verbs (restore start behind acknowledgment). Restore inspect is a query-only
  dry run returning the confirmation digest that binds the restore.
- Pending evidence (never fabricated): the physical BC250 backup/restore round
  trip + post-restore inference verification and the live Linux renameat2
  publication remain hardware-gated until C4.

### Release closure C1 — evidence-driven release gate v2

- The boolean readiness model is NO LONGER the release authority. Caller-
  supplied approval booleans can never, on their own, qualify a `1.0.0`.
  Eligibility now derives ONLY from validated, candidate-bound evidence.
- `release_policy.py`: versioned release policy — closed evidence-kind and
  gate-code vocabularies, RC vs 1.0 required-evidence sets, and capability
  classification (`backup-restore-publish` MANDATORY; `model-conversion`
  DEFERRED, requiring an accepted-limitation record). Canonical policy digest;
  reviewed snapshot at `release/policy-v1.json`.
- `release_evidence.py`: immutable evidence-record validation, fail-closed —
  unknown kind, non-PASS result, wrong version/commit, expired, superseded,
  duplicated, non-contained attachment path, secret/prompt material, and
  malformed digest are all rejected with stable codes.
- `release_gate.py`: pure, deterministic, fail-closed evaluator
  (`evaluate_release`) returning exact blocking codes; an eligible decision
  cannot be constructed directly. `check_release_checkout` rejects untracked
  build inputs WITHOUT deleting developer files.
- `release_state.py` migrated to manifest schema v2: `may_tag_1_0_0()` requires
  no informational gaps, no unavailable mandatory capability, AND satisfied
  evidence (public constructor leaves evidence unsatisfied).
- Repository-only tooling `tools/release/` (validate/evaluate/manifest/verify)
  plus the read-only documentation-consistency gate (C1.4). Tooling is excluded
  from the runtime package. Evidence rules documented in
  `release/evidence/README.md` — no fabricated samples.
- The C0 red gates are green and folded into the default suite. The evaluator
  truthfully reports the repository as `eligible_for_1_0_0 = false` with exact
  missing-evidence codes; no record is fabricated to turn it green.

### Release state (P9 §15)

- The build is NOT `1.0.0` and will not be tagged until the P9 exit gate is
  met: all milestone gates evidence-linked, hardware qualification + soak
  green on BC250 hardware, human acceptance signed off, and security review
  signed off. `bc250_llm_mode/release_state.py` models this state and keeps
  the known-unavailable capabilities VISIBLE rather than implying
  completeness: **model conversion** (no pinned, verified converter ships in
  this build) and the **backup-restore publish step** (atomic profile-level
  publish + post-restore inference verification require physical hardware).
- Deterministic property/fuzz robustness gates (`tests/test_release_fuzz.py`)
  prove GGUF parsing, operation request validation, backup manifest digest
  verification, and event pagination are fail-closed and bounded.

### Added (P8 §14: backup, restore, repair, and upgrade safety)

- `backup_manifest.py`: versioned, secret-free backup manifest with a stable
  canonical digest; refuses secret-like keys and non-contained paths.
- `backup_restore.py`: fail-closed dry-run restore gate — tampered/partial/
  wrong-key/path-traversal/newer-schema/low-space/permission/identity failures
  all refuse BEFORE any mutation, leaving the current profile untouched.
- `repair_center.py`: read-only repair findings + idempotent, auditable,
  precondition-gated actions for the eight supported repairs (no manual
  SQLite/filesystem edits).
- Upgrade matrix tests prove schema upgrades preserve the database, managed
  artifacts, and runtime lineage (v8 -> v9 with no data loss).

### Added (P7 §13: chat reliability, privacy, and daily-use UX)

- `chat_lifecycle.py`: shared request/result/error semantics for both chat
  clients — request/conversation IDs, bounded deadlines (never `timeout=None`),
  cancellation token, closed terminal classification, pure retry policy (never
  after tokens emitted), redacted event record, recoverable messages with the
  request ID instead of a traceback.
- `conversation_ux.py` + `benchmark_ux.py`: pure presentation contracts for
  conversation UX (model-change indicator, export redaction, confirmations,
  bounded search) and benchmark/tune UX (tested-vs-estimated, attribution,
  bounded retention, "apply winner" as a separate verified operation).
- Privacy exit gate: conversation content never enters operation history,
  logs, metrics, or support bundles by default.

### Added (P6 §12: model library and storage lifecycle v2)

- Model Library read model over the immutable artifact identity (provenance,
  digest, trust, fit, active/known-good references, deletion eligibility);
  migration 009 `model_library_meta` (schema v9).
- Durable `MODEL_REMOVE v1`: quarantines rather than deletes; refuses
  active/known-good/referenced artifacts; survives process death + lease
  takeover.
- `storage_capacity.py`: capacity/dedup report + ranked, report-only cleanup
  suggestions.
- `MODEL_CONVERT v1` gate: known versioned type, but honestly unavailable (no
  converter shipped) and refused before any external effect.

### Added (P5 §11: appliance home, health, and diagnostics)

- `health.py` typed health model (closed readiness vocabulary, fail-closed
  staleness, READY never inferred) + `home.py` home snapshot (ten cards, one
  read unit).
- `doctor.py` read-only diagnostics catching the eight seeded failures;
  `support_bundle.py` redacted-by-construction export; `home_ux.py` pure
  presentation contract.

### Added (P4 §10: authenticated integration boundary)

- Gateway (credential-only, scope/rate/size bounded, loopback), Open WebUI
  digest-pinned container, and sharing routed ONLY through the gateway.

### Added (P3 §9: bounded execution platform)

- One bounded process port + bounded HTTP transport policy; every production
  subprocess/HTTP caller migrated; AST guards against regressions.

## [0.9.0.dev0] — unreleased development line

### Added (P2/U1.5: Activity Center v1)

- Activity Center (`gui/activity.py`) reachable from a dashboard button:
  live operation list with severity-ordered rows, plain-language state
  labels with exact semantics ("Waiting to start", "Paused safely",
  "ATTENTION REQUIRED"…), bounded progress that never renders 100%
  before terminal verification, action buttons derived ONLY from the
  durable `OperationSummary` flags (stop/resume/retry/recover/dismiss),
  "what happened / what is safe / what can I do now" copy, a status
  strip for working/paused/recovery counts plus worker ownership, and a
  redacted copy-support-details control.
- The presentation contract (state labels, severity ordering, progress
  clamp, action plan, support text) is pure and headless-tested over
  every durable state; the widget layer is constructed under stubbed
  tkinter against a REAL composed application and routes actions through
  `operation_commands` only. A static guard forbids sqlite/subprocess/
  repository/engine/worker imports in the GUI module forever.
- Refreshing is coalesced on a bounded timer and never blocks the GUI
  thread; closing the window never cancels work.

## [0.9.0.dev0] — unreleased development line

### Added (P1/U1.4: operation command/query API)

- Typed, versioned operation view models (`operations/views.py`):
  summaries, details, steps, events, leases, wait results, active
  summary — path-label redacted, bounded, JSON-serializable.
- `OperationQueryService`: read-only `list/show/steps/events/leases/
  wait/active_summary` over one read unit each; mandatory pagination
  bounds; stale leases reported truthfully; bounded condition-injectable
  wait (never zero-timeout polling).
- Fenced `OperationCommandService`: cancel (durable intent, no false
  success), resume (paused work re-armed through the shared engine),
  retry (NEW operation from the immutable request with lineage),
  recover (real takeover of expired-lease work behind `--confirm`;
  RECOVERY_REQUIRED barriers refused with kind-specific guidance, exit
  78), dismiss (durable visibility flag via migration 007; audit
  history never deleted), and §7.5 generic detach bound to THE ONE
  worker entry point with an explicit profile argument.
- `bc250-llm-mode operations …` CLI group: list/show/steps/events/wait/
  cancel/resume/retry/recover/dismiss with `--json` schema-versioned
  stdout and stable exit codes (0 ok, 1 refused, 78 recovery gating,
  130 interrupted).
- Migration 007 (`operation-dismissal`) advancing DATABASE_SCHEMA_VERSION
  to 7: `operations.dismissed_at` plus a default-view partial index;
  ordered, atomic, refusal-based like its predecessors.
- `worker_main` writes a bounded startup receipt into the profile logs
  dir so detached handoffs are diagnosable even with stdio cut off.

## [0.9.0.dev0] — unreleased development line

### Added (P0: foundation correction — FINAL_PRODUCTION_READINESS plan)

- Real detached-worker entry point `bc250_llm_mode/worker_main.py`
  (closes DEF-001): strict profile/policy argument parsing, explicit
  `AppPaths` resolution with no import-time home leakage, one
  application composition, and stable exit codes (0 idle-exit, 2 usage,
  3 worker-already-running, 4 repair-required, 5 run-failed,
  130 interrupted). A detached child now provably completes a real
  production operation (MODEL_IMPORT v1) exactly once from a clean
  session-detached process, including from an installed wheel with the
  repository root off `sys.path`.
- Scoped test diagnostics (`tests/support_diagnostics.py`) replacing the
  process-wide `faulthandler.dump_traceback_later(…, exit=True)` watchdog
  (closes DEF-002): diagnostics wrap one block or subprocess wait, dump
  stacks without killing the parent, always cancel, and hard kills apply
  to child process groups only with a structured timeout result.
- Composition hygiene guard: a symtable walk of `app.py` proves every
  referenced name binds in some enclosing scope (caught the latent
  `ThermalStateRepository` NameError on the composed runtime thermal
  barrier).

### Fixed (P0)

- Engine failure classification is exception-safe: a step's
  classification probe that itself raises now classifies that step
  UNCERTAIN so durable compensation still decides, instead of escaping
  `execute_one` and leaving operations RUNNING under live leases.
- `_wire_services` binds `ThermalStateRepository` for the runtime
  thermal barrier (latent NameError on the composed update path).
- Removed dead `json_safe` from `worker_service.py`; `run_worker_main`
  delegates to the single entry in `worker_main.py`.

## [0.9.0.dev0] — unreleased development line

### Added (Session 6B+ / U1.3: explicit worker lifecycle)

- One bounded, profile-scoped `WorkerHost`: survives frontend closure by
  resuming abandoned operations through standard takeover probes (no
  duplicate effects), enforced single-instance via a heartbeated
  `worker_locks` row (migration 006), idle exit after a bounded quiet
  period on injected clocks, bounded restart policy that pauses poisoned
  operations, graceful shutdown checkpoints, and condition-backed bounded
  waiting (never timeout=0 polling).
- `bc250-llm-mode llamacpp update --detach` hands the queued operation to
  exactly one detached worker process (`python -m worker_main`) with an
  honest DETACHED outcome; composition/boot/frontends never auto-start
  workers (hard architecture guards).

## [0.9.0.dev0] — unreleased development line

### Added (Session 6B / U1.2: durable llama.cpp runtime lifecycle)

- Durable `RUNTIME_UPDATE v1` / `RUNTIME_ROLLBACK v1`: immutable ref→commit
  resolution before any mutation, operation-owned bounded builds with
  typed-argv processes (no shell), content-derived build IDs over a
  canonical manifest (source commit + recipe + image/toolchain identity +
  per-binary sha256), one no-gap `renameat2(RENAME_EXCHANGE)` cutover via
  a digest-verified fixed helper (initial installs publish no-replace),
  seven-link live verification (manifest → binary digest → handoff v2 →
  start receipt → new systemd invocation → model/config observation →
  bounded inference) before one generation-CAS promotion that also
  advances known-good identity.
- Rollback selects the repository's current retained target, revalidates
  identities, and toggles lineage so an accidental rollback is itself
  reversible without rebuilding.
- Phase-scoped resource leasing (ADR 002 §17): builds hold only
  `runtime-installation`; `runtime-active` joins at the activation
  boundary through promotion; conflicts refuse/pause before any work;
  recovery barriers retain leases.
- Handoff schema v2 binding configuration to the exact runtime component,
  plus a launcher-published 0600 start receipt; stale receipts and
  swapped binaries refuse startup.
- Migration 005 (schema v5): immutable `runtime_builds`, append-only
  `runtime_build_verifications`, operation-owned `runtime_trees`, and the
  single generation-checked `runtime_component_state` row with
  deterministic legacy backfill.
- `RuntimeLifecycleCommandService`: one composed entry for CLI, wizard,
  dashboard, and setup (`update/rollback/resume/status`); honest
  foreground-only reporting with durable resume.

### Removed (Session 6B / U1.2)

- The synchronous llama.cpp lifecycle: `env.update_llamacpp`,
  `env.rollback_llamacpp`, `record_llamacpp_build`, `llamacpp_status`,
  mutable `llamacpp_history`, the fixed `-staging/-backup/-rolled`
  directory dance, `ComponentLifecycleService.update/rollback`, and the
  direct setup clone/build — deleted with hard architecture guards.
  Setup provisioning never touches llama.cpp; fresh installs obtain their
  first runtime from the durable pinned update.

### Added (Session 6A / U1.1: durable model acquisition & import)

- Durable `MODEL_ACQUIRE v1` / `MODEL_IMPORT v1`: immutable hub revision
  pinning, bounded range-resumable transfer with credential stripping on
  cross-origin redirects, descriptor-stable local import that never
  modifies the source, no-replace content-addressed publication,
  quarantine for invalid candidates (no alias), digest deduplication,
  lease-fenced logical reservations, forward-only recovery, and
  cancellation that retains a labeled resumable partial.
- One composed `ModelAcquisitionCommandService`; install-and-use surfaces
  run acquisition then the existing durable activation as separate
  operations.

### Removed

- The synchronous download/prepare route: `download_model`,
  `prepare_model`, `prepare_local_model`, conversion cleanup helpers from
  production, and `ModelInstallationService`. Frontends may not import
  download/prepare/hub/storage/repository modules (AST guards).

### Changed (Road to 1.0 — Phase A: Session 3 frontend sweep)

- **Frontends can no longer persist whole-state dictionaries.** All ten
  `__main__` saves, ten gui/steps saves, seven dashboard saves, three
  gui/app saves, one forms save, and four chat saves are gone — routed
  through SetupService / HostModeService / ComponentLifecycleService /
  OpenWebUIService / SharingService / ModelInstallationService /
  MaintenanceService / ModelActivationService, or the application's narrow
  diff-persistence primitive. The exact-count guard is now zero across the
  entire frontend surface.
- **Repository-native query layer**: `ApplicationQueryService.snapshot()`
  assembles state from repositories + AppPaths (never wrapping the facade),
  exposing disposable drafts that carry their source revision. Status
  refreshes are pure queries and never bump revisions.
- **Composition expansion**: `Application.compose()` now wires paths,
  logger, unit-of-work factory, query service, setup/safety/runtime/
  activation/host-mode/component/OpenWebUI/sharing/model-install/maintenance
  services, and a typed systemd runtime controller. `run_gui(application)`
  / `run_chat(application)` receive the composition; constructor fallback
  stores (`StateStore()`) are removed from GUI/chat paths.
- **Safety**: `thermals --force-reset` removed from the normal CLI — a
  missing sensor now denies latch reset, and no normal flag can bypass the
  safe-temperature requirement.
- **Architecture guards** enforce: zero `.save(` outside persistence
  implementations; transitional transaction allowlist only; no
  `StateStore(` in frontends (the `--state` legacy branch excepted);
  no `Path.home()` outside paths.py; GUI modules import neither subprocess
  nor sqlite nor repositories nor privilege helpers; runtime-handoff path
  literal confined to its service; status refresh never persists.

### Changed (Road to 1.0 — Phase A: A3–A5)

- **Per-command unit of work**: services no longer share the facade
  connection; `UnitOfWorkFactory.begin()` gives every command its own
  connection with `BEGIN IMMEDIATE`, commit-on-success, rollback-on-error.
  Histories became append-only (the facade can no longer clobber narrowly
  appended records), proven by a four-worker concurrency test.
- **Named setup workflow** (A3): `SetupService` owns canonical stages
  (WELCOME…COMPLETE) with expected-stage transitions — stale/skipped
  transitions raise `SetupConflict`, repeats are idempotent, evidence is
  recorded, and repair never rewinds the safety acknowledgement. Bootstrap
  persistence now goes through the service (3 saves removed).
- **Typed runtime preview/apply** (A5): migration 002 adds the
  `known_good_runtime` row; `RuntimeConfigurationService.preview()` is a
  pure projection sharing apply's exact validation; `apply()` commits in
  one unit with revision checks and publishes the handoff from the
  committed revision. Autotune trials/winners route through it.
- **Model activation service** (A4): thermal-latch gate, fit/artifact
  policy, candidate commit → handoff → restart → health → bounded
  inference probe → known-good promotion, with verified rollback and
  durable `RECOVERY_REQUIRED` when rollback itself fails. All
  `model_manager` mutations route through it (3 saves removed).
- Whole-state-save guard exact counts: **model_manager 3→0, tune 2→0**
  (plus bootstrap 3→0 earlier in the phase). Remaining inventory is
  frontend-only: `__main__` 10, gui/steps 10, dashboard 7, gui/app 3,
  chat 4, forms 1.

### Changed (Road to 1.0 — Phase A progress)

- **Thermal latch is service-persisted** (A1): `ThermalStateService` is the
  sole writer of the safety-authoritative thermal state; whole-state saves
  can no longer clear or downgrade a latched stop; stop intent is durable
  *before* the server stops; a missing sensor refuses latch reset; failed
  GPU-profile restoration keeps durable recovery evidence.
- **Narrow history appends** (A2): benchmark and autotune records go through
  capped repository appends with transactional retention — no prompts or
  generated content are stored (canary-tested).
- Whole-state-save guard moved to **exact expected counts**: thermals 2→0,
  chat 5→4, tune 3→2.

### Fixed (R2 hardening)

- **Failed legacy import no longer publishes an empty database**: compose
  returns an explicit repair-required application; only `repair-status`
  and `repair-retry` are permitted; every other command exits 78 until
  migration succeeds.
- **Stale drafts can no longer overwrite newer state**: whole-state saves
  validate against the revision carried by the saved mapping, not a
  store-level cache.
- **Schema migrations are atomic**: statement-by-statement execution inside
  an explicit transaction (no `executescript`); a mid-migration failure
  leaves neither partial tables nor a recorded version.
- **Durability is real**: all durable artifacts publish through fsynced
  six-step atomic writes; new databases are 0600 from first connect;
  app-owned sensitive directories are enforced 0700.

### Changed

- Compatibility `transaction()` now matches the legacy contract exactly
  (replacement mappings persisted, `None` cancels, other types rejected).
- Shared SQLite connections are serialized by a process-local reentrant
  lock; cross-process writers remain flock-serialized.
- `runtime-handoff.json` is rendered by a dedicated renderer/service —
  only after committed runtime/model/profile changes, carrying
  `config_revision` and model identity, regenerated when missing or stale
  at daemon start, with publication failures reported separately from
  database commits.
- Two guard tests now drive transitional persistence toward zero:
  direct `StateStore(` construction sites and per-file whole-state
  save/transaction counts.

## [Unreleased] — R2.2 cutover

### Added

- **SQLite is the source of truth** (ADR 001 cutover): the composition root
  opens/initializes `state.db`, auto-imports a legacy `state.json` once on
  first run, and serves every surface through the compatibility facade
  (`compat_state.CompatStateStore` — same `load/save/transaction` contract).
  JSON remains a read-only backup; explicit `--state <json>` opts into
  transitional legacy mode.
- Typed repositories (`repositories.py`) over all migration-001 tables; raw
  SQL no longer appears outside them.
- Runtime handoff artifact: every committed save renders
  `<app_dir>/runtime-handoff.json` (0600); launcher v2 execs argv built from
  it (legacy `state.json` fallback retained for pre-cutover installs).
- Optimistic revision checks on whole-state saves (`StaleStateError`);
  transactions remain flock-serialized and lost-update safe across threads
  and processes (`check_same_thread=False` + busy timeout).
- Cutover guard test freezing direct `StateStore(` construction in
  production at its four transitional call sites.

### Changed

- Generated launchers no longer use positional `CFG[…]` arrays; both handoff
  and legacy paths emit one argument per line into a single `exec`.
- `--state <file>` semantics narrowed to transitional legacy mode (no
  database is created or imported in that invocation).

## [0.8.0.dev0] — unreleased development line

The 0.8 line targets the production-readiness plan: stabilized beta
(0.8), transactional core and safety supervisor (0.9), secure lifecycle
(0.10), operations/DR UX (0.11), and the 1.0 stable gate.

### Added

- State schema **v5**: declared telemetry keys (`bench_history`,
  `autotune_history`, `thermal_watchdog_state`) and llama.cpp build
  provenance (`llamacpp_build`, `llamacpp_history`); tested migration from
  v4; monotonic `revision` counter.
- `StateStore.transaction()`: advisory-file-locked read-modify-write with
  revision increments, preventing lost updates between GUI, CLI, watchdog,
  and benchmark writers. Benchmark history recording uses it.
- `paths.AppPaths`: explicit application path profile with test isolation
  (`AppPaths.temporary`), symlink rejection for app-owned directories, and
  no import-time `Path.home()` evaluation.
- llama.cpp pinned lifecycle: shipped known-good tag pin, staged source
  clone builds (active checkout untouched until smoke tests pass), atomic
  source+build swap with health-checked automatic rollback,
  `llamacpp status|update|rollback`.
- Thermal watchdog hardening: preserved GPU-profile baseline across
  throttle/recover cycles, idempotent latched stop, prominent degraded-sensor
  status, explicit safe-temperature `thermals reset [--force-reset]`.
- Self-healing `llm ensure`; live tokens/second in chat; `/bench` with
  repeat aggregation; `/save`, `/load`, `/export`, `/system`, `/temp`,
  `/retry`, `/recommend`; prompt caching; conversation persistence.
- Catalog expanded to 24 models with fit-aware search/recommendation and
  release-tier metadata (`supported` / `preview`).
- Production hardening: Hugging Face token delivered via private 0600
  env-file (never argv/logs), rotating setup logs, Open WebUI pinned image +
  hardened container flags, systemd memory guards under safeguards,
  `--version`, clean Ctrl-C (exit 130), behavioral launcher argv test,
  headless GUI contract test, dashboard deferred-import coverage.

### Changed

- GUI split into the `bc250_llm_mode/gui/` package (app/steps/dashboard/
  forms) with pure form helpers unit-tested without tkinter.
- `llm status` is read-only (no acknowledgment required); mutating llm
  actions print JSON envelopes and persist through the store.
- Setup log rotation: 5 MB × 3 backups.

### Security

- Hugging Face credentials can no longer leak into `/proc/<pid>/cmdline`
  or `setup.log`.
- Open WebUI container runs with `no-new-privileges`, dropped capabilities,
  bounded memory (2g) and PID limit (256), on an immutable version-pinned
  image reference instead of `:main`.

## [0.7.0] — public beta

Initial public beta: resumable setup wizard, single systemd-owned model
server, 13-model catalog, fit-gated activation with rollback, desktop/LLM
boot safety, reversible host optimizations, optional Open WebUI and tailnet
HTTPS sharing, terminal chat.
