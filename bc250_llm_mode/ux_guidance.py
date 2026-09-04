"""Pure, bounded guidance shared by the native GUI and CLI.

This module translates durable/runtime state into end-user decisions.  It owns
no files, network calls, subprocesses, Tk widgets, or credentials.  Stable
technical codes remain available as details, while primary copy answers what
happened and what the user can safely do next.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from .problem_details import UNKNOWN_PROBLEM, problem_detail


UX_GUIDANCE_SCHEMA_VERSION = 1
MAX_CONNECTION_STEPS = 12


@dataclass(frozen=True)
class SetupChapterGuidance:
    chapter: int
    title: str
    estimate: str
    change_summary: str
    resumable: bool = True


SETUP_CHAPTER_GUIDANCE = (
    SetupChapterGuidance(
        1, "Machine check", "about 1 minute",
        "Read-only checks confirm the host, memory split, GPU, Vulkan, and storage.",
    ),
    SetupChapterGuidance(
        2, "Safety", "about 1 minute",
        "You review the thermal, firmware, and memory responsibilities before changes are allowed.",
    ),
    SetupChapterGuidance(
        3, "Prepare system", "usually 10–30 minutes",
        "The app prepares current-boot LLM Mode and a verified Vulkan runtime. Administrator approval may be requested.",
    ),
    SetupChapterGuidance(
        4, "Choose workload", "about 2 minutes",
        "You choose a workload goal, model, context, and concurrent-user count before anything downloads.",
    ),
    SetupChapterGuidance(
        5, "Install and verify", "depends on model size and network speed",
        "The model is acquired, validated, started, and tested. Downloads and durable progress can resume.",
    ),
)


def setup_chapter_guidance(index: int) -> SetupChapterGuidance:
    if not 0 <= int(index) < len(SETUP_CHAPTER_GUIDANCE):
        raise ValueError("setup chapter index must be 0..4")
    return SETUP_CHAPTER_GUIDANCE[int(index)]


@dataclass(frozen=True)
class WorkloadPreset:
    preset_id: str
    label: str
    context: int
    slots: int
    summary: str


WORKLOAD_PRESETS = (
    WorkloadPreset(
        "interactive", "Interactive", 8192, 1,
        "Balanced response speed, quality, and memory for one active conversation.",
    ),
    WorkloadPreset(
        "long-context", "Long context", 32768, 1,
        "Keeps more document history for one user and reserves more GPU memory.",
    ),
    WorkloadPreset(
        "shared", "Shared", 8192, 4,
        "Reserves four concurrent slots; a smaller model may fit more comfortably.",
    ),
    WorkloadPreset(
        "cool", "Cool / conservative", 4096, 1,
        "Uses a smaller context target to leave more memory and thermal headroom.",
    ),
)


def workload_preset(value: str) -> WorkloadPreset:
    normalized = str(value).strip().casefold()
    for preset in WORKLOAD_PRESETS:
        if normalized in {preset.preset_id.casefold(), preset.label.casefold()}:
            return preset
    raise ValueError(f"unknown workload preset {value!r}")


_QUANTIZATION_GUIDANCE = {
    "Q4_K_M": "Smaller download and lower memory use; modest quality trade-off.",
    "Q5_K_M": "Balanced quality and size; the usual BC-250 starting point.",
    "Q6_K": "Higher fidelity with a larger download and tighter memory headroom.",
    "Q8_0": "Largest common option; use only when the fit preview remains comfortable.",
}


def quantization_guidance(value: str | None) -> str:
    name = str(value or "").strip().upper()
    if not name:
        return "Choose a quality/size option to calculate fit."
    return _QUANTIZATION_GUIDANCE.get(
        name,
        "Model compression option; compare the displayed size and fit before installing.",
    )


def model_install_time_guidance(size_gib: float | None, *, installed: bool) -> str:
    """Return a deliberately coarse planning range, never a speed promise."""
    if installed:
        return "Already installed; starting and verification usually take a few minutes."
    if not isinstance(size_gib, (int, float)) or not math.isfinite(float(size_gib)):
        return "Download time depends on model size, network speed, and validation."
    if float(size_gib) <= 3.0:
        estimate = "often 5–20 minutes"
    elif float(size_gib) <= 7.0:
        estimate = "often 10–45 minutes"
    else:
        estimate = "often 20–90 minutes"
    return (
        f"Planning range: {estimate}; network speed and validation determine "
        "the actual time."
    )


_STATE_LABELS = {
    "READY": "Ready",
    "VERIFIED": "Verified",
    "ACTIVE": "Running",
    "INSTALLED": "Installed",
    "AVAILABLE": "Available to install",
    "BUSY": "Work in progress",
    "STALE": "Needs a fresh check",
    "UNVERIFIED": "Not verified yet",
    "UNAVAILABLE": "Unavailable",
    "DEGRADED": "Available with limits",
    "BLOCKED": "Blocked for safety",
    "RECOVERY_REQUIRED": "Recovery needs attention",
    "REPAIR_REQUIRED": "Repair needs attention",
    "QUARANTINED": "Held safely in quarantine",
    "DOWNLOADING": "Downloading",
    "VALIDATING": "Checking the model",
    "REMOVING": "Moving to quarantine",
}


def friendly_state(value: Any) -> str:
    normalized = str(value or "UNVERIFIED").strip().upper()
    return _STATE_LABELS.get(
        normalized, normalized.replace("_", " ").strip().title() or "Unknown"
    )


@dataclass(frozen=True)
class ConnectionDoctorStep:
    check_id: str
    label: str
    passed: bool
    result: str
    next_action: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConnectionDoctorView:
    schema_version: int
    ready: bool
    headline: str
    explanation: str
    passed_count: int
    total_count: int
    next_action_label: str | None
    next_action_route: str | None
    technical_code: str | None
    steps: tuple[ConnectionDoctorStep, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["steps"] = [step.to_dict() for step in self.steps]
        return value


_CONNECTION_CHECK_COPY = {
    "model": (
        "Model answers locally", "The selected model is running and verified.",
        "Start and verify the selected model.", "Open Models", "models",
    ),
    "gateway": (
        "Private model API is ready", "The authenticated API can reach the model.",
        "Start or repair the private model API.", "Review Connections", "connections",
    ),
    "credential": (
        "A named client key is ready", "At least one independently revocable client key is available.",
        "Create a separate key for this device or app.", "Create client key", "connections",
    ),
    "local-authorized": (
        "The key can list this model", "An authenticated local request saw the selected public model name.",
        "Run the selected client's connection test.", "Run connection test", "connections",
    ),
    "local-unauthorized": (
        "Requests without a key are blocked", "The local API refused an unauthenticated request.",
        "Run the required no-key safety test.", "Run connection test", "connections",
    ),
    "tailscale-dns": (
        "This BC-250 has a private network name", "Tailscale is connected and supplied a tailnet DNS name.",
        "Connect Tailscale on this BC-250.", "Review private network", "connections",
    ),
    "serve-mappings": (
        "Private HTTPS addresses point to the right services", "The browser UI and model API mappings match the reviewed topology.",
        "Publish or repair the private HTTPS mappings.", "Repair private sharing", "connections",
    ),
    "funnel-disabled": (
        "Public internet access is off", "Tailscale Funnel is disabled for the app endpoints.",
        "Disable public Funnel exposure before continuing.", "Disable public access", "connections",
    ),
    "tailnet-unauthorized": (
        "Remote requests without a key are blocked", "The tailnet API refused an unauthenticated request.",
        "Run the required remote no-key safety test.", "Run connection test", "connections",
    ),
    "tailnet-models": (
        "The remote key sees this model", "The authenticated tailnet model list contains the selected public name.",
        "Test that the selected key can list this model at the exact Base URL.", "Run connection test", "connections",
    ),
    "tailnet-stream": (
        "A remote response can stream", "A bounded authenticated response reached a normal stream event.",
        "Test streaming with the selected client key.", "Run connection test", "connections",
    ),
}


_PROBLEM_ROUTE = {
    "choose-model": "models",
    "start-model": "models",
    "reconcile-model": "system",
    "check-model": "system",
    "view-activity": "activity",
    "reduce-request": "profiles",
}


def connection_doctor(snapshot: Mapping[str, Any]) -> ConnectionDoctorView:
    raw_checks = snapshot.get("checks")
    checks = raw_checks if isinstance(raw_checks, (list, tuple)) else ()
    steps: list[ConnectionDoctorStep] = []
    first_failed: tuple[str, str, str] | None = None
    for item in checks[:MAX_CONNECTION_STEPS]:
        if not isinstance(item, Mapping):
            continue
        check_id = str(item.get("id") or "unknown")
        passed = bool(item.get("passed"))
        label, success, failure, action_label, route = _CONNECTION_CHECK_COPY.get(
            check_id,
            (
                check_id.replace("-", " ").title(),
                "This check passed.",
                str(item.get("next_action") or "Review this connection check."),
                "Review Connections",
                "connections",
            ),
        )
        result = success if passed else failure
        next_action = None if passed else str(item.get("next_action") or failure)
        steps.append(ConnectionDoctorStep(check_id, label, passed, result, next_action))
        if not passed and first_failed is None:
            first_failed = (action_label, route, failure)

    readiness = snapshot.get("readiness")
    readiness = readiness if isinstance(readiness, Mapping) else {}
    ready = bool(readiness.get("remote_client_ready", snapshot.get("ready")))
    problem_code = str(readiness.get("primary_problem_code") or "").strip() or None
    problem = problem_detail(problem_code or "") if problem_code else None
    if ready:
        headline = "Connection verified"
        explanation = (
            "The selected model, named client key, private HTTPS address, and "
            "streaming response passed the guided checks."
        )
        action_label = route = None
    elif problem is not None and problem != UNKNOWN_PROBLEM:
        headline = problem.title
        explanation = problem.user_message
        action_label = problem.safe_action_label
        route = _PROBLEM_ROUTE.get(problem.safe_action_id, "connections")
    elif first_failed is not None:
        action_label, route, explanation = first_failed
        headline = "One connection step needs attention"
    else:
        headline = "Connection status is unavailable"
        explanation = "Refresh the page, then run guided setup for the device or app you want to connect."
        action_label, route = "Review Connections", "connections"

    return ConnectionDoctorView(
        schema_version=UX_GUIDANCE_SCHEMA_VERSION,
        ready=ready,
        headline=headline,
        explanation=explanation,
        passed_count=sum(1 for step in steps if step.passed),
        total_count=len(steps),
        next_action_label=action_label,
        next_action_route=route,
        technical_code=problem_code,
        steps=tuple(steps),
    )


@dataclass(frozen=True)
class HTTPStatusGuidance:
    status: int
    title: str
    explanation: str
    action: str
    technical_code: str


def http_status_guidance(
    status: int, *, problem_code: str | None = None
) -> HTTPStatusGuidance:
    code = str(problem_code or "").strip().upper()
    defaults = {
        401: (
            "AUTH_INVALID", "The app did not receive an active key for this client.",
            "Copy a newly created or rotated key into the client, with no extra spaces.",
        ),
        403: (
            "SCOPE_NOT_GRANTED", "The key was recognized but this request is not permitted.",
            "Check the key's model permissions and confirm the client is using Chat Completions at the displayed /v1 Base URL.",
        ),
        404: (
            "ENDPOINT_UNSUPPORTED", "The client requested an address this model API does not provide.",
            "Use the displayed Base URL ending once in /v1 and the Chat Completions endpoint.",
        ),
        502: (
            "UPSTREAM_FAILED", "The private address responded, but the selected model did not complete the request.",
            "Open Models or System, start and verify the selected model, then retry the connection test.",
        ),
    }
    fallback = (
        "UNKNOWN_PROBLEM", "The connection did not complete successfully.",
        "Run Connection Doctor and review the first step that needs attention.",
    )
    default_code, explanation, action = defaults.get(int(status), fallback)
    selected = code or default_code
    problem = problem_detail(selected)
    title = problem.title if problem != UNKNOWN_PROBLEM else f"Connection returned HTTP {int(status)}"
    if code and problem != UNKNOWN_PROBLEM:
        explanation = problem.user_message
        action = problem.safe_action_label
    return HTTPStatusGuidance(int(status), title, explanation, action, selected)


@dataclass(frozen=True)
class OperationReasonGuidance:
    title: str
    explanation: str
    next_step: str


_OPERATION_REASONS = {
    "SIGNED_UPDATE_CHANNEL_UNAVAILABLE": OperationReasonGuidance(
        "Online updates are not configured in this build",
        "The app refused to use an untrusted package, branch, or arbitrary download as an update.",
        "Use a signed offline bundle when one is supplied by the project owner.",
    ),
    "CHANNEL_FETCH_FAILED": OperationReasonGuidance(
        "The signed update source could not be reached",
        "No installed files were changed.", "Check the network and try the explicit update check again.",
    ),
    "VERSION_NOT_FOUND": OperationReasonGuidance(
        "That signed version is not available",
        "The requested version was not present in the verified release source.",
        "Run Check signed channel and select the version it reports.",
    ),
    "INSUFFICIENT_SPACE": OperationReasonGuidance(
        "More storage is required",
        "The verified update cannot be staged with the currently available free space.",
        "Preview storage cleanup, then build a fresh update preview.",
    ),
    "VERIFIED_BACKUP_REQUIRED": OperationReasonGuidance(
        "A verified backup is required",
        "The app will not replace the active slot without recoverable profile evidence.",
        "Create or repair the backup, then preview the update again.",
    ),
    "PREVIEW_STALE": OperationReasonGuidance(
        "The preview is out of date", "State changed after the preview was created.",
        "Build a new preview and review it before confirming.",
    ),
    "UPDATE_BUSY": OperationReasonGuidance(
        "Another protected task is running", "The update cannot safely share its required resources.",
        "Open Activity, wait for or safely stop the current task, then retry.",
    ),
    "SIGNATURE_INVALID": OperationReasonGuidance(
        "The update signature was rejected", "The bundle was not trusted and nothing was installed.",
        "Obtain a new signed bundle from the project owner; do not bypass verification.",
    ),
    "CHECKSUM_MISMATCH": OperationReasonGuidance(
        "The update bundle is damaged or changed", "A file did not match the signed inventory.",
        "Discard this bundle and obtain a fresh signed copy.",
    ),
    "BUNDLE_MALFORMED": OperationReasonGuidance(
        "This is not a supported update bundle", "The archive did not match the closed signed-bundle format.",
        "Choose the original signed bundle without extracting or repacking it.",
    ),
    "ROLLBACK_UNAVAILABLE": OperationReasonGuidance(
        "No verified rollback is available", "The app cannot prove a safe prior slot and profile pairing.",
        "Keep the current installation and create a redacted support bundle if recovery is needed.",
    ),
    "RECOVERY_BARRIER_MANUAL": OperationReasonGuidance(
        "Update recovery needs attention", "The app stopped rather than guess which slot or profile is current.",
        "Open Activity and follow the recorded recovery action.",
    ),
}


def operation_reason_guidance(code: Any) -> OperationReasonGuidance:
    normalized = str(getattr(code, "value", code) or "UNKNOWN").strip().upper()
    return _OPERATION_REASONS.get(
        normalized,
        OperationReasonGuidance(
            "The action stopped safely",
            "The requested change was not applied or could not be fully verified.",
            "Open Technical details for the stable reason code, then follow the page's safe next action.",
        ),
    )


def repair_duration_label(value: Any) -> str:
    return {
        "INSTANT": "usually under a minute",
        "SHORT": "usually a few minutes",
        "LONG": "may take several minutes",
    }.get(str(value or "").upper(), "duration depends on current state")


def repair_reversibility_label(value: Any, *, prior_state_survives: bool = True) -> str:
    normalized = str(value or "").upper()
    if normalized == "EXACT_UNTIL":
        return "Undo is available until the displayed retention deadline."
    if normalized == "COMPENSATED_BY_OWNER":
        return (
            "The prior working state is retained for owner-guided recovery."
            if prior_state_survives else
            "The typed owner verifies recovery and stops if it cannot prove a safe state."
        )
    return "This change has no automatic Undo; review the preview carefully."


__all__ = [
    "ConnectionDoctorStep", "ConnectionDoctorView", "HTTPStatusGuidance",
    "MAX_CONNECTION_STEPS", "OperationReasonGuidance", "SETUP_CHAPTER_GUIDANCE",
    "SetupChapterGuidance", "UX_GUIDANCE_SCHEMA_VERSION", "WORKLOAD_PRESETS",
    "WorkloadPreset", "connection_doctor", "friendly_state",
    "http_status_guidance", "operation_reason_guidance",
    "quantization_guidance", "repair_duration_label",
    "repair_reversibility_label", "setup_chapter_guidance", "workload_preset",
]
