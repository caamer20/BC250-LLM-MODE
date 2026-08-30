# EXP-4 physical Maintenance and notification qualification

This checklist collects candidate-bound evidence on a real AMD BC-250. It is
not a developer-test substitute and does not grant release eligibility.

Run the full matrix on both advertised native host profiles:

- Bazzite with the normal KDE desktop session;
- CachyOS with the normal KDE desktop session.

Record the exact commit, installed wheel SHA-256, host profile observation,
kernel, Mesa/Vulkan identity, desktop session type, and UTC start/end time.
Never record prompts, completions, paths, addresses, client labels, tokens,
notification bodies from external applications, or raw logs.

## Maintenance checks

1. Open the single native window and visit Maintenance. Confirm no second
   window, tray icon, or resident GUI process appears.
2. Confirm the inbox displays at most five items in the documented priority
   order, with evidence freshness/age, impact, resource, action, details, and
   correct nondismissibility for safety/recovery/security/integrity.
3. Leave the window open and idle for ten minutes. Measure RSS, CPU, threads,
   file descriptors, and sockets before/after; confirm normal refresh does not
   run Doctor, hash a model, contact an update channel, or start a service.
4. Run **Run full check** once and the equivalent
   `bc250-llm-mode maintenance check`. Confirm the same redacted observations
   and item order. Run `maintenance cleanup --dry-run`; confirm it deletes
   nothing and names no secret or credential material.

## Notification matrix

For each case, preserve only the closed category, delivery result/reason,
receipt count, and timestamp:

1. Fresh profile/defaults: all preferences disabled and no notice delivered.
2. Master enabled/category disabled: eligible event suppressed.
3. Master and category enabled: one fixed local notice delivered.
4. Desktop/session capability unavailable: domain action succeeds and a
   generic unavailable result is reported; no silent package installation.
5. Repeat the same committed event after application restart: no second
   notice; the durable receipt reports duplicate suppression.
6. Trigger enough distinct safe fixtures to verify the per-category and
   three-per-hour limits without delaying or changing domain results.
7. Exercise a long operation success, safe failure, rolled-back failure, and
   recovery-required terminal. Confirm no notice precedes the terminal event.
8. Exercise watchdog throttled and stopped latch transitions. Confirm the
   existing watchdog remains the only sensor loop and safety stop is not
   delayed by desktop delivery.
9. Exercise critical storage and stale-backup findings through an explicit
   bounded check. Confirm repeat evidence collapses within its six-hour window.
10. Force `notify-send` nonzero exit and timeout. Confirm operation, thermal,
    and maintenance truth is unchanged and only closed failure codes persist.
11. Inspect the SQLite profile, application logs, support bundle, process argv,
    and desktop activation request for privacy canaries. Paths, addresses,
    client/model labels, prompts, outputs, credentials, exceptions, and raw
    logs must be absent.

## Pass boundary

PASS requires both host matrices, stable resource measurements, every negative
case above, and reviewer sign-off bound to the exact candidate. Until those
records exist, report EXP-4 as **developer-qualified; physical evidence pending**.
Any package-code change invalidates the candidate-bound results.
