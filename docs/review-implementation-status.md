# September 4 review implementation status

The corrective implementation and the existing UX changes are committed and
pushed. **Version `0.9.0.dev4` is installed and verified on the owner's BC250**,
from code commit `317c30deefb30a7d0b51a01d9594cb8af1a9dcca`. Owner plans and
scratch files were preserved. The local results below at `0.9.0.dev0` are the
earlier development checkpoint; the final hosted CI and deployment results
follow here. This development installation does not qualify a final release.

## GitHub and BC250 deployment

- [Code commit](https://github.com/caamer20/BC250-LLM-MODE/commit/317c30deefb30a7d0b51a01d9594cb8af1a9dcca)
  includes the completed review work plus the defects found during deployment:
  read-only shared profile locks in the strict gateway sandbox, visible recovery
  for empty/reasoning-only answers, and exact private-address reservation before
  the Podman bridge activates after reboot.
- [Final hosted CI](https://github.com/caamer20/BC250-LLM-MODE/actions/runs/33934503955)
  passed on Python 3.11 and 3.14: **1,753 passed and one expected platform skip
  per interpreter**, 1,754 selected and finished with no omissions. Each includes
  all 52 slow gates and 28 real Linux Tk 8.6 route/scale checks. The build job
  and clean-wheel checks passed.
- The deployment wheel was built once from the exact committed source through
  a clean sdist and verified outside the checkout. All **184 installed Python
  modules** match its bytes and inventory; schema remains 14. The BC250 source
  checkout was fast-forwarded without overwriting local changes.
- Real native chat completed with visible text. A current named-client
  credential passed authenticated gateway SSE, while a request without a
  credential was rejected. The earlier probe's obsolete legacy credential was
  also correctly rejected; no credential was rotated or modified.
- LFM2.5-2.6B-Q5_K_M remains selected with **128,000 context tokens and one
  slot**, verified against application configuration and llama.cpp `/props`.
  The model service is active and disabled for boot. The current-boot supervisor
  and request metrics are operating; the existing disabled thermal-monitoring
  preference and `KEEP_LOADED` policy were preserved.
- Model files, credential files and conversation files are unchanged. Previous
  application copies, service definitions and a consistent database backup are
  retained in the private rollback directory:
  `/root/.bc250-deployments/20260904-317c30d-final/rollback`.

The [deployment record](review-deployment-2026-09-04.json) binds the source,
artifact digests, final CI inventory and redacted appliance checks. Earlier
unsuccessful attempts retained their own rollback records. An intervening host
reboot exposed the cold-start bridge dependency; final verification exercised
the corrected gateway while optional Open WebUI was stopped.

| Phase | Implemented changes | Remaining qualification |
| --- | --- | --- |
| RV-0 | Complete pytest inventory, omission detection, machine-readable skips/results, default + slow CI, current-state index; hosted Python 3.11/3.14 gates passed | Repeat qualification for any later candidate code change |
| RV-1 | Committed latch checks on starts/restarts; repeated stop enforcement; current-boot supervisor; sensor preflight; guest-process cleanup; applied idle-policy snapshot; unknown/in-flight activity suppresses idle stop; observed policy status | Physical thermal, guest termination, reboot and resource measurements |
| RV-2 | Held-descriptor archive inspection; strict member, size, hash and schema validation; actual-byte Verify/Preview/Restore; unsupported inclusion requests refused | Large real-device archive and storage interruption measurements |
| RV-3 | Complete local-asset preservation; current safety, runtime and credential authority retained; external profile exclusion; exact exchange/rollback intent receipts; verified reverse exchange; prior-profile location | Linux atomic-exchange and installed appliance journeys on both supported hosts |
| RV-4 | Admission before handler creation; bounded HTTP framing/body/response/SSE; exact in-flight reservations across rate windows; enforced generation cap; interruptible response deadlines; strict-sandbox and cold-bridge startup corrections | Independent security review and long-running hostile-traffic qualification |
| RV-5 | Venv interpreter preserved; friendly alias handling; duplicate alias ambiguity refused; missing timestamps remain unknown; negative readiness expires Send; connection checks bind credential/runtime/invocation/endpoint generations | Fresh/upgraded desktop-menu launch and real client versions |
| RV-6 | Closed terminal stream results; explicit SSE completion; silent-read cancellation; retained partials and result classification; prompt preflight; save recovery; same-route lifecycle protection | Physical native/terminal inference journeys |
| RV-7 | Private metadata index, pagination, visible invalid-file recovery, revision-fenced saves, 200-file creation quota, legacy discovery through 10,000 files, opt-in draft checkpoints, incremental output and follow control | Large-history device responsiveness and resource measurements |
| RV-8 | Native Backups page, exact restore preview, asynchronous close boundary, broker fairness/readiness, selected connection target, model storage/RAM guidance, scrolling/focus, honest theme fallback | Linux/KDE scale screenshots, screen readers and non-developer journey acceptance |
| RV-9 | One immutable workflow source; hashed dependencies; actual installed-dependency SBOM; signer/source verification; persisted decision/digest; separately attested decision/manifest; downstream comparison | Trusted hosted rehearsal, signed evidence, owner approval; upload remains unavailable |
| RV-10 | Committed code identity, local/hosted regression, real Tk, compile, clean package and installed BC250 native/gateway inference checks | Full C4 four-cell/soak, C5 independent security, C6 human acceptance and C8 signed final release remain pending |

## Operating details

- Monitoring belongs to the explicitly started model service. It is not a tray
  daemon or boot/login service. Starting or restarting refreshes the service
  definition. The controlled Fedora guest requires `procps-ng`; startup checks
  that cleanup is available. `ExecStopPost` kills a remaining `llama-server` in
  that dedicated guest if its attached host client died. This must still be
  demonstrated on both physical hosts.
- Host RAM and GPU fit are separate. Starting inference refuses below 512 MiB
  observed available host memory. Optional Open WebUI has a 2 GiB container
  allowance; displayed limits are not measurements or throughput claims.
- Idle activity comes from bounded backend metrics, covering direct local and
  gateway clients. Missing metrics suppress idle shutdown. Only counts and
  counters are observed; prompts and responses are not retained there.
- Backups contain the database only. A restore preserves current local assets,
  model/runtime service settings, keys/revocations, thermal state and operational
  lineage. It stops inference, retains the prior tree beside the profile, and
  asks the user to reopen the app after success. See the
  [preservation contract](restore-preservation-contract.md).
- Chat drafts save on ordinary leave/close. The optional checkbox enables local
  checkpoints at most once per ten seconds through the existing action lane.
  It defaults off each window. Drafts are limited to 32 KiB. Failed saves keep
  text in the window; Save response creates a separate copy on a revision
  conflict. No automatic conversation deletion enforces the quota.
- Connection verification stores a generation digest, not a URL, model name,
  key or prompt. Changes to the key, access state, runtime, invocation or endpoint
  invalidate older results. An appliance-side synthetic probe does not qualify
  PocketPal or another physical device.
- The current `system` appearance preference is visibly described as a light
  fallback. No operating-system theme detection is claimed.

## Validation scope

Local qualification passed on September 4, 2026, using Python 3.14.7 on macOS
15.7.9. The authoritative combined run selected and finished all **1,743 tests**:
**1,741 passed and two expected Linux-only atomic-exchange tests skipped**.
The inventory includes all 52 slow tests, with no missing or extra test IDs.
Compile checks, dependency consistency and `git diff --check` passed.

Real Tk 9.0 checks passed for all 14 application routes at 100% and 200% scale
(28 combinations), with no callback errors and one remaining thread after
close. The slowest measured route construction was 267 ms on this development
host. Setup and physical Linux accessibility remain outside this smoke result.

A clean source archive produced one sdist and one wheel outside the checkout's
historical build tree. The wheel installed with the exact hashed dependency
lock into a separate venv, contained all 184 expected Python modules and no
extra Python modules, initialized schema 14, and passed CLI, worker/runtime
entry-point, desktop-launcher, conversation, backup verification and restore
preview checks. The package remains version `0.9.0.dev0`. These artifacts are
developer diagnostics, not signed release candidates.

The [machine-readable qualification summary](review-qualification-2026-09-04.json)
records inventory digests, exact artifact digests, package-source identity and
the remaining gates. The full local run outputs are retained at
`/tmp/bc250-rv-qualified-inventory.json`, `/tmp/bc250-rv-qualified.xml`,
`/tmp/bc250-rv-real-tk-final.json` and
`/tmp/bc250-rv-clean-qualification/report.json` on the development host.

The independent real-widget harness is `python -m tools.real_tk_smoke`; Linux CI
runs it under Xvfb separately from the pytest process that installs Tk stubs.
Its temporary profile and suppressed host actions prevent it from changing a
real appliance. Its results prove Tcl/Tk construction, routing and focus
behavior; they do not prove screen-reader or physical appliance acceptance.

The running BC250 was inspected over Tailscale SSH. LFM2.5-2.6B-Q5_K_M was healthy:
its application configuration and llama.cpp `/props` both reported 128,000
context tokens and one slot. The inspection made no remote configuration change.
