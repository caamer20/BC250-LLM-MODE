"""Pure appliance readiness reduction for every user-facing status surface.

The reducer consumes bounded observations.  It never probes, starts, stops, or
persists anything.  A caller may therefore use it from Home, Connections,
Doctor, CLI, and support-bundle projections without creating another source of
runtime truth.

Three different claims are intentionally visible:

``process_ready``
    The owning process/container/service is present and active.
``protocol_ready``
    A current bounded request reached the expected local protocol and identity.
``journey_ready``
    The intended user journey, including authentication and streaming where
    applicable, has current verification evidence.

Process state alone can never reduce to ``READY`` when a stronger level is
required by that component.
"""

from __future__ import annotations

import datetime as _datetime
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping


READINESS_SNAPSHOT_SCHEMA_VERSION = 1
LIVE_OBSERVATION_MAX_AGE_SECONDS = 10
JOURNEY_VERIFICATION_MAX_AGE_SECONDS = 5 * 60
MAX_COMPONENTS = 16
MAX_TEXT_LENGTH = 240


class ReadinessState(StrEnum):
    """Closed appliance-facing readiness vocabulary."""

    ABSENT = "ABSENT"
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


READINESS_STATES = tuple(state.value for state in ReadinessState)


@dataclass(frozen=True)
class ComponentReadiness:
    component_id: str
    state: ReadinessState
    process_ready: bool
    protocol_ready: bool
    journey_ready: bool
    observed_identity: str | None
    expected_identity: str | None
    observed_at: str | None
    fresh_until: str | None
    problem_code: str | None
    action_id: str | None
    summary: str
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


@dataclass(frozen=True)
class ApplianceReadinessSnapshot:
    schema_version: int
    generated_at: str
    target_journey: str
    overall_state: ReadinessState
    primary_problem_code: str | None
    primary_action: str | None
    native_chat_ready: bool
    openwebui_ready: bool
    remote_client_ready: bool
    components: tuple[ComponentReadiness, ...]

    def component(self, component_id: str) -> ComponentReadiness:
        for component in self.components:
            if component.component_id == component_id:
                return component
        raise KeyError(component_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "target_journey": self.target_journey,
            "overall_state": self.overall_state.value,
            "primary_problem_code": self.primary_problem_code,
            "primary_action": self.primary_action,
            "native_chat_ready": self.native_chat_ready,
            "openwebui_ready": self.openwebui_ready,
            "remote_client_ready": self.remote_client_ready,
            "components": {
                component.component_id: component.to_dict()
                for component in self.components[:MAX_COMPONENTS]
            },
        }


def _parse_timestamp(value: Any) -> _datetime.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = _datetime.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone(_datetime.timezone.utc)


def _iso(value: _datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _freshness(
    observed_at: Any,
    *,
    generated_at: str,
    max_age_seconds: int,
) -> tuple[bool, str | None, str | None]:
    observed = _parse_timestamp(observed_at)
    now = _parse_timestamp(generated_at)
    if observed is None or now is None:
        return False, None, None
    fresh_until = observed + _datetime.timedelta(seconds=max_age_seconds)
    # A timestamp far in the future is also not credible current evidence.
    fresh = observed <= now + _datetime.timedelta(seconds=1) and now <= fresh_until
    return fresh, _iso(observed), _iso(fresh_until)


def _text(value: Any, *, identity: bool = False) -> str | None:
    if value is None:
        return None
    candidate = " ".join(str(value).strip().split())[:MAX_TEXT_LENGTH]
    if not candidate:
        return None
    lowered = candidate.lower()
    if identity and (
        candidate.startswith(("/", "~"))
        or "\\" in candidate
        or lowered.endswith(".gguf")
        or lowered.startswith(("bearer ", "hf_", "ghp_"))
    ):
        return None
    return candidate


def _bool(source: Mapping[str, Any], *names: str, default: bool = False) -> bool:
    for name in names:
        if name in source:
            return bool(source.get(name))
    return default


def _component(
    component_id: str,
    state: ReadinessState,
    *,
    process_ready: bool = False,
    protocol_ready: bool = False,
    journey_ready: bool = False,
    observed_identity: Any = None,
    expected_identity: Any = None,
    observed_at: str | None = None,
    fresh_until: str | None = None,
    problem_code: str | None = None,
    action_id: str | None = None,
    summary: str,
    optional: bool = False,
) -> ComponentReadiness:
    return ComponentReadiness(
        component_id=component_id,
        state=state,
        process_ready=bool(process_ready),
        protocol_ready=bool(protocol_ready),
        journey_ready=bool(journey_ready),
        observed_identity=_text(observed_identity, identity=True),
        expected_identity=_text(expected_identity, identity=True),
        observed_at=observed_at,
        fresh_until=fresh_until,
        problem_code=_text(problem_code),
        action_id=_text(action_id),
        summary=_text(summary) or "No current readiness evidence.",
        optional=optional,
    )


def _model_readiness(
    source: Mapping[str, Any], generated_at: str,
) -> ComponentReadiness:
    installed = _bool(source, "installed", default=bool(
        source.get("public_alias") or source.get("model_id")
    ))
    process = _bool(source, "process_active", "service_active", "active")
    protocol = _bool(source, "protocol_ready", "healthy")
    expected = source.get("expected_identity") or source.get("desired_identity")
    observed = (
        source.get("observed_identity")
        or source.get("public_alias")
        or source.get("model_id")
    )
    expected_clean = _text(expected, identity=True)
    observed_clean = _text(observed, identity=True)
    identity_matches = (
        _bool(source, "identity_matches", default=True)
        and (not expected_clean or expected_clean == observed_clean)
    )
    fresh, observed_at, fresh_until = _freshness(
        source.get("observed_at") or generated_at,
        generated_at=generated_at,
        max_age_seconds=LIVE_OBSERVATION_MAX_AGE_SECONDS,
    )
    if not installed:
        return _component(
            "model", ReadinessState.ABSENT, observed_identity=observed,
            expected_identity=expected, observed_at=observed_at,
            fresh_until=fresh_until, problem_code="MODEL_ABSENT",
            action_id="models/browse", summary="No usable model is installed.")
    if _bool(source, "starting"):
        return _component(
            "model", ReadinessState.STARTING, process_ready=process,
            observed_identity=observed, expected_identity=expected,
            observed_at=observed_at, fresh_until=fresh_until,
            problem_code="MODEL_STARTING", action_id="activity/open",
            summary="The selected model is starting.")
    if not process:
        return _component(
            "model", ReadinessState.STOPPED, observed_identity=observed,
            expected_identity=expected, observed_at=observed_at,
            fresh_until=fresh_until, problem_code="MODEL_STOPPED",
            action_id="model/start", summary="The selected model is stopped.")
    if not identity_matches:
        return _component(
            "model", ReadinessState.BLOCKED, process_ready=True,
            protocol_ready=protocol and fresh, observed_identity=observed,
            expected_identity=expected, observed_at=observed_at,
            fresh_until=fresh_until, problem_code="MODEL_IDENTITY_MISMATCH",
            action_id="model/reconcile",
            summary="The running server does not match the selected model configuration.")
    if not protocol or not fresh:
        code = "MODEL_OBSERVATION_STALE" if protocol else "MODEL_PROTOCOL_UNAVAILABLE"
        return _component(
            "model", ReadinessState.DEGRADED, process_ready=True,
            protocol_ready=False, observed_identity=observed,
            expected_identity=expected, observed_at=observed_at,
            fresh_until=fresh_until, problem_code=code,
            action_id="checks/run", summary=(
                "The model process is active, but its current protocol identity is not verified."
            ))
    return _component(
        "model", ReadinessState.READY, process_ready=True,
        protocol_ready=True, journey_ready=_bool(source, "chat_verified"),
        observed_identity=observed, expected_identity=expected,
        observed_at=observed_at, fresh_until=fresh_until,
        summary="The selected model protocol and identity are current.")


def _gateway_readiness(
    source: Mapping[str, Any], generated_at: str,
) -> ComponentReadiness:
    metadata = _bool(source, "credential_metadata_ready", default=bool(
        source.get("enabled") and int(source.get("ready_clients") or 0) > 0
    ))
    installed = _bool(
        source, "service_installed", "installed",
        default=bool(source.get("healthy") or source.get("service_active")),
    )
    process = _bool(source, "service_active", "process_active", "active")
    listeners = _bool(source, "listeners_ready", default=bool(source.get("healthy")))
    backend = _bool(
        source, "backend_identity_verified", default=bool(source.get("healthy")))
    protocol = listeners and backend
    fresh, observed_at, fresh_until = _freshness(
        source.get("observed_at") or generated_at,
        generated_at=generated_at,
        max_age_seconds=LIVE_OBSERVATION_MAX_AGE_SECONDS,
    )
    journey = _bool(source, "authenticated_sse_verified")
    if not installed:
        return _component(
            "gateway", ReadinessState.ABSENT, problem_code="GATEWAY_NOT_INSTALLED",
            action_id="connections/enable", observed_at=observed_at,
            fresh_until=fresh_until, summary="The authenticated gateway is not installed.",
            optional=True)
    if not process:
        return _component(
            "gateway", ReadinessState.STOPPED, problem_code="GATEWAY_STOPPED",
            action_id="connections/enable", observed_at=observed_at,
            fresh_until=fresh_until, summary="The authenticated gateway service is stopped.",
            optional=True)
    if not metadata:
        return _component(
            "gateway", ReadinessState.BLOCKED, process_ready=True,
            problem_code="GATEWAY_CREDENTIAL_REQUIRED",
            action_id="connections/add-client", observed_at=observed_at,
            fresh_until=fresh_until,
            summary="The gateway is active but has no usable client credential.",
            optional=True)
    if not protocol or not fresh:
        code = "GATEWAY_OBSERVATION_STALE" if protocol else "GATEWAY_PROTOCOL_UNAVAILABLE"
        return _component(
            "gateway", ReadinessState.DEGRADED, process_ready=True,
            protocol_ready=False, journey_ready=False, problem_code=code,
            action_id="checks/run", observed_at=observed_at,
            fresh_until=fresh_until,
            summary="The gateway process is active, but its private listener or backend identity is not ready.",
            optional=True)
    return _component(
        "gateway", ReadinessState.READY, process_ready=True,
        protocol_ready=True, journey_ready=journey, observed_at=observed_at,
        fresh_until=fresh_until,
        summary=("Authenticated gateway streaming is verified."
                 if journey else "The authenticated gateway protocol is ready."),
        optional=True)


def _openwebui_readiness(
    source: Mapping[str, Any], generated_at: str,
) -> ComponentReadiness:
    installed = _bool(source, "installed", "available")
    process = _bool(source, "process_active", "running")
    http_ready = _bool(source, "http_ready", "http_responsive", "healthy")
    provider = _bool(source, "provider_configured", "gateway_provider_configured")
    visible = _bool(source, "expected_model_visible", "model_visible")
    verified = _bool(source, "end_to_end_verified", "chat_verified")
    fresh, observed_at, fresh_until = _freshness(
        source.get("observed_at") or generated_at,
        generated_at=generated_at,
        max_age_seconds=LIVE_OBSERVATION_MAX_AGE_SECONDS,
    )
    if not installed:
        return _component(
            "openwebui", ReadinessState.ABSENT, problem_code="OPENWEBUI_NOT_INSTALLED",
            action_id="openwebui/enable", observed_at=observed_at,
            fresh_until=fresh_until, summary="Open WebUI is not installed.", optional=True)
    if _bool(source, "starting"):
        return _component(
            "openwebui", ReadinessState.STARTING, process_ready=process,
            problem_code="OPENWEBUI_STARTING", action_id="activity/open",
            observed_at=observed_at, fresh_until=fresh_until,
            summary="Open WebUI is starting.", optional=True)
    if not process:
        return _component(
            "openwebui", ReadinessState.STOPPED,
            problem_code="OPENWEBUI_STOPPED", action_id="openwebui/enable",
            observed_at=observed_at, fresh_until=fresh_until,
            summary="Open WebUI is stopped.", optional=True)
    if not http_ready or not fresh:
        return _component(
            "openwebui", ReadinessState.DEGRADED, process_ready=True,
            problem_code=("OPENWEBUI_OBSERVATION_STALE" if http_ready
                          else "OPENWEBUI_HTTP_UNAVAILABLE"),
            action_id="checks/run", observed_at=observed_at,
            fresh_until=fresh_until,
            summary="The Open WebUI container is running, but its local HTTP service is not ready.",
            optional=True)
    if not provider or not visible:
        return _component(
            "openwebui", ReadinessState.BLOCKED, process_ready=True,
            protocol_ready=True, problem_code=(
                "OPENWEBUI_PROVIDER_NOT_CONFIGURED" if not provider
                else "OPENWEBUI_MODEL_NOT_VISIBLE"),
            action_id="openwebui/reconcile", observed_at=observed_at,
            fresh_until=fresh_until,
            summary="Open WebUI is responsive, but its expected gateway provider or model is missing.",
            optional=True)
    if not verified:
        return _component(
            "openwebui", ReadinessState.DEGRADED, process_ready=True,
            protocol_ready=True, problem_code="OPENWEBUI_CHAT_UNVERIFIED",
            action_id="checks/run", observed_at=observed_at,
            fresh_until=fresh_until,
            summary="Open WebUI is configured; an end-to-end chat check is still required.",
            optional=True)
    return _component(
        "openwebui", ReadinessState.READY, process_ready=True,
        protocol_ready=True, journey_ready=True, observed_at=observed_at,
        fresh_until=fresh_until,
        summary="Open WebUI completed an end-to-end chat check.", optional=True)


def _tailscale_readiness(
    source: Mapping[str, Any], generated_at: str,
) -> ComponentReadiness:
    installed = _bool(source, "installed", "available")
    process = _bool(source, "process_active", "daemon_active", "active")
    connected = _bool(source, "protocol_ready", "connected")
    dns_name = _text(source.get("dns_name"), identity=True)
    fresh, observed_at, fresh_until = _freshness(
        source.get("observed_at") or generated_at,
        generated_at=generated_at,
        max_age_seconds=LIVE_OBSERVATION_MAX_AGE_SECONDS,
    )
    if not installed:
        state, code, action, summary = (
            ReadinessState.ABSENT, "TAILSCALE_NOT_INSTALLED", "connections/open",
            "Tailscale is not installed.")
    elif not process:
        state, code, action, summary = (
            ReadinessState.STOPPED, "TAILSCALE_STOPPED", "connections/open",
            "Tailscale is stopped.")
    elif not connected or not dns_name or not fresh:
        state, code, action, summary = (
            ReadinessState.DEGRADED,
            "TAILSCALE_OBSERVATION_STALE" if connected and dns_name else "TAILSCALE_DISCONNECTED",
            "connections/open",
            "Tailscale is active, but no current private DNS connection is ready.")
    else:
        state, code, action, summary = (
            ReadinessState.READY, None, None,
            "The private tailnet connection and DNS name are current.")
    return _component(
        "tailscale", state, process_ready=process,
        protocol_ready=state is ReadinessState.READY,
        observed_identity=dns_name, observed_at=observed_at,
        fresh_until=fresh_until, problem_code=code, action_id=action,
        summary=summary, optional=True)


def _serve_readiness(
    source: Mapping[str, Any], generated_at: str,
) -> ComponentReadiness:
    mappings = _bool(source, "mappings_exact", default=bool(
        source.get("webui_mapping_exact") and source.get("api_mapping_exact")
    ))
    funnel_disabled = _bool(
        source, "funnel_disabled", default=source.get("public_funnel") is False)
    fresh, observed_at, fresh_until = _freshness(
        source.get("observed_at") or generated_at,
        generated_at=generated_at,
        max_age_seconds=LIVE_OBSERVATION_MAX_AGE_SECONDS,
    )
    if source.get("public_funnel") is True:
        state, code, summary = (
            ReadinessState.BLOCKED, "PUBLIC_FUNNEL_ENABLED",
            "Public Funnel exposure is enabled and must be removed.")
    elif not mappings:
        state, code, summary = (
            ReadinessState.STOPPED, "SERVE_MAPPING_MISSING",
            "The reviewed private Serve mappings are missing.")
    elif not funnel_disabled:
        state, code, summary = (
            ReadinessState.UNKNOWN, "FUNNEL_STATE_UNKNOWN",
            "Public exposure could not be ruled out.")
    elif not fresh:
        state, code, summary = (
            ReadinessState.DEGRADED, "SERVE_OBSERVATION_STALE",
            "The private Serve mapping observation is stale.")
    else:
        state, code, summary = (
            ReadinessState.READY, None,
            "The reviewed private Serve mappings are current and Funnel is disabled.")
    return _component(
        "serve", state, process_ready=mappings,
        protocol_ready=state is ReadinessState.READY,
        observed_at=observed_at, fresh_until=fresh_until,
        problem_code=code, action_id=None if code is None else "sharing/reconcile",
        summary=summary, optional=True)


def _verification_readiness(
    source: Mapping[str, Any], *, generated_at: str,
    dependencies_ready: bool,
) -> ComponentReadiness:
    required = (
        "local_unauthorized", "local_authorized", "tailnet_unauthorized",
        "tailnet_authorized_models", "tailnet_stream",
    )
    passed = all(source.get(key) == "passed" for key in required)
    fresh, observed_at, fresh_until = _freshness(
        source.get("observed_at"), generated_at=generated_at,
        max_age_seconds=JOURNEY_VERIFICATION_MAX_AGE_SECONDS,
    )
    expected_identity = source.get("dependency_identity")
    verified_identity = source.get("verified_dependency_identity")
    identity_current = (
        not expected_identity or expected_identity == verified_identity
    )
    if not dependencies_ready:
        state, code, summary = (
            ReadinessState.BLOCKED, "REMOTE_DEPENDENCY_NOT_READY",
            "A required model, gateway, tailnet, or Serve dependency is not ready.")
    elif expected_identity and not identity_current:
        state, code, summary = (
            ReadinessState.BLOCKED, "CLIENT_VERIFICATION_INVALIDATED",
            "A dependency changed after the last client verification.")
    elif not fresh:
        state, code, summary = (
            ReadinessState.DEGRADED, "CLIENT_VERIFICATION_STALE",
            "The end-to-end client verification is missing or stale.")
    elif not passed:
        state, code, summary = (
            ReadinessState.DEGRADED, "CLIENT_VERIFICATION_FAILED",
            "The authenticated positive, negative, and streaming checks did not all pass.")
    else:
        state, code, summary = (
            ReadinessState.READY, None,
            "Authentication, model discovery, streaming, and completion were verified end to end.")
    return _component(
        "client_verification", state,
        process_ready=dependencies_ready,
        protocol_ready=dependencies_ready and passed,
        journey_ready=state is ReadinessState.READY,
        observed_identity=verified_identity, expected_identity=expected_identity,
        observed_at=observed_at, fresh_until=fresh_until,
        problem_code=code, action_id=None if code is None else "checks/run",
        summary=summary, optional=True)


def _first_problem(
    components: Iterable[ComponentReadiness],
) -> ComponentReadiness | None:
    rows = tuple(components)
    for state in (
        ReadinessState.BLOCKED,
        ReadinessState.ABSENT,
        ReadinessState.STOPPED,
        ReadinessState.DEGRADED,
        ReadinessState.STARTING,
        ReadinessState.UNKNOWN,
    ):
        for component in rows:
            if component.state is state:
                return component
    return None


def build_appliance_readiness(
    *,
    generated_at: str,
    model: Mapping[str, Any],
    gateway: Mapping[str, Any] | None = None,
    openwebui: Mapping[str, Any] | None = None,
    tailscale: Mapping[str, Any] | None = None,
    serve: Mapping[str, Any] | None = None,
    client_verification: Mapping[str, Any] | None = None,
    thermal: Mapping[str, Any] | None = None,
    recovery: Mapping[str, Any] | None = None,
    target_journey: str = "native_chat",
) -> ApplianceReadinessSnapshot:
    """Reduce current observations to one deterministic, bounded snapshot."""
    if target_journey not in {"native_chat", "openwebui", "remote_client"}:
        raise ValueError("unknown target journey")

    model_component = _model_readiness(model, generated_at)
    gateway_component = _gateway_readiness(gateway or {}, generated_at)
    openwebui_component = _openwebui_readiness(openwebui or {}, generated_at)
    tailscale_component = _tailscale_readiness(tailscale or {}, generated_at)
    serve_component = _serve_readiness(serve or {}, generated_at)
    dependencies_ready = all(
        component.state is ReadinessState.READY
        for component in (
            model_component, gateway_component, tailscale_component, serve_component)
    )
    verification_component = _verification_readiness(
        client_verification or {}, generated_at=generated_at,
        dependencies_ready=dependencies_ready,
    )

    safety_blocked = _bool(thermal or {}, "blocked", "latched")
    safety = _component(
        "thermal", ReadinessState.BLOCKED if safety_blocked else ReadinessState.READY,
        process_ready=True, protocol_ready=not safety_blocked,
        journey_ready=not safety_blocked,
        observed_at=_text((thermal or {}).get("observed_at") or generated_at),
        problem_code="THERMAL_SAFETY_BLOCK" if safety_blocked else None,
        action_id="system/thermal" if safety_blocked else None,
        summary=("Thermal safety blocks model work." if safety_blocked
                 else "No thermal safety block is active."),
    )
    recovery_required = _bool(recovery or {}, "required", "recovery_required")
    recovery_component = _component(
        "recovery", ReadinessState.BLOCKED if recovery_required else ReadinessState.READY,
        process_ready=True, protocol_ready=not recovery_required,
        journey_ready=not recovery_required,
        observed_at=_text((recovery or {}).get("observed_at") or generated_at),
        problem_code="RECOVERY_REQUIRED" if recovery_required else None,
        action_id="activity/recovery" if recovery_required else None,
        summary=("A durable operation requires recovery." if recovery_required
                 else "No durable recovery action is required."),
    )

    core_ready = all(
        component.state is ReadinessState.READY
        for component in (safety, recovery_component, model_component)
    )
    # Native Chat needs current completion evidence, not merely a listening
    # model protocol.  Remote verification carries its own authenticated
    # streaming completion proof and therefore does not depend on this local
    # journey bit.
    native_ready = core_ready and model_component.journey_ready
    openwebui_ready = core_ready and all(
        component.state is ReadinessState.READY
        for component in (gateway_component, openwebui_component)
    )
    remote_ready = core_ready and verification_component.state is ReadinessState.READY

    target_components = {
        "native_chat": (safety, recovery_component, model_component),
        "openwebui": (
            safety, recovery_component, model_component,
            gateway_component, openwebui_component,
        ),
        "remote_client": (
            safety, recovery_component, model_component, gateway_component,
            tailscale_component, serve_component, verification_component,
        ),
    }[target_journey]
    primary = _first_problem(target_components)
    if target_journey == "native_chat" and primary is None and not native_ready:
        primary = _component(
            "native_chat", ReadinessState.DEGRADED,
            process_ready=model_component.process_ready,
            protocol_ready=model_component.protocol_ready,
            journey_ready=False,
            observed_at=model_component.observed_at,
            fresh_until=model_component.fresh_until,
            problem_code="NATIVE_CHAT_UNVERIFIED", action_id="checks/run",
            summary="The model protocol is ready; a current local completion check is required.",
        )
    overall = ReadinessState.READY if primary is None else primary.state
    components = (
        safety, recovery_component, model_component, gateway_component,
        openwebui_component, tailscale_component, serve_component,
        verification_component,
    )
    return ApplianceReadinessSnapshot(
        schema_version=READINESS_SNAPSHOT_SCHEMA_VERSION,
        generated_at=generated_at,
        target_journey=target_journey,
        overall_state=overall,
        primary_problem_code=primary.problem_code if primary else None,
        primary_action=primary.action_id if primary else None,
        native_chat_ready=native_ready,
        openwebui_ready=openwebui_ready,
        remote_client_ready=remote_ready,
        components=components,
    )


def _health_state(card: Mapping[str, Any]) -> str:
    health = card.get("health")
    if not isinstance(health, Mapping):
        return "UNKNOWN"
    return str(health.get("effective_state") or health.get("state") or "UNKNOWN")


def readiness_from_snapshots(
    *,
    home: Mapping[str, Any],
    connection: Mapping[str, Any],
    target_journey: str = "native_chat",
) -> ApplianceReadinessSnapshot:
    """Project the existing bounded Home and Connections observations.

    This compatibility composer lets every frontend adopt the new contract
    before older snapshot fields are removed.  It remains pure: callers own
    collection of both source snapshots.
    """
    generated_at = str(connection.get("generated_at") or home.get("generated_at") or "")
    cards = home.get("cards") if isinstance(home.get("cards"), Mapping) else {}
    model_card = cards.get("model") if isinstance(cards.get("model"), Mapping) else {}
    inference_card = (
        cards.get("inference") if isinstance(cards.get("inference"), Mapping) else {}
    )
    thermal_card = (
        cards.get("thermal") if isinstance(cards.get("thermal"), Mapping) else {}
    )
    operations_card = (
        cards.get("operations") if isinstance(cards.get("operations"), Mapping) else {}
    )
    connection_model = (
        connection.get("model")
        if isinstance(connection.get("model"), Mapping) else {}
    )
    gateway = (
        connection.get("gateway")
        if isinstance(connection.get("gateway"), Mapping) else {}
    )
    openwebui = (
        connection.get("openwebui")
        if isinstance(connection.get("openwebui"), Mapping) else {}
    )
    tailscale = (
        connection.get("tailscale")
        if isinstance(connection.get("tailscale"), Mapping) else {}
    )
    sharing = (
        connection.get("sharing")
        if isinstance(connection.get("sharing"), Mapping) else {}
    )
    probes = (
        connection.get("probes")
        if isinstance(connection.get("probes"), Mapping) else {}
    )
    inference_ready = (
        _health_state(inference_card) == "READY"
        and not bool(inference_card.get("stale"))
    )
    operations_state = _health_state(operations_card)
    thermal_state = _health_state(thermal_card)
    return build_appliance_readiness(
        generated_at=generated_at,
        target_journey=target_journey,
        model={
            **connection_model,
            "installed": bool(
                connection_model.get("public_alias")
                or int(model_card.get("installed_count") or 0) > 0
            ),
            "service_active": bool(connection_model.get("service_active")),
            "protocol_ready": bool(connection_model.get("healthy")),
            "expected_identity": (
                connection_model.get("expected_identity")
                or model_card.get("desired")
                or connection_model.get("public_alias")
            ),
            "observed_identity": (
                connection_model.get("observed_identity")
                or connection_model.get("public_alias")
            ),
            "identity_matches": connection_model.get("identity_matches", True),
            "chat_verified": inference_ready,
            "observed_at": connection_model.get("observed_at") or generated_at,
        },
        gateway=gateway,
        openwebui=openwebui,
        tailscale=tailscale,
        serve={
            **sharing,
            "mappings_exact": bool(
                sharing.get("webui_mapping_exact")
                and sharing.get("api_mapping_exact")
            ),
            "funnel_disabled": sharing.get("public_funnel") is False,
            "observed_at": sharing.get("observed_at") or generated_at,
        },
        client_verification=probes,
        thermal={
            "blocked": thermal_state in {
                "BLOCKED", "RECOVERY_REQUIRED", "REPAIR_REQUIRED"
            },
            "observed_at": thermal_card.get("as_of") or generated_at,
        },
        recovery={
            "required": operations_state in {
                "RECOVERY_REQUIRED", "REPAIR_REQUIRED"
            },
            "observed_at": operations_card.get("as_of") or generated_at,
        },
    )


class ApplianceReadinessQueryService:
    """One query-only composed source for GUI, CLI, and support surfaces."""

    def __init__(self, *, home: Any, connections: Any) -> None:
        self._home = home
        self._connections = connections

    def snapshot(
        self, *, target_journey: str = "native_chat",
    ) -> ApplianceReadinessSnapshot:
        home = self._home.snapshot().to_dict()
        connection = self._connections.snapshot().to_dict()
        return readiness_from_snapshots(
            home=home, connection=connection, target_journey=target_journey)


__all__ = [
    "ApplianceReadinessSnapshot",
    "ApplianceReadinessQueryService",
    "ComponentReadiness",
    "JOURNEY_VERIFICATION_MAX_AGE_SECONDS",
    "LIVE_OBSERVATION_MAX_AGE_SECONDS",
    "READINESS_SNAPSHOT_SCHEMA_VERSION",
    "READINESS_STATES",
    "ReadinessState",
    "build_appliance_readiness",
    "readiness_from_snapshots",
]
