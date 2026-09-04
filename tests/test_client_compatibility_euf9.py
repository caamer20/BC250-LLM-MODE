"""EUF-9 versioned client capability, card, and drift gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode.client_compatibility import (
    API_CAPABILITIES,
    CAPABILITY_CONDITIONAL,
    CAPABILITY_DEFERRED,
    CAPABILITY_NOT_CLIENT_API,
    CAPABILITY_SUPPORTED,
    CAPABILITY_UNSUPPORTED,
    CLIENT_CARDS,
    CLIENT_CARD_SCHEMA_VERSION,
    CLIENT_COMPATIBILITY_SCHEMA_VERSION,
    OPENWEBUI_GATEWAY_BASE_URL,
    base_url_problem,
    capability_contract,
    gateway_route_classification,
    validate_contract,
)
from bc250_llm_mode.connection_setup import endpoint_urls, instructions_for
from bc250_llm_mode.gateway import classify_path


def test_api_contract_has_the_exact_reviewed_v1_matrix():
    expected = (
        ("models-list", "GET", "/v1/models", CAPABILITY_SUPPORTED),
        ("chat-json", "POST", "/v1/chat/completions", CAPABILITY_SUPPORTED),
        ("chat-sse", "POST", "/v1/chat/completions", CAPABILITY_SUPPORTED),
        ("tools", "POST", "/v1/chat/completions", CAPABILITY_CONDITIONAL),
        ("embeddings", "POST", "/v1/embeddings", CAPABILITY_UNSUPPORTED),
        ("legacy-completions", "POST", "/v1/completions", CAPABILITY_UNSUPPORTED),
        ("responses", "POST", "/v1/responses", CAPABILITY_DEFERRED),
        ("openwebui-browser-api", "ANY", "/api/...", CAPABILITY_NOT_CLIENT_API),
    )
    assert tuple(
        (item.capability_id, item.method, item.path, item.status)
        for item in API_CAPABILITIES
    ) == expected
    assert CLIENT_COMPATIBILITY_SCHEMA_VERSION == 1


def test_contract_is_offline_bounded_and_contains_no_credentials():
    contract = capability_contract()
    blob = json.dumps(contract)

    assert contract["schema_version"] == 1
    assert contract["profile"] == "bc250-openai-compatible-v1"
    assert contract["offline"] is True
    assert len(contract["capabilities"]) == 8
    assert len(contract["client_cards"]) == 6
    assert "Bearer <" not in blob
    assert "credential_file" not in blob
    assert "fingerprint" not in blob


def test_gateway_classification_is_derived_from_the_published_matrix():
    expected = {
        "/v1/models": "models:list",
        "/v1/chat/completions": "inference:read",
        "/v1/embeddings": "unsupported-inference",
        "/v1/completions": "unsupported-inference",
        "/v1/responses": "unsupported-inference",
        "/api/chat": "management",
    }
    for path, classification in expected.items():
        assert gateway_route_classification(path) == classification
        assert classify_path(path) == classification

    gateway_source = Path("bc250_llm_mode/gateway.py").read_text(encoding="utf-8")
    assert "gateway_route_classification(clean)" in gateway_source
    for unsupported in ("/v1/embeddings", "/v1/completions", "/v1/responses"):
        assert unsupported not in gateway_source


def test_client_cards_claim_no_version_without_matching_evidence():
    validate_contract()
    assert CLIENT_CARD_SCHEMA_VERSION == 2
    assert all(card.support_level != "hardware-tested" for card in CLIENT_CARDS)
    version_claims = {
        card.card_id: card.tested_version
        for card in CLIENT_CARDS if card.tested_version is not None
    }
    assert version_claims == {"openwebui": "0.11.3"}
    for card in CLIENT_CARDS:
        assert card.field_labels
        assert card.automatic_probe_paths
        assert card.base_url_rule
        assert card.transport_requirement
        assert card.support_evidence


@pytest.mark.parametrize("card_id", [
    "openwebui", "pocketpal", "openai", "curl", "python", "sse",
])
def test_each_card_outputs_exactly_its_advertised_fields(card_id):
    instruction = instructions_for(
        card_id,
        urls=endpoint_urls("bazzite.tail2168f.ts.net"),
        public_alias="qwen38-9b",
    )
    card = next(item for item in CLIENT_CARDS if item.card_id == card_id)

    assert instruction["schema_version"] == CLIENT_CARD_SCHEMA_VERSION
    assert tuple(instruction["values"]) == card.field_labels
    assert instruction["available"] is True
    assert instruction["api_contract"]["schema_version"] == 1
    assert "qwen38-9b" in json.dumps(instruction)


def test_openwebui_and_tailnet_cards_use_distinct_exact_base_urls():
    urls = endpoint_urls("bazzite.tail2168f.ts.net")
    webui = instructions_for(
        "openwebui", urls=urls, public_alias="qwen38-9b"
    )
    phone = instructions_for(
        "pocketpal", urls=urls, public_alias="qwen38-9b"
    )

    assert webui["values"]["Base URL"] == OPENWEBUI_GATEWAY_BASE_URL
    assert phone["values"]["Base URL"] == (
        "https://bazzite.tail2168f.ts.net:10000/v1"
    )
    assert base_url_problem(webui["values"]["Base URL"], openwebui=True) is None
    assert base_url_problem(phone["values"]["Base URL"]) is None


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "BASE_URL_MISSING"),
        ("http://bazzite.tail2168f.ts.net:10000/v1", "BASE_URL_TRANSPORT_INVALID"),
        ("https://bazzite.tail2168f.ts.net:10000", "BASE_URL_PATH_INVALID"),
        ("https://bazzite.tail2168f.ts.net:10000/v1/v1", "BASE_URL_DUPLICATES_V1"),
        ("https://bazzite.tail2168f.ts.net:10000/v1?x=1", "BASE_URL_PATH_INVALID"),
    ],
)
def test_base_url_mistakes_have_stable_explanations(value, code):
    assert base_url_problem(value) == code


def test_connections_help_docs_and_support_share_the_contract():
    connections_source = Path(
        "bc250_llm_mode/gui/connections_page.py"
    ).read_text(encoding="utf-8")
    help_source = Path(
        "bc250_llm_mode/gui/help_page.py"
    ).read_text(encoding="utf-8")
    support_source = Path(
        "bc250_llm_mode/support_bundle.py"
    ).read_text(encoding="utf-8")
    doc = Path("docs/client-api-compatibility.md").read_text(encoding="utf-8")

    assert "capability_display_rows" in connections_source
    assert "capability_display_rows" in help_source
    assert 'emit("compatibility.json"' in support_source
    for item in API_CAPABILITIES:
        assert f"`{item.method} {item.path}`" in doc
        assert f"**{item.status}**" in doc
