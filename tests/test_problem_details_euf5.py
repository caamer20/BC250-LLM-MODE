import json

from bc250_llm_mode.problem_details import (
    PROBLEM_CATALOG,
    openai_error,
    problem_detail,
)


REQUIRED = {
    "MODEL_NOT_RUNNING", "MODEL_IDENTITY_MISMATCH", "MODEL_WARMING",
    "GATEWAY_NOT_INSTALLED", "GATEWAY_NOT_RUNNING",
    "GATEWAY_BACKEND_UNVERIFIED", "AUTH_MISSING", "AUTH_INVALID",
    "SCOPE_NOT_GRANTED", "ENDPOINT_UNSUPPORTED",
    "OPENWEBUI_START_FAILED", "OPENWEBUI_WARMING",
    "OPENWEBUI_PROVIDER_STALE", "OPENWEBUI_MODEL_MISSING",
    "TAILSCALE_DISCONNECTED", "SERVE_MAPPING_MISMATCH",
    "FUNNEL_MUST_BE_DISABLED", "STREAM_INTERRUPTED", "UPSTREAM_TIMEOUT",
    "RECOVERY_REQUIRED",
}


def test_catalog_is_closed_bounded_and_contains_required_euf5_codes():
    assert REQUIRED <= set(PROBLEM_CATALOG)
    for code, problem in PROBLEM_CATALOG.items():
        assert problem.code == code
        assert len(problem.title) <= 160
        assert len(problem.user_message) <= 2048
        assert len(problem.technical_summary) <= 240
        assert problem.safe_action_id and problem.safe_action_label


def test_unknown_and_request_ids_fail_closed_without_echoing_input():
    unknown = problem_detail(
        "SECRET_UNKNOWN_CANARY", request_id="bad/request/id?secret=canary")
    assert unknown.code == "UNKNOWN_PROBLEM"
    assert unknown.request_id is None
    assert "CANARY" not in json.dumps(unknown.to_dict())


def test_openai_error_shape_has_stable_code_and_no_technical_detail():
    problem = problem_detail("AUTH_INVALID", request_id="request-123")
    payload = openai_error(problem)
    assert payload == {
        "error": {
            "message": problem.user_message,
            "type": "authentication_error",
            "param": None,
            "code": "AUTH_INVALID",
        },
        "request_id": "request-123",
    }
    assert "technical_summary" not in json.dumps(payload)
