# Experience improvements — September 2026

Owner request: implement the improvements proposed after the dev5 review,
and investigate open-source internet access. This work changes the candidate;
previous deployment and test evidence cannot qualify the new package.

## Required outcomes

1. Long conversations: disclose exactly which turns are excluded from the
   model request without removing saved history; preserve instructions; use
   bounded model/template-aware token counting with a visibly estimated
   fallback; offer a new conversation with a reviewable generated summary.
2. Native chat: bounded Markdown rendering, readable/copyable code blocks,
   and edit any previous user prompt into an independent conversation branch.
3. Portable recovery: export/import conversations, drafts and selected safe
   settings to an explicit destination, with verified contents preview and
   collision-safe restoration. Preserve the existing database-backup contract
   and exclude credentials, revocations and machine operational authority.
4. Conversation controls: saved instructions, editable writing/coding/summary
   templates, bounded temperature and response-length controls in native Chat.
5. Model guidance: make existing local measurements easy to compare by exact
   model/profile/runtime identity, including first response, speed, temperature
   and tested context. Display missing/stale evidence honestly. Collect real
   hardware measurements where an authorized device is available.
6. Local attachments: explicitly select text/Markdown and PDF files, inspect
   extracted text and context cost, remove sources before sending, enforce
   file/page/text/runtime bounds, and keep source content out of diagnostics.
7. Qualification: focused behavioral/privacy/resource tests, integrated test
   inventory, real Tk checks, clean package verification, documentation. Run
   available physical journeys; retain genuine Bazzite/CachyOS, human,
   screen-reader, security and signed-release gaps without inventing evidence.
8. Web access: provide an opt-in native SearXNG query/review/attach workflow and
   document the separately configured Open WebUI option against upstream docs.
   The native workflow was selected as the default while the optional owner
   preference question remained unanswered.

## Implementation and evidence

The development implementation is complete and the existing Bazzite BC250 is
updated to dev9. Current verification is recorded in
[the deployment record](experience-deployment-2026-09-17.json), with
[operator details](experience-deployment-2026-09-17.md). The
[dev6 local qualification](experience-qualification-2026-09-17.json) is
historical. This does not complete the full physical matrix, independent
security, human or release qualification.

| Outcome | Implementation and executed verification | Remaining external evidence |
| --- | --- | --- |
| Long conversations | Whole-turn selection, exact omitted-message indices, pinned instructions, runtime template/tokenizer counting with labelled fallback, reviewed summary into a new file. Unit tests, real HTTP child tests, installed-wheel tests and real Tk summary/send journeys. Actual BC250 model counting and native send pass. | Further pinned llama.cpp/model combinations; real long-context latency and memory. |
| Chat readability/editing | Bounded Markdown and individual code-copy buttons, keyboard copy selector, independent edited-prompt branches. Real Tk verifies copy contents, callback/widget cleanup and preservation of original history. | Keyboard-only and screen-reader journeys on both supported desktops. |
| Portable recovery | Verified bounded archives, explicit destination and import preview, conversations/drafts/sources/options, selected display settings/templates, collision-safe idempotent import. Tests cover tampering, links, oversized members, interrupted import, later user edits and stale preferences; real Linux Tk export/import passes. A private deployment snapshot restored into an isolated directory with exact file hashes and a healthy database. | Fresh/upgraded and cross-device recovery, real power interruption and complete candidate-bound rollback journeys. |
| Conversation controls | Saved instructions, temperature, response limits and editable templates. Format-1 compatibility and format-2 persistence tested; native settings journey passes. | Human comprehension and acceptance, including dev5 inability to open new format-2 files. |
| Measured guidance | Bounded query of exact model/profile/runtime calibration records; separate fit estimates, stale/missing states, explicit token/chunk-rate provenance. Exact-identity mismatch and production-adapter evidence tests pass. Actual model/profile preflights remain ESTIMATED. | Establish the small model's verified artifact identity and a genuinely promoted runtime/known-good identity, then collect small/9B Interactive/Long context/Shared/Cool measurements on each host; no numbers or identities are invented. |
| Local sources | Text/Markdown and PDF extraction/review/removal, file/page/text bounds, isolated PDF child, private persistence/export. Actual PDF fixtures and clean installed child pass. | Representative user PDFs, Linux limits under inference load, reading order and accessibility acceptance. |
| Web access | Explicit query only; SearXNG JSON excerpts and URLs, review/select/attach, no page crawl. A digest-pinned, loopback-only provider is installed. Native Chat with the real BC250 model cites its source URL; provider failure/restart and bounded resource observations pass. | Further engine/model/workload coverage and non-developer source/privacy comprehension. |
| Qualification | Complete default and slow inventories, clean source → sdist → wheel, fresh hash-locked environment, exact installed-file identity, real Tk routes/scales and new journeys. | Hosted Linux CI, physical four-cell/phone/reboot/soak journeys, independent security, non-developer acceptance and owner-gated release. |

Verification also found and fixed two defects: Tk-owning task closures could
be finalized on a worker thread; cleanup preview retention changed every
second and invalidated an unchanged confirmation. Regression checks cover UI
thread finalization, queue pressure, elapsed seconds and preview expiry.

No release publication, production trust root, automatic update, background
agent, or boot service is introduced. Existing profile locks, thermal/fit gates,
bounded worker lanes, and current-boot service ownership remain authoritative.
No remote repository push occurred. The existing device received dev9 through
a reversible manual development installation, retaining its dev5 environment
and private backup. This did not enable the signed automatic-update path.

## Physical continuation

The existing Bazzite host is accessible through the owner's authenticated
`root@bazzite` SSH connection. No CachyOS or fresh-install cell, interactive
desktop participant, phone or independent reviewer has been supplied. The
Linux native checks used an isolated Xvfb display; do not infer human desktop
acceptance from them or inherit dev5 evidence for dev9.

1. Bind the final commit and the wheel digest in the qualification record to
   every new measurement. Preserve a verified current installation/profile
   rollback before a development deployment.
2. Follow the existing [four-cell/participant worksheet](appliance-experience-physical-qualification.md),
   [connection worksheet](connection-physical-qualification.md),
   [profile worksheet](profile-physical-qualification.md) and
   [update worksheet](application-update-physical-qualification.md).
3. Add the new native journeys above: long-context omission/count fallback;
   branch and reviewed summary; instructions/templates; text/PDF source review;
   web query/source disclosure; portable import interruption and retry; and
   format-2 conversation behavior across rollback.
4. Extend the tested SearXNG/model combination across representative workloads
   and the other physical cells. Preserve its loopback binding and explicit
   query consent, and include optional-provider restart after reboot in the
   operator journey.
5. Retain C5 independent security, C6 non-developer acceptance and C8
   signing/publication as separate external gates. This record grants no
   1.0 tag, production trust root, or release publication authority.
