# Accessibility and privacy behavior

This document describes the implemented desktop behavior. It is not physical
qualification evidence for Bazzite, CachyOS, a screen reader, or a particular
desktop theme.

## Keyboard and display

BC250 LLM MODE uses one native Tk window. `Ctrl+1` through `Ctrl+9` opens the
first nine primary pages, `Ctrl+K` opens the local command palette, `Ctrl+F`
focuses the current page's primary control, `Ctrl+L` opens bounded logs, and
`Escape` closes the in-window drawer. The command palette performs bounded
token matching without a network call. Protected results only open their
normal preview page.

Settings provides 100%, 125%, 150%, 175%, and 200% interface scaling and a
reduced-motion preference. Light and dark semantic colors meet a 4.5:1 text
contrast floor against the configured background. State, fit, warning,
progress, and result meaning is also written in text; color is not the only
signal. Streaming chat updates the transcript without moving keyboard focus.

Confirmations do not make the mutating button an implicit default. A typed
confirmation focuses its phrase field and Return is accepted only after the
exact phrase is present. Other confirmations begin on Cancel. Escape closes
the drawer without running its action.

Tk and assistive-technology behavior varies across desktop environments.
Tree-table headers and cell changes may not be announced consistently by
every Linux screen reader. Important tables repeat the selected record in
adjacent text or an explicit Details view, but full screen-reader parity is
still evidence-pending on physical Bazzite and CachyOS systems. Native file
chooser accessibility also belongs to the host desktop. These limitations are
not waived by developer tests.

## Local data and network behavior

The Settings **Privacy** page and `bc250-llm-mode privacy` print the same
query-only inventory. It covers saved conversations, logs, durable operation
events, benchmarks, purpose-scoped credentials, the Open WebUI Podman volume,
backups, support bundles, verified update bundles, notification receipts, and
update network behavior. Each row states its real local owner/location,
retention behavior, whether it can leave the machine, and the existing page
that safely manages it.

BC250 LLM MODE has no telemetry and sends no usage analytics. There is no
telemetry toggle because there is no telemetry subsystem. Model downloads,
explicit connection probes, local or remote chat requests, and any future
eligible signed-channel check are user-directed network operations, not
background analytics. The current production build has no available online
application-update channel and performs no automatic update check.

Prompts, responses, credential values, authorization headers, and raw remote
bodies are excluded from logs, durable events, notifications, metrics, and
support bundles. A support bundle is generated only on request and is never
uploaded by the application.
