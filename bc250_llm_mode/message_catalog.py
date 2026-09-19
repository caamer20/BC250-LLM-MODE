"""Stable, bounded presentation copy and the bundled offline glossary.

This module is intentionally free of Tk, filesystem, database, and network
imports.  Domain and GUI code may share the same wording without making the
presentation layer an infrastructure owner.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


MAX_MESSAGE_TITLE = 96
MAX_MESSAGE_BODY = 768
MAX_GLOSSARY_RESULTS = 64
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_.-]{0,63}$")
_SECRET_LIKE_CODE = re.compile(r"^(?:HF_|GHP_|GITHUB_PAT_|BEARER[_.-])")
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class MessageText:
    """One stable user-facing message selected by a non-secret code."""

    code: str
    category: str
    level: str
    title: str
    body: str

    def __post_init__(self) -> None:
        if not _SAFE_CODE.fullmatch(self.code):
            raise ValueError("message code must be a bounded stable identifier")
        if self.level not in {"info", "success", "warning", "error"}:
            raise ValueError("message level is not supported")
        if not self.title.strip() or not self.body.strip():
            raise ValueError("message title and body are required")
        if len(self.title) > MAX_MESSAGE_TITLE or len(self.body) > MAX_MESSAGE_BODY:
            raise ValueError("message copy exceeds its presentation bound")


@dataclass(frozen=True)
class GlossaryEntry:
    """One internet-independent help definition."""

    key: str
    term: str
    definition: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", self.key):
            raise ValueError("glossary key is not a bounded stable identifier")
        if not self.term.strip() or not self.definition.strip():
            raise ValueError("glossary term and definition are required")
        if len(self.term) > 64 or len(self.definition) > 640:
            raise ValueError("glossary copy exceeds its presentation bound")


def _message(code: str, category: str, level: str, title: str, body: str) -> MessageText:
    return MessageText(code, category, level, title, body)


_MESSAGES = (
    _message("STATE_READY", "state", "success", "Ready", "The checked component is ready for use."),
    _message("STATE_DEGRADED", "state", "warning", "Available with limits", "The component is usable, but a reported limitation needs attention."),
    _message("STATE_BLOCKED", "state", "error", "Blocked for safety", "A safety or integrity check must be resolved before this action can continue."),
    _message("STATE_UNVERIFIED", "state", "warning", "Not verified yet", "The application does not yet have enough current evidence to call this ready."),
    _message("FIT_FITS", "fit", "success", "Fits comfortably", "The projected model, context, and user slots stay within the comfortable fast-VRAM range."),
    _message("FIT_TIGHT", "fit", "warning", "Fits tightly", "The projection is within the 12 GiB fast-VRAM budget but leaves little safety margin."),
    _message("FIT_NO_FIT", "fit", "error", "Does not fit", "The projected workload exceeds the 12 GiB fast-VRAM budget. Choose a smaller quantization, context, or slot count."),
    _message("THERMAL_SAFE", "thermal", "success", "Temperature is in range", "The latest observed GPU temperature is below the configured throttle threshold."),
    _message("THERMAL_THROTTLED", "thermal", "warning", "Thermal throttling is active", "New performance work is limited while the GPU cools."),
    _message("THERMAL_STOPPED", "thermal", "error", "Stopped for thermal safety", "The thermal safety latch stopped model work. Inspect cooling and wait for a verified recovery."),
    _message("SECURITY_LOCAL_ONLY", "security", "info", "Available on this machine only", "The raw model server is bound to loopback and is not published directly to the network."),
    _message("SECURITY_AUTH_REQUIRED", "security", "warning", "Authentication required", "Remote clients must use the authenticated gateway and a purpose-scoped credential."),
    _message("SECURITY_CREDENTIAL_REVOKED", "security", "success", "Credential revoked", "That client credential can no longer access the authenticated gateway."),
    _message("LIFECYCLE_STARTING", "lifecycle", "info", "Starting the model", "The durable start operation is applying and verifying the selected workload."),
    _message("LIFECYCLE_RUNNING", "lifecycle", "success", "Model server is running", "The selected model passed the current health check and is ready for requests."),
    _message("LIFECYCLE_STOPPED", "lifecycle", "info", "Model server is stopped", "No model inference server is currently active."),
    _message("CONTEXT_EXPLAINER", "context", "info", "Context is memory per conversation", "A larger context keeps more tokens available to each conversation and consumes more KV-cache memory."),
    _message("SLOTS_EXPLAINER", "slots", "info", "Slots are concurrent users", "Each parallel slot reserves its own context and KV-cache capacity. More slots reduce the context that fits safely."),
    _message("ENDPOINT_LOOPBACK", "endpoint", "info", "Local API address", "Use the loopback OpenAI-compatible base URL only from software running on this BC-250."),
    _message("ENDPOINT_REMOTE", "endpoint", "info", "Authenticated remote API address", "Remote clients use the exact HTTPS base URL and credential shown on the Connections page."),
    _message("PRIVILEGE_REQUIRED", "privilege", "warning", "Administrator approval required", "This host change needs a reviewed privileged action. Preview the exact change before approving it."),
    _message("RECOVERY_REQUIRED", "recovery", "error", "Recovery required", "Durable evidence is incomplete. Open Activity and resume or repair the recorded operation before starting conflicting work."),
    _message("ROLLBACK_AVAILABLE", "rollback", "warning", "Rollback is available", "A verified prior state is retained and can be restored through the normal preview and confirmation flow."),
    _message("ROLLBACK_COMPLETE", "rollback", "success", "Rollback verified", "The prior state was restored and passed its required verification checks."),
    _message("UNDO_AVAILABLE", "undo", "info", "Undo is available", "The app retained the evidence and recovery material required to reverse this completed action."),
    _message("UNDO_UNAVAILABLE", "undo", "warning", "Undo is not available", "The app cannot prove a safe inverse for this action. No automatic reversal will be attempted."),
    _message("EVIDENCE_ESTIMATED", "evidence", "info", "Estimated", "This value is a projection and has not been measured on the current workload."),
    _message("EVIDENCE_OBSERVED", "evidence", "info", "Observed locally", "This value was measured on this machine, but it is not a release or hardware qualification claim."),
    _message("EVIDENCE_VERIFIED", "evidence", "success", "Verified for this action", "The action completed its defined local verification checks."),
    _message("CONFIRM_DELETE_MODEL", "confirmation", "warning", "Remove this model?", "The selected model files will enter the reviewed removal or quarantine flow. Other models are not affected."),
    _message("CONFIRM_UNINSTALL", "confirmation", "warning", "Uninstall BC250 LLM MODE?", "Application-owned services and integration will be removed. Models are preserved unless separately selected."),
    _message("CONFIRM_APPLY_UPDATE", "confirmation", "warning", "Apply this verified update?", "The application will publish the staged slot and retain the prior eligible slot for rollback."),
    _message("SETTINGS_INVALID", "validation", "error", "Settings need attention", "One or more draft values are outside the supported range or do not form a safe workload."),
    _message("CONVERSATION_RENAME_INVALID", "validation", "error", "Conversation name not changed", "Choose a non-empty conversation name within the displayed length limit."),
    _message("ACTION_FAILED", "fallback", "error", "Action needs attention", "The action could not be completed. Open Activity or the relevant bounded log for a stable error code and recovery guidance."),
)

_SOURCE_ERRORS = {
    "DOCUMENT_INVALID": "This source is malformed or unsupported. Choose a readable local document or a valid web excerpt.",
    "DOCUMENT_TYPE": "Choose a UTF-8 text, Markdown, or PDF file.",
    "DOCUMENT_TEXT_LIMIT": "A document must contain readable text within 64 KiB. Attach a smaller section instead.",
    "DOCUMENT_FILE_LIMIT": "Choose a regular file of at most 16 MiB.",
    "DOCUMENT_CHANGED": "The document changed while it was being read. Choose it again.",
    "DOCUMENT_READ_FAILED": "The selected document could not be read. Its original file is unchanged.",
    "DOCUMENT_ENCODING": "Save this document as UTF-8 text before attaching it.",
    "SOURCE_COUNT_LIMIT": "Attach at most four sources to one message. Remove a source before adding another.",
    "SOURCE_TEXT_LIMIT": "Attached text exceeds the combined 128 KiB limit. Remove a source or attach a smaller section.",
    "SOURCE_DUPLICATE": "This source is already attached.",
    "PDF_INVALID": "This file is not a supported PDF, or its text could not be read. Export a fresh copy or attach its text.",
    "PDF_TIMEOUT": "PDF extraction exceeded 12 seconds. Export fewer pages or attach its text instead.",
    "PDF_RESOURCES": "PDF extraction exceeded its resource limits or could not finish. Attach a smaller PDF or its text.",
    "PDF_ENCRYPTED": "This PDF is encrypted. Export an unencrypted copy you are authorized to read.",
    "PDF_PAGE_LIMIT": "This PDF exceeds 40 pages. Export a smaller page range before attaching it.",
    "PDF_NO_TEXT": "No readable text was found. Scanned PDFs need OCR before they can be attached.",
    "PDF_STREAM_LIMIT": "A PDF page is too complex to extract within the memory limit. Attach its text instead.",
    "PDF_UNAVAILABLE": "The PDF reader dependency is unavailable. Repair the application installation.",
    "SEARCH_ENDPOINT_INVALID": "Enter a SearXNG HTTP(S) base URL without spaces, a query, a fragment, or embedded credentials.",
    "SEARCH_ENDPOINT_TLS": "Use HTTPS for a remote search provider; HTTP is supported only on loopback.",
    "SEARCH_CONFIG_INVALID": "Search configuration needs repair. Re-enter a supported SearXNG address.",
    "SEARCH_QUERY_INVALID": "Enter one search query of at most 2 KiB.",
    "SEARCH_JSON_DISABLED": "This SearXNG instance refuses JSON searches. Enable search.formats: [html, json] in its settings.",
    "SEARCH_RESPONSE_LIMIT": "Search response exceeded its size or time limit. Try again or check the provider.",
    "SEARCH_FAILED": "Search could not finish. Check the SearXNG address, network connection and JSON support; the query was not added to your conversation.",
}
_MESSAGES += tuple(_message(code, "source", "error", "Source needs attention", body)
                   for code, body in _SOURCE_ERRORS.items())

SEARXNG_PROVIDER_HINT = "SearXNG base URL (example: http://127.0.0.1:8888)"

MESSAGE_CATALOG = {item.code: item for item in _MESSAGES}
MESSAGE_CATEGORIES = frozenset(item.category for item in _MESSAGES)
REQUIRED_MESSAGE_CATEGORIES = frozenset({
    "state", "fit", "thermal", "security", "lifecycle", "context", "slots",
    "endpoint", "privilege", "recovery", "rollback", "undo", "evidence",
    "confirmation",
})


def message_for(code: str) -> MessageText:
    """Return stable copy; unknown/untrusted codes cannot become raw UI copy."""
    normalized = str(code).strip().upper()
    found = MESSAGE_CATALOG.get(normalized)
    if found is not None:
        return found
    from .problem_details import PROBLEM_CATALOG
    problem = PROBLEM_CATALOG.get(normalized)
    if problem is not None:
        return MessageText(problem.code, problem.category, problem.severity,
                           problem.title, problem.user_message)
    safe = (
        normalized
        if _SAFE_CODE.fullmatch(normalized) and not _SECRET_LIKE_CODE.match(normalized)
        else "UNKNOWN_CODE"
    )
    return MessageText(
        code=safe,
        category="fallback",
        level="error",
        title="Action needs attention",
        body=(
            f"The application does not recognize status code {safe}. "
            "Open Activity or the relevant bounded log for recovery details."
        ),
    )


def safe_exception_message(_exc: BaseException, *, code: str = "ACTION_FAILED") -> MessageText:
    """Map an exception without rendering its potentially sensitive text."""
    return message_for(code)


class SourceInputError(ValueError):
    """Closed public error code; no document or remote diagnostic text."""
    def __init__(self, code):
        self.code = code if code in _SOURCE_ERRORS else "DOCUMENT_INVALID"
        super().__init__(message_for(self.code).body)


_GLOSSARY = (
    GlossaryEntry("model", "Model", "The learned weights and architecture used to generate a response. Installing a model does not start it."),
    GlossaryEntry("quantization", "Quantization", "A compact numeric representation of model weights. Smaller quantizations use less VRAM, usually with a modest quality tradeoff.", ("quant",)),
    GlossaryEntry("gguf", "GGUF", "The model-file format used by llama.cpp. BC250 LLM MODE accepts standard per-tensor layouts and rejects fused or MAX repacks."),
    GlossaryEntry("context", "Context", "The maximum token history available to one conversation slot. More context consumes more KV-cache memory.", ("context window", "tokens")),
    GlossaryEntry("kv-cache", "KV cache", "GPU memory used to retain attention state for the active context. Its size grows with context and parallel slots.", ("kv",)),
    GlossaryEntry("slots", "Slots", "The number of conversations that can generate concurrently. Each slot reserves context and KV-cache capacity.", ("parallel users", "concurrency")),
    GlossaryEntry("vram", "VRAM", "Fast memory reserved for the integrated GPU. This appliance plans against a 12 GiB fast-VRAM budget."),
    GlossaryEntry("gtt", "GTT", "Slower system-backed memory the GPU may use as overflow. It is not a substitute for fast VRAM."),
    GlossaryEntry("ram", "RAM", "Memory available to the operating system and host processes. On the recommended 12/4 split it is deliberately limited."),
    GlossaryEntry("uma", "UMA", "Unified Memory Architecture: system memory is divided between the integrated GPU and operating system by firmware.", ("bios split",)),
    GlossaryEntry("cu", "Compute unit (CU)", "A group of GPU execution resources. Boards that support it should expose all 40 BC-250 compute units for best throughput."),
    GlossaryEntry("vulkan", "Vulkan", "The graphics and compute API llama.cpp uses here to run inference on the BC-250 GPU compute units."),
    GlossaryEntry("open-webui", "Open WebUI", "An optional browser chat interface that connects to the local OpenAI-compatible model endpoint.", ("web ui",)),
    GlossaryEntry("base-url", "Base URL", "The root API address entered in an OpenAI-compatible client. Use the exact URL shown on Connections.", ("endpoint",)),
    GlossaryEntry("gateway", "Gateway", "The authenticated local service that safely presents the loopback model API to approved tailnet clients."),
    GlossaryEntry("tailscale-serve", "Tailscale Serve", "The tailnet-only HTTPS publication layer used for remote access to the authenticated gateway.", ("serve",)),
    GlossaryEntry("funnel", "Tailscale Funnel", "A public-internet publication feature. BC250 LLM MODE keeps Funnel off and does not use it for model access."),
    GlossaryEntry("installed", "Installed", "Model files are present in an app-approved location. Installed does not mean verified or running."),
    GlossaryEntry("verified", "Verified", "The relevant integrity and compatibility checks passed for the stated action and evidence scope."),
    GlossaryEntry("active", "Active", "The model currently selected by the running, healthy inference service."),
    GlossaryEntry("known-good", "Known-good", "A previously applied configuration that passed its required health and integrity checks and may be eligible for rollback."),
    GlossaryEntry("recovery", "Recovery required", "A durable operation stopped without enough evidence to declare success or a safe rollback. Conflicting work remains blocked until reviewed."),
)

GLOSSARY = {entry.key: entry for entry in _GLOSSARY}


def glossary_entries(query: str = "", *, limit: int = MAX_GLOSSARY_RESULTS) -> tuple[GlossaryEntry, ...]:
    """Return deterministic local token matches with an explicit hard limit."""
    if not 1 <= int(limit) <= MAX_GLOSSARY_RESULTS:
        raise ValueError(f"glossary limit must be 1..{MAX_GLOSSARY_RESULTS}")
    tokens = tuple(_TOKEN.findall(str(query).casefold())[:8])
    if not tokens:
        return _GLOSSARY[:limit]
    matches: list[GlossaryEntry] = []
    for entry in _GLOSSARY:
        haystack = " ".join((entry.term, entry.definition, *entry.aliases)).casefold()
        if all(token in haystack for token in tokens):
            matches.append(entry)
            if len(matches) == limit:
                break
    return tuple(matches)


if not REQUIRED_MESSAGE_CATEGORIES <= MESSAGE_CATEGORIES:
    raise RuntimeError("stable message catalog is missing a required category")
if len(MESSAGE_CATALOG) != len(_MESSAGES) or len(GLOSSARY) != len(_GLOSSARY):
    raise RuntimeError("stable presentation identifiers must be unique")


__all__ = [
    "GLOSSARY", "MAX_GLOSSARY_RESULTS", "MESSAGE_CATALOG",
    "MESSAGE_CATEGORIES", "REQUIRED_MESSAGE_CATEGORIES", "GlossaryEntry",
    "MessageText", "glossary_entries", "message_for", "safe_exception_message",
]
