# Dev15 production-hardening candidate

Source `0.9.0.dev15` fixes three defects found during the production-readiness
audit. The installed BC250 remains dev14 until a separate deployment record
confirms activation. These changes do not constitute independent security,
human acceptance, hardware-soak, or signed-release evidence.

## Confirmed defects and corrections

1. **Safety and readiness probes could outlive their deadline.** Socket read
   timeouts reset as data arrives. A fixture trickling HTTP headers or metrics
   kept the old metrics reader waiting beyond its declared sampling budget.
   Local model, inference and gateway probes now use one monotonic deadline
   across connect, write, headers and body. Their literal-loopback transport
   does not use DNS, environment proxies, redirects, background timers or
   additional processes. Replies are bounded before JSON/metrics parsing.
   Host Open WebUI warm-up and remote connection verification use the existing
   isolated HTTP worker to include DNS/TLS/header work within the deadline.
2. **Connection verification could forward a bearer key across redirects.** A
   two-server fixture proved that the old urllib adapter sent its synthetic
   key to the redirect destination. Verification now refuses redirect following,
   with credentials passed to its fixed worker through private stdin. The
   worker supports bounded GET/POST, headers-only warm-up and the existing
   first-valid-SSE-event probe contract. It retains no credentials or response
   content in diagnostic records. This is a reproduced local canary result,
   not evidence that an owner's credential was exposed.
3. **Missing server geometry could be mistaken for verified settings.** If
   `/props` omitted context or slots, the old health result filled them from
   the requested configuration. Those observations now remain `None`, including
   the derived aggregate context when either input is unknown. Existing
   activation/runtime verification consequently cannot use missing observations
   as proof. Requested settings remain separately identified.

Malformed, oversized, truncated, ambiguously framed or unavailable metrics
cannot prove that the server is idle. Fractional request counts and duplicate
required counters also remain unknown. Valid content-length, chunked and
EOF-delimited replies are covered by real-socket tests. The external-effect
inventory now detects standard-library HTTP imports as well as httpx, closing
the inventory gap that hid these probe paths.

## Qualification scope

Regression tests first reproduced the trickle, credential-redirect and
requested-versus-observed defects against the previous implementation. Focused
checks cover the corrected boundaries, existing native chat/search preflight,
two-client verification/revocation, gateway behavior, Open WebUI transactions,
and the execution inventory. The exact code commit is
`3974ffa0381a03da2853141cf3348094a3c5aad4`; its clean-built wheel SHA-256 is
`eedd80343fe39c12c8e843c69a9a26727613e16af0ca77f5ed8f3f732eba0ba3`.
The [qualification record](production-hardening-qualification-2026-09-19.json)
binds the source archive, sdist, wheel and executed inventory.

All four environments execute the same 1,962-test inventory, including all
52 slow gates: macOS passes 1,943 with 19 platform skips; Bazzite passes 1,947
with 15 skips; GitHub Python 3.11 and 3.14 each pass 1,955 with seven skips.
Eight Bazzite compiler skips pass inside the existing guest's 82/82 installed
runtime checks. Fresh hash-locked environments verify all 198 package files /
197 Python modules against the exact source and wheel. Installed feature
checks pass 93/93 and probe checks 81/81 on Mac and Linux. These overlap the
full suite and are not additional unique tests.

Actual Tk checks pass 28 routes/scales and eight Bonsai widgets on Mac and
Linux. Linux also passes the source/summary/branch/clipboard/portable-backup
journeys at 100% and 200% scale. These use isolated fixture services and data.
The temporary display container and its socket are removed. The current dev14
application, model selection, launch configuration, model/gateway service
identities and definitions, governor configuration and graphical boot target
match the pre-test snapshot. All ten owner-controlled untracked files remain
unchanged. No candidate activation or production model inference was performed.

A read-only compatibility check of the new transport against the running BC250
completed `/health`, `/v1/models`, `/props` and `/metrics` requests. It observed
8,192 context / one slot, with responses from 15 to 7,602 bytes and approximately
0.7–1.4 ms per request. Service PIDs/invocations were unchanged and no generation
request was sent. This verifies compatibility of the helper, not deployment or
qualification of the whole new application.

## Remaining production gates

The release remains blocked by the evidence required in
[the external-evidence handoff](../release/EVIDENCE_HANDOFF.md) and
[the physical journey worksheet](appliance-experience-physical-qualification.md):
Bazzite/CachyOS fresh and upgraded cells, the complete fourteen-journey matrix,
phone and desktop/reboot/recovery behavior, accessibility and non-developer
acceptance, independent security review, at least 24 hours of soak, and exact
owner-approved signed-release evidence. Developer tests and this audit cannot
substitute for those records. No signing, release tag, updater enablement or
package publication is part of this checkpoint.
