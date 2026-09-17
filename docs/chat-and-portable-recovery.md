# Chat, sources and portable recovery

These features are part of the dev6 development source. The recorded dev5
installation predates them. Developer tests and real Tk fixtures do not qualify
inference, accessibility, or recovery on a physical appliance.

## Long conversations

The context line estimates the request size while you write. **Context…** shows
which numbered messages will be included and excluded. Send attempts to count
the formatted prompt with the running model's template/tokenizer, using a
six-second total preflight bound. Unsupported or unavailable endpoints fall
back to a clearly labelled local estimate. Estimates are not guaranteed token
counts for every language/model.

Instructions remain pinned. Older complete turns can be excluded from a
request, but remain in the saved conversation. When preflight excludes history,
the app asks whether to send with recent history. An oversized latest message
or instructions is refused without deleting the draft. Each request has at
most 500 messages and 4 MiB of content; each message is bounded to 256 KiB.

**Conversation → Start a conversation with a summary…** runs a local summary
request. Review and edit the result before creating the new conversation. If
earlier messages did not fit the summary request, the review says so; the
summary is not presented as covering omitted history. The original conversation
and draft remain intact. Summaries can contain model errors.

## Formatting, editing and instructions

Assistant answers render headings, bold text, inline code, fenced code and
quotes in native text widgets. Unsupported Markdown/HTML remains text. Code
blocks have individual **Copy code** buttons (up to 128 in the bounded visible
transcript). **Response → Copy code…** also provides a keyboard-operable
selector and preview. Rendering is bounded; long responses fall back
to readable plain text after the styling limit. Streaming stays incremental
and formatting is finalized at completion.

**Conversation → Edit a prompt into a branch…** lets you choose any saved user
prompt by number, edit it, and create a separate conversation. The branch
contains history before that prompt; the edited prompt and its sources become
a draft. Nothing is sent until Send. The original conversation is not rewritten.

**Chat settings** saves instructions, an optional temperature from 0–2, and a
response allowance of 512, 2,048, 4,096, or 8,192 tokens per conversation. The
current context may lower the allowance. Larger allowances leave less room for
history and may increase generation time; they do not guarantee longer answers.
Writing, Coding and Summarizing templates can be loaded, edited and saved under
their existing name or a new name. There are at most 20 templates, each 16 KiB.
The optional ten-second draft checkpoint toggle applies to the current window.

## Documents and web excerpts

Choose **Add source → Local document…** for UTF-8 `.txt`, `.md`, `.markdown`,
or `.pdf` files. Review extracted text before adding it. **Sources…** lets you
read or remove a source before sending. Selected text is included in the user
message, identified as quoted material, and saved in the private conversation.
Original file paths are not stored or sent to the model. Original files remain
unchanged.

Limits: four sources per message; 16 MiB per input file; 64 KiB extracted text
per source; 128 KiB combined extracted text. PDFs support up to 40 pages with
page labels. Extraction uses a separate process with a 12-second parent
deadline, an eight-second CPU limit, and a 384 MiB address-space limit on Linux.
Compressed PDF streams are bounded. These limits can refuse complex PDFs.
Encrypted PDFs are refused; scanned/image-only PDFs need OCR first. Extraction
does not promise visual layout fidelity: check columns, tables and reading
order in the preview. No document macros, embedded scripts, or links are run.

**Add source → Web search…** uses a SearXNG instance you configure. Review the
query before Search, then choose which excerpts to attach. The provider and its
upstream engines receive that query; conversation history and documents are
not sent to search. Results carry source URLs for citations. They are search
excerpts, not fetched full pages or verified facts. No automatic model browsing
or host-command execution occurs. See [SearXNG setup](web-search.md).

Saved conversations (including instructions, drafts and source text) stay in
the private conversation directory, bounded to 2,000 messages/8 MiB per file.
The 200-file creation quota never deletes existing history. A full Markdown
export includes saved instructions and source text; a redacted export excludes
them. Portable backups include complete conversation files, including drafts.

Conversation files saved by dev6 use format version 2. Existing version-1 files
load without being rewritten until saved. A dev5 rollback cannot open a
version-2 conversation and leaves the file intact; use dev6 again or its full
Markdown/portable export to access that content. Do not treat the unchanged
database schema as proof that older application versions understand new private
conversation formats. This compatibility boundary must be included in physical
update/rollback acceptance before release.

## Portable backups

Open **Maintenance → Backups → Export conversations and settings…**. All saved
conversation files are selected; display preferences and reusable templates are
optional. Choose a destination outside the application profile, preferably on
another disk. An archive supports up to 200 conversations and 128 MiB. Invalid
source files or excessive selections refuse export rather than silently
omitting work. The file is readable/unencrypted and written with private file
permissions. Its contents are verified before the destination is published.

**Import portable backup…** verifies every member and shows conversation/draft
counts, titles and optional settings before import. Conversations receive new
IDs. The same archive resumes after interruption without duplicating or
overwriting previously imported files, including conversations edited after
the first attempt. Each conversation is published atomically; a partial import
reports its remaining work and can be retried. Changed archives need a new
preview. Delete unwanted imported conversations through Chat's normal controls.

Only appearance, interface scale and reduced motion can be restored globally.
Notification consent, credentials/revocations, thermal state, runtime/model
selection, operations, and services are excluded. Existing template text is
preserved; conflicting imported templates get a distinct name. Model bytes and
Open WebUI's separate data volume are not included.

The original **Create backup / Preview restore** controls still refer to a
database configuration backup. Its restore preserves current local files; it
does not recover conversation files from a failed disk. Keep a portable backup
for that purpose.

## Measured model guidance

Select an installed model in **Models → Compare local profile measurements…**.
Interactive, Long context, Shared and Cool show estimated fit separately from
actual local first-response time, generation rate, peak temperature and the
context/slot settings used. **Review profile / calibrate…** opens that model's
exact profile preview and existing guarded calibration action. Profiles can
also compare up to three selected profiles.

Measurements must match model content, runtime identity, profile revision and
resolved settings. Evidence older than 30 days is labelled historical; absent
or mismatched evidence says not measured. Unknown temperatures remain unknown.
Legacy records without token-rate provenance display units/s, and new streams
without backend token timings display chunks/s. A short calibration is not a
quality benchmark, full-context trial, concurrent-client test or thermal soak.
Calibration does not automatically apply its proposed winner.
