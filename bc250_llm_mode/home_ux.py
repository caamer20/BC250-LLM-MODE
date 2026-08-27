"""P5 §11.4: first-run and failure UX presentation contract.

PURE functions — no tkinter, no I/O, no persistence. Every helper operates
on the already-composed read models (the home snapshot ``to_dict``, the
doctor report ``to_dict``, a catalog ``FitResult``, or an operations active
summary) and returns plain-language, bounded presentation data. The GUI
layer is a thin widget shell over these functions, so the whole §11.4
contract is testable headlessly.

The contract covers the seven §11.4 requirements:

1. a preflight checklist before downloads/builds;
2. explicit disk-space estimates;
3. model fit explanation (VRAM/RAM/context/slots);
4. clear separation between downloaded / installed / active / verified;
5. recovery instructions after frontend closure;
6. a safe "copy diagnostic details" control (redacted, bounded);
7. a link to the exact operation history for every failure.

It also renders the home snapshot cards (§11.1) so the GUI surface is fed
by the SAME query-only snapshot the CLI and support bundle consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- tones (drive GUI colouring; bounded vocabulary) --------------------------
TONE_GOOD = "good"
TONE_ATTENTION = "attention"
TONE_BLOCKED = "blocked"
TONE_UNKNOWN = "unknown"

_STATE_TONE = {
    "READY": TONE_GOOD,
    "DEGRADED": TONE_ATTENTION,
    "BUSY": TONE_ATTENTION,
    "BLOCKED": TONE_BLOCKED,
    "RECOVERY_REQUIRED": TONE_BLOCKED,
    "REPAIR_REQUIRED": TONE_BLOCKED,
    "UNVERIFIED": TONE_UNKNOWN,
    "UNAVAILABLE": TONE_UNKNOWN,
}

# Diagnostic details are bounded so the copy control can never produce an
# unbounded blob.
_MAX_DIAGNOSTIC_CHARS = 8192
_MAX_FINDINGS_IN_DETAILS = 20


def tone_for_state(state: str) -> str:
    return _STATE_TONE.get(state, TONE_UNKNOWN)


# --- 8. home snapshot rendering (consistent source) ---------------------------

def overall_headline(home: dict[str, Any]) -> dict[str, str]:
    """Top-of-home headline from the composed overall dimension."""
    overall = home.get("overall") or {}
    state = overall.get("effective_state") or overall.get("state") or "UNVERIFIED"
    return {
        "state": state,
        "tone": tone_for_state(state),
        "evidence": overall.get("evidence") or "",
        "as_of": overall.get("as_of") or "",
    }


def home_card_lines(home: dict[str, Any]) -> list[dict[str, Any]]:
    """One display row per home card, in a stable order.

    Each row carries the card's effective state, tone, evidence, data
    timestamp, and staleness reason — a stale card is labeled, never shown
    as a current green status (§11.1).
    """
    cards = home.get("cards") or {}
    lines = []
    for name in sorted(cards):
        card = cards[name]
        health = card.get("health") or {}
        state = health.get("effective_state") or health.get("state") or "UNVERIFIED"
        stale = bool(card.get("stale"))
        lines.append({
            "name": name,
            "state": state,
            "tone": TONE_UNKNOWN if stale else tone_for_state(state),
            "evidence": health.get("evidence") or "",
            "as_of": card.get("as_of") or "",
            "stale": stale,
            "stale_reason": card.get("stale_reason") or "",
        })
    return lines


# --- 1. preflight checklist ---------------------------------------------------

@dataclass(frozen=True)
class PreflightItem:
    label: str
    ok: bool
    detail: str


def preflight_checklist(home: dict[str, Any]) -> list[PreflightItem]:
    """Gate downloads/builds behind a bounded, honest checklist."""
    cards = home.get("cards") or {}

    def state_of(name: str) -> str:
        health = (cards.get(name) or {}).get("health") or {}
        return health.get("effective_state") or health.get("state") or "UNVERIFIED"

    storage = cards.get("storage") or {}
    thermal = state_of("thermal")
    operations = state_of("operations")
    storage_state = state_of("storage")
    runtime = state_of("runtime")

    items = [
        PreflightItem(
            label="Enough free disk space",
            ok=storage_state in ("READY", "DEGRADED"),
            detail=(f"{storage.get('available_bytes')} bytes available"
                    if storage.get("available_bytes") is not None
                    else "filesystem usage unknown"),
        ),
        PreflightItem(
            label="Thermal latch nominal",
            ok=thermal == "READY",
            detail="safe to proceed" if thermal == "READY"
                   else "thermal latch engaged; cool the hardware first",
        ),
        PreflightItem(
            label="No operation needs recovery",
            ok=operations not in ("RECOVERY_REQUIRED", "REPAIR_REQUIRED"),
            detail="no recovery pending" if operations not in (
                "RECOVERY_REQUIRED", "REPAIR_REQUIRED")
                   else "resolve recovery before starting new work",
        ),
        PreflightItem(
            label="Runtime available",
            ok=runtime in ("READY", "DEGRADED", "BUSY"),
            detail="runtime published" if runtime in ("READY", "DEGRADED",
                                                      "BUSY")
                   else "no runtime published yet",
        ),
    ]
    return items


def preflight_ready(items: list[PreflightItem]) -> bool:
    return all(item.ok for item in items)


# --- 2. disk-space estimates --------------------------------------------------

def disk_space_report(home: dict[str, Any]) -> dict[str, Any]:
    cards = home.get("cards") or {}
    storage = cards.get("storage") or {}
    return {
        "available_bytes": storage.get("available_bytes"),
        "total_bytes": storage.get("total_bytes"),
        "models_bytes": storage.get("models_bytes"),
        "staging_count": storage.get("staging_count"),
        "quarantine_count": storage.get("quarantine_count"),
        "pressure_fraction": storage.get("pressure_fraction"),
        "summary": _bytes_summary(storage.get("available_bytes")),
    }


def _bytes_summary(value: Any) -> str:
    if value is None:
        return "free space unknown"
    gib = value / (1024 ** 3)
    return f"{gib:.1f} GiB free"


# --- 3. model fit explanation -------------------------------------------------

def fit_explanation(fit: Any, *, context: int | None = None,
                    slots: int | None = None) -> dict[str, Any]:
    """Plain-language VRAM/RAM/context/slots breakdown from a FitResult."""
    verdict = getattr(fit, "verdict", None) or "UNKNOWN"
    required = getattr(fit, "required_gib", None)
    weights = getattr(fit, "weights_gib", None)
    kv = getattr(fit, "kv_gib", None)
    overhead = getattr(fit, "overhead_gib", None)
    lines = [
        f"Weights: {weights:.2f} GiB" if weights is not None else "Weights: unknown",
        f"KV cache (context × slots): {kv:.2f} GiB"
        if kv is not None else "KV cache: unknown",
        f"Overhead: {overhead:.2f} GiB" if overhead is not None
        else "Overhead: unknown",
        f"Total required: {required:.2f} GiB of fast VRAM"
        if required is not None else "Total required: unknown",
    ]
    if context is not None:
        lines.append(f"Context: {context} tokens")
    if slots is not None:
        lines.append(f"Parallel slots: {slots}")
    return {
        "verdict": verdict,
        "tone": (TONE_GOOD if verdict == "FITS"
                 else TONE_ATTENTION if verdict == "TIGHT"
                 else TONE_BLOCKED),
        "required_gib": required,
        "lines": lines,
        "detail": getattr(fit, "detail", "") or "",
    }


# --- 4. downloaded / installed / active / verified ----------------------------

def model_stage_labels(home: dict[str, Any]) -> dict[str, Any]:
    """Separate the four model lifecycle stages so the UI never conflates
    'downloaded' with 'verified'."""
    cards = home.get("cards") or {}
    model = cards.get("model") or {}
    health = model.get("health") or {}
    state = health.get("effective_state") or health.get("state") or "UNAVAILABLE"

    desired = model.get("desired")
    observed = model.get("observed")
    verified = bool(model.get("verified"))
    installed = observed is not None

    return {
        "desired": desired,
        "downloaded": installed,          # artifact present on disk
        "installed": installed,           # registered installation row
        "active": bool(desired and observed == desired),
        "verified": verified,             # digest-pinned artifact
        "state": state,
        "tone": tone_for_state(state),
        "explanation": _stage_explanation(installed, verified,
                                          bool(desired and observed == desired)),
    }


def _stage_explanation(installed: bool, verified: bool, active: bool) -> str:
    if not installed:
        return "Not downloaded yet."
    parts = ["Downloaded and installed."]
    parts.append("Digest-verified." if verified
                 else "Installed but not digest-verified.")
    parts.append("Currently active." if active
                 else "Not the active model.")
    return " ".join(parts)


# --- 5. recovery instructions after frontend closure --------------------------

def recovery_instructions(active_summary: dict[str, Any]) -> list[str]:
    """Durable next steps when work was interrupted or needs recovery.

    Operations survive frontend closure; these instructions tell the user
    how to resume without re-running anything by hand.
    """
    summary = active_summary or {}
    recovery = int(summary.get("recovery_required_count") or 0)
    running = int(summary.get("running_count") or 0)
    queued = int(summary.get("queued_count") or 0)
    paused = int(summary.get("paused_count") or 0)
    oldest = summary.get("oldest_queued_id")

    lines = [
        "Your work is stored durably; closing this window does not delete it.",
    ]
    if recovery:
        lines.append(
            f"{recovery} operation(s) need recovery. Run: "
            f"bc250 operations recover --confirm")
    if paused:
        lines.append(
            f"{paused} operation(s) are paused. Resume with: "
            f"bc250 operations resume <id>")
    if running or queued:
        lines.append(
            f"{running} running / {queued} queued. A background worker can "
            f"finish them; check progress with: bc250 operations list")
    if oldest:
        lines.append(f"Oldest queued operation: {oldest}")
    if not (recovery or running or queued or paused):
        lines.append("No active operations. Nothing to recover.")
    return lines


# --- 6. safe copy-diagnostic-details ------------------------------------------

def diagnostic_details_text(home: dict[str, Any],
                            doctor: dict[str, Any] | None) -> str:
    """Bounded, redacted text for the copy-diagnostic-details control.

    Uses only the already-redacted home snapshot and doctor report (both
    query-only and secret-free by construction), then applies a hard length
    bound so the clipboard can never receive an unbounded blob.
    """
    lines: list[str] = []
    headline = overall_headline(home)
    lines.append(f"appliance state: {headline['state']}")
    lines.append(f"generated_at: {home.get('generated_at', '')}")
    lines.append("")
    lines.append("cards:")
    for row in home_card_lines(home):
        stale = f" (stale: {row['stale_reason']})" if row["stale"] else ""
        lines.append(f"  {row['name']}: {row['state']}{stale} — {row['evidence']}")
    if doctor:
        lines.append("")
        lines.append(f"doctor overall: {doctor.get('overall', '')}")
        lines.append("doctor findings:")
        for finding in (doctor.get("findings") or [])[:_MAX_FINDINGS_IN_DETAILS]:
            rec = finding.get("recommended_command")
            rec_text = f" → {rec}" if rec else ""
            lines.append(
                f"  [{finding.get('severity')}] {finding.get('id')}: "
                f"{finding.get('title')}{rec_text}")
    text = "\n".join(lines)
    if len(text) > _MAX_DIAGNOSTIC_CHARS:
        text = text[:_MAX_DIAGNOSTIC_CHARS] + "\n…[truncated]"
    return text


# --- 7. link to the exact operation history -----------------------------------

def operation_history_commands(operation_id: str | None) -> list[str]:
    """Exact CLI commands that open the history for a specific failure."""
    if not operation_id:
        return ["bc250 operations list"]
    return [
        f"bc250 operations show {operation_id}",
        f"bc250 operations steps {operation_id}",
        f"bc250 operations events {operation_id}",
    ]
