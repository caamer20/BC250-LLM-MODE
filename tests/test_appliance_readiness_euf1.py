from __future__ import annotations

import json

import pytest

from bc250_llm_mode.appliance_readiness import (
    JOURNEY_VERIFICATION_MAX_AGE_SECONDS,
    LIVE_OBSERVATION_MAX_AGE_SECONDS,
    READINESS_STATES,
    ReadinessState,
    build_appliance_readiness,
    readiness_from_snapshots,
)


NOW = "2026-09-01T01:00:00+00:00"


def _model(**changes):
    value = {
        "installed": True,
        "service_active": True,
        "protocol_ready": True,
        "chat_verified": True,
        "expected_identity": "qwen38-9b:ctx8192:slots1",
        "observed_identity": "qwen38-9b:ctx8192:slots1",
        "observed_at": NOW,
    }
    value.update(changes)
    return value


def _gateway(**changes):
    value = {
        "service_installed": True,
        "service_active": True,
        "credential_metadata_ready": True,
        "listeners_ready": True,
        "backend_identity_verified": True,
        "authenticated_sse_verified": True,
        "observed_at": NOW,
    }
    value.update(changes)
    return value


def _openwebui(**changes):
    value = {
        "installed": True,
        "running": True,
        "http_ready": True,
        "provider_configured": True,
        "expected_model_visible": True,
        "end_to_end_verified": True,
        "observed_at": NOW,
    }
    value.update(changes)
    return value


def _tailscale(**changes):
    value = {
        "installed": True,
        "daemon_active": True,
        "connected": True,
        "dns_name": "bazzite.tail2168f.ts.net",
        "observed_at": NOW,
    }
    value.update(changes)
    return value


def _serve(**changes):
    value = {
        "mappings_exact": True,
        "funnel_disabled": True,
        "public_funnel": False,
        "observed_at": NOW,
    }
    value.update(changes)
    return value


def _verification(**changes):
    value = {
        "local_unauthorized": "passed",
        "local_authorized": "passed",
        "tailnet_unauthorized": "passed",
        "tailnet_authorized_models": "passed",
        "tailnet_stream": "passed",
        "observed_at": NOW,
        "dependency_identity": "m1:g1:s1:c1",
        "verified_dependency_identity": "m1:g1:s1:c1",
    }
    value.update(changes)
    return value


def _ready(**changes):
    values = {
        "generated_at": NOW,
        "model": _model(),
        "gateway": _gateway(),
        "openwebui": _openwebui(),
        "tailscale": _tailscale(),
        "serve": _serve(),
        "client_verification": _verification(),
    }
    values.update(changes)
    return build_appliance_readiness(**values)


def test_closed_vocabulary_and_bounded_serialization():
    snapshot = _ready(target_journey="remote_client")
    document = snapshot.to_dict()

    assert set(READINESS_STATES) == {
        "ABSENT", "STOPPED", "STARTING", "READY", "DEGRADED", "BLOCKED", "UNKNOWN"
    }
    assert document["overall_state"] == "READY"
    assert document["native_chat_ready"] is True
    assert document["openwebui_ready"] is True
    assert document["remote_client_ready"] is True
    assert len(document["components"]) <= 16
    assert "credential" not in json.dumps(document).lower()


@pytest.mark.parametrize(
    ("component", "source", "state", "code"),
    (
        ("model", _model(protocol_ready=False), "DEGRADED", "MODEL_PROTOCOL_UNAVAILABLE"),
        ("gateway", _gateway(listeners_ready=False), "DEGRADED", "GATEWAY_PROTOCOL_UNAVAILABLE"),
        ("openwebui", _openwebui(http_ready=False), "DEGRADED", "OPENWEBUI_HTTP_UNAVAILABLE"),
    ),
)
def test_active_process_with_dead_protocol_never_reports_ready(
    component, source, state, code,
):
    values = {component: source, "target_journey": (
        "native_chat" if component == "model" else
        "openwebui" if component == "openwebui" else "remote_client"
    )}
    snapshot = _ready(**values)
    observed = snapshot.component(component)

    assert observed.process_ready is True
    assert observed.protocol_ready is False
    assert observed.state.value == state
    assert observed.problem_code == code
    assert snapshot.overall_state is not ReadinessState.READY


def test_gateway_metadata_without_runtime_is_stopped_not_ready():
    snapshot = _ready(
        gateway=_gateway(service_active=False, listeners_ready=False),
        target_journey="remote_client",
    )

    gateway = snapshot.component("gateway")
    assert gateway.state is ReadinessState.STOPPED
    assert gateway.problem_code == "GATEWAY_STOPPED"
    assert snapshot.remote_client_ready is False


def test_wrong_running_model_identity_is_blocked():
    snapshot = _ready(
        model=_model(observed_identity="another-model:ctx4096:slots2"),
        target_journey="native_chat",
    )

    model = snapshot.component("model")
    assert model.state is ReadinessState.BLOCKED
    assert model.problem_code == "MODEL_IDENTITY_MISMATCH"
    assert snapshot.native_chat_ready is False


def test_explicit_local_public_alias_match_is_preserved():
    snapshot = _ready(
        model=_model(
            expected_identity="local-a3515b591cfc",
            observed_identity="LFM2.5-2.6B-Q5_K_M",
            identity_matches=True,
        ),
        target_journey="native_chat",
    )

    model = snapshot.component("model")
    assert model.state is ReadinessState.READY
    assert model.problem_code is None
    assert snapshot.native_chat_ready is True


def test_native_chat_does_not_depend_on_optional_integrations():
    snapshot = _ready(
        gateway={}, openwebui={}, tailscale={}, serve={},
        client_verification={}, target_journey="native_chat",
    )

    assert snapshot.native_chat_ready is True
    assert snapshot.overall_state is ReadinessState.READY
    assert snapshot.openwebui_ready is False
    assert snapshot.remote_client_ready is False


def test_remote_ready_requires_positive_negative_models_and_streaming_checks():
    snapshot = _ready(
        client_verification=_verification(tailnet_stream="failed"),
        target_journey="remote_client",
    )

    verification = snapshot.component("client_verification")
    assert verification.state is ReadinessState.DEGRADED
    assert verification.journey_ready is False
    assert snapshot.remote_client_ready is False
    assert snapshot.primary_problem_code == "CLIENT_VERIFICATION_FAILED"
    assert snapshot.primary_action == "checks/run"


def test_stale_live_and_journey_evidence_cannot_remain_ready():
    old_live = "2026-09-01T00:59:49+00:00"
    old_journey = "2026-09-01T00:54:59+00:00"
    assert LIVE_OBSERVATION_MAX_AGE_SECONDS == 10
    assert JOURNEY_VERIFICATION_MAX_AGE_SECONDS == 300

    model_stale = _ready(
        model=_model(observed_at=old_live), target_journey="native_chat")
    remote_stale = _ready(
        client_verification=_verification(observed_at=old_journey),
        target_journey="remote_client")

    assert model_stale.component("model").problem_code == "MODEL_OBSERVATION_STALE"
    assert model_stale.native_chat_ready is False
    assert remote_stale.component("client_verification").problem_code == "CLIENT_VERIFICATION_STALE"
    assert remote_stale.remote_client_ready is False


def test_dependency_identity_change_invalidates_only_downstream_verification():
    snapshot = _ready(
        client_verification=_verification(
            dependency_identity="m2:g1:s1:c1",
            verified_dependency_identity="m1:g1:s1:c1",
        ),
        target_journey="remote_client",
    )

    assert snapshot.native_chat_ready is True
    assert snapshot.component("client_verification").state is ReadinessState.BLOCKED
    assert snapshot.component("client_verification").problem_code == "CLIENT_VERIFICATION_INVALIDATED"
    assert snapshot.remote_client_ready is False


def test_safety_then_recovery_then_identity_priority_is_deterministic():
    thermal = _ready(
        thermal={"blocked": True, "observed_at": NOW},
        recovery={"required": True, "observed_at": NOW},
        model=_model(observed_identity="wrong"),
        target_journey="remote_client",
    )
    recovery = _ready(
        recovery={"required": True, "observed_at": NOW},
        model=_model(observed_identity="wrong"),
        target_journey="remote_client",
    )
    identity = _ready(
        model=_model(observed_identity="wrong"),
        target_journey="remote_client",
    )

    assert thermal.primary_problem_code == "THERMAL_SAFETY_BLOCK"
    assert recovery.primary_problem_code == "RECOVERY_REQUIRED"
    assert identity.primary_problem_code == "MODEL_IDENTITY_MISMATCH"


def test_path_and_secret_shaped_identity_values_are_not_serialized():
    snapshot = _ready(
        model=_model(
            expected_identity="/root/models/private.gguf",
            observed_identity="Bearer secret-canary-value",
        )
    ).to_dict()
    blob = json.dumps(snapshot)

    assert "/root/models" not in blob
    assert "secret-canary" not in blob


def test_unknown_target_fails_closed():
    with pytest.raises(ValueError, match="unknown target"):
        _ready(target_journey="anything")


def test_composed_projection_uses_current_home_completion_evidence():
    connection = {
        "generated_at": NOW,
        "model": {
            "healthy": True, "service_active": True,
            "public_alias": "qwen38-9b", "observed_at": NOW,
        },
        "gateway": {}, "openwebui": {}, "tailscale": {},
        "sharing": {}, "probes": {},
    }
    home = {
        "generated_at": NOW,
        "cards": {
            "model": {"desired": "qwen38-9b", "installed_count": 1},
            "inference": {
                "stale": False,
                "health": {"state": "READY", "effective_state": "READY"},
            },
            "thermal": {"health": {"state": "READY"}},
            "operations": {"health": {"state": "READY"}},
        },
    }
    ready = readiness_from_snapshots(home=home, connection=connection)
    stale_home = {
        **home,
        "cards": {
            **home["cards"],
            "inference": {
                "stale": True,
                "health": {"state": "READY", "effective_state": "READY"},
            },
        },
    }
    stale = readiness_from_snapshots(home=stale_home, connection=connection)

    assert ready.native_chat_ready is True
    assert stale.native_chat_ready is False
    assert stale.primary_problem_code == "NATIVE_CHAT_UNVERIFIED"
