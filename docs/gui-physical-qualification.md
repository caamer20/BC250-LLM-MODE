# Unified GUI physical qualification checklist

Use this checklist separately on an AMD BC-250 running Bazzite and on one
running CachyOS. It is an operator procedure, not a pre-approved result. Do
not create a PASS record or reuse screenshots unless every item was exercised
against the exact candidate commit and artifact inventory.

## Candidate identity

Record the full commit, package version, wheel SHA-256, artifact-inventory
digest, host distribution/version, kernel, Mesa/RADV version, firmware, 40-CU
status, and 12 GiB GPU / 4 GiB host UMA split.

## Installation and one-window journey

1. Install the clean wheel as a regular desktop user.
2. Verify the desktop menu entry and terminal entry point open the same GUI.
3. Launch twice; verify the second request activates the existing window and
   does not create another root, listener thread, or worker set.
4. Complete the five chapters from a fresh profile. Before acknowledgment,
   verify the exact warning blocks every mutation. Relaunch once mid-setup and
   reboot once at a required boundary; verify the saved durable stage resumes.
5. Install or import a standard-layout GGUF, activate it, pass health and
   bounded inference, and enter native Chat without a terminal handoff.
6. Visit Home, Models, Chat, Activity, System, Settings, and Help repeatedly.
   Confirm no second application window appears and the log/confirmation
   drawer stays inside the root.
7. Exercise model start/stop/switch, context and slot change, Open WebUI,
   tailnet sharing when configured, runtime rollback, backup/restore, repair,
   and return-to-desktop. Verify Activity reflects the durable operation truth.
8. Reboot and confirm the normal graphical desktop returns with no model
   running.

## Accessibility and resource measurements

- Capture first-paint latency, idle RSS/CPU, active-chat RSS/CPU, and RSS after
  100 route changes. Record the measurement commands and raw attachment paths.
- Verify 760×560 and a typical 1080p window at 100%, 125%, 150%, 175%, and
  200% scale. Complete keyboard-only navigation (`Ctrl+1`…`Ctrl+9`, `Ctrl+K`,
  `Ctrl+F`, `Ctrl+L`, `Esc`), check visible focus and safe Enter semantics,
  and review light/dark/system appearance plus reduced motion.
- With the available Linux screen reader, traverse every table and its
  adjacent text/Details alternative. Record the desktop, Tk, theme, and screen
  reader versions; inconsistent table announcements remain a failure or
  evidence-pending result, never an inferred pass.
- Confirm minimized refresh backoff, one refresh timer, at most three lazy GUI
  worker threads, bounded model/activity/chat/log rendering, safe close during
  streaming chat, and safe close while a durable operation is active.
- Run a sustained inference/thermal soak. Exercise throttle, latched emergency
  stop, explicit safe reset, and restoration of the prior profile.

## Required screenshots

Capture setup machine check, exact disclaimer, each of the five chapter
headings, Home, Models with fit result, native Chat streaming and stopped
partial response, Activity detail, System, Settings, Help, the in-window log
drawer, narrow layout, keyboard focus, and reduced-motion setting. Scrub all
credentials, prompts, completions, usernames, addresses, and non-contained
paths before attaching evidence.

## Result rule

Any crash, second root, stale-ready display, privacy leak, unbounded growth,
failed desktop-next-boot result, raw/public exposure, unsupported host guess,
or unrecovered operation is a FAIL or evidence-pending result—not a waiver.
Follow `release/EVIDENCE_HANDOFF.md` and the evidence schema for signed records.
