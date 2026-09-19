# September 17 dev9 development deployment

The existing Bazzite BC250 now runs `0.9.0.dev9` from code commit
`d3fcf3bf9cd3fbb39b8c6f6749330973990a076c`. Its exact wheel SHA-256 is
`b9059ec6715d7e357b82359daabd6e0ad316b87459984eec85193b951b1a8554`.
The [diagnostic record](experience-deployment-2026-09-17.json) binds results,
artifact hashes and raw-report digests to that candidate. Documentation-only
closeout can advance the source checkout without altering its build inputs or
the installed wheel.

## Delivered experience

Native Chat now supports visible context omissions, model token counting with
a labelled fallback, reviewed summaries, Markdown/code copy, edited-prompt
branches, saved instructions/templates/response controls, local text/Markdown/
PDF sources and reviewed SearXNG excerpts. Maintenance includes portable
conversation/draft/settings export and import. Model/profile comparisons show
exact recorded measurements only when their identities match.

Actual Bazzite checks found and corrected three defects before activation:

- Fedora Python needed more virtual-address headroom to import the HTTP worker;
  its finite limit is now 384 MiB, matching the PDF worker.
- Automatic cyclic collection could finalize Tk objects on a worker thread;
  the existing UI refresh coordinator now owns that collection while workers
  exist, including workers that outlive the short close budget.
- Managed acquisition records include a `sha256:` prefix; verified profile
  inputs now normalize it while retaining existing fingerprints and trust gates.

## Executed checks and their scope

| Check | Result |
| --- | --- |
| Bazzite Python 3.14.6 default + slow | 1,822 passed, one expected off-Linux skip; 1,823 selected |
| macOS Python 3.14.7 default + slow | 1,819 passed, four expected Linux-only skips; 1,823 selected |
| Slow security/recovery/clean-wheel gates | 52 passed on each platform |
| Fresh hash-locked installed Bazzite wheel | All 194 Python modules / 195 package files match source and wheel; dependency check passes |
| Installed feature checks | 50 passed; overlaps the suite above |
| Actual Tk routes/scales | 28 passed at 100% and 200%; one thread after close |
| Actual Tk experience journeys | Send, branch, settings, summary, source review, code copy, portable export/import pass |
| Native Chat with real model and SearXNG after activation | Completed in 4.97 seconds; model-counted context; exact source URL cited; saved source/answer round-trip passed |
| Short native-window resource observation | 75.3 MiB RSS after chat; 76.9 MiB after 100 route changes; 0.46% of one core during a 15-second idle sample |
| Gateway | Authenticated models and SSE pass; anonymous request returns 401 |
| Private rollback snapshot | 14 files match original hashes; consistent SQLite backup and isolated restore pass; two existing conversations open in dev9 |
| Actual previous dev5 reader | Refuses a disposable new format-2 conversation; its bytes remain intact |
| Search provider recovery | Stopped provider returns a bounded error in 0.27 seconds; restarted search returns four sources |
| Artifact set | Checksums/SBOM/draft manifest verify; release evaluator remains BLOCKED |

The Tk checks ran under an isolated Xvfb display on the physical BC250, using
temporary profiles. The route/experience checks used fixture transports; the
separate native live-model test used the actual model and external search
provider. Neither is interactive desktop, screen-reader, participant or soak
acceptance. The temporary display container was removed afterward. A 512-token
diagnostic on an earlier candidate produced no visible answer; the native
Standard 2,048-token allowance passed before and after dev9 activation.

## Installation, preservation and rollback

This was a manual development installation. A fresh environment received
hash-locked dependencies and the already-built wheel. After qualification and
idle/operation checks, the model and gateway were stopped, the active
environment was atomically exchanged, and the previously active services were
restarted and verified. The device source checkout was fast-forwarded to the
same code commit. No GitHub push, tag, package upload, production trust root or
automatic updater was introduced.

The active environment is `/root/.bc250-llm-mode/app-venv`, pointing to
`/root/.bc250-deployments/20260917-d3fcf3b/candidate-venv`. Device-only rollback
material is under `/root/.bc250-deployments/20260917-d3fcf3b/rollback`:
the original dev5 environment, consistent `profile/state.db`, private-file
copies/hash manifest, service-unit copies and prior service states. These
private files were not exported to the development machine.

The existing model, context 128,000, one slot, optimization settings, service
units and boot enablement are unchanged. The normal boot target is still
`graphical.target`; the model service is disabled for boot. The gateway retains
its original static unit state. Credentials and existing conversations remain
byte-identical. Database schema is still 14.

A rollback operator must first stop requests/operations and preserve any data
created after deployment, then restore the retained application environment
and prior service state with the same checked atomic exchange procedure. Do
not blindly copy the old database or private files over newer data. Dev5 cannot
read format-2 conversations written by dev6 and later: keep those files and use
dev9 or a full Markdown export to access them. The isolated restore and reader
check do not qualify a full interrupted physical rollback.

## Optional search provider

`bc250-searxng` is a separately installed official container, pinned to
`sha256:56d6ce4c64e76ca0b78ab21884d25b6112f81c68a3838afdc9945ad3d315e4c6`.
It publishes only `127.0.0.1:8888`, has a read-only root, dropped capabilities,
no-new-privileges, 384 MiB memory/one CPU/128 PID limits, and no automatic restart
or boot service. Its observed memory immediately after a recovery search was
97.76 MB; CPU at that moment includes startup/search work.

The provider is running and saved in native Chat. Choose **Add source → Web
search…**, search, review and attach excerpts. Only the explicit query is sent
to SearXNG and its upstream engines. After reboot, run `podman start
bc250-searxng` on the BC250 under the installation account when search is wanted.
Use `podman stop bc250-searxng` to stop it. See [web-search guidance](web-search.md).

## Remaining gates

The actual profile preflights remain ESTIMATED. The small active model has no
verified managed content identity, and the legacy runtime has no promoted build
identity or identity-bound known-good restoration record. The verified 9B model
now resolves correctly after the digest fix, but calibration still requires the
runtime identity. Establish these through the supported verification/runtime
lifecycle before trials; do not fabricate rows or downgrade the running runtime
solely to satisfy a label. Long-context TIGHT trials require explicit acceptance.

Further work needs the Bazzite/CachyOS fresh/upgraded cells, exact small/9B
profile measurements, representative long-context stress, phone/second-device,
desktop/reboot, interruption/recovery, thermal/soak and accessibility journeys.
Independent security review, non-developer acceptance, hosted candidate CI and
owner-gated signing/publication also remain pending. These local diagnostic
reports are unsigned and cannot satisfy the release verifier's evidence gates.
Release remains BLOCKED.
