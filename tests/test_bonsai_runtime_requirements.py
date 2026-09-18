"""Requested Bonsai packs must not masquerade as stock-runtime models."""

from dataclasses import replace
import hashlib
import json
import struct
from types import SimpleNamespace

import pytest

from bc250_llm_mode.acquisition_adapter import HostError
from bc250_llm_mode.catalog import catalog_rows, model_by_id, recommend_models
from bc250_llm_mode.hub_source import catalog_fingerprint
from bc250_llm_mode.model_artifact import (
    MAX_ARRAY_VALUES, MAX_HEADER_BYTES, VERDICT_NOT_GGUF,
    VERDICT_RUNTIME_REQUIRED, VERDICT_STANDARD, gguf_layout_verdict,
)
from test_acquisition_adapter import ProductionHarness
from test_activation_adapter import world  # noqa: F401 — shared production fixture


def text(value):
    encoded = value.encode()
    return struct.pack("<Q", len(encoded)) + encoded


def field(name, kind, payload):
    return text(name) + struct.pack("<I", kind) + payload


ARCH = field("general.architecture", 8, text("qwen35"))
ROTATION = field("prism.hadamard.version", 4, struct.pack("<I", 1))


def gguf(*fields):
    return b"GGUF" + struct.pack("<IQQ", 3, 1, len(fields)) + b"".join(fields)


def test_requested_packs_are_exact_and_memory_fit_is_not_runtime_compatibility():
    rows = {row["id"]: row for row in catalog_rows("bonsai", ctx_tokens=32768)}
    assert set(rows) == {"bonsai2-27b", "bonsai2-27b-crack"}
    official = model_by_id("bonsai2-27b")
    crack = model_by_id("bonsai2-27b-crack")
    assert official.allow_globs == {"PTQ1_0": "Ternary-Bonsai-2-27B-PTQ1_0.gguf"}
    assert crack.allow_globs == {"PQ2_0": "Bonsai-2-27B-PQ2_0-CRACK.gguf"}
    assert official.weights_gib_by_quant["PTQ1_0"] * 1024**3 == 5946648928
    assert crack.weights_gib_by_quant["PQ2_0"] * 1024**3 == 7206168928
    assert rows[official.id]["recommended_quant"] == "PTQ1_0"
    for row in rows.values():
        assert row["verdict"] == "FITS"  # memory estimate only
        assert row["runtime_compatible"] is False
        assert "PrismML" in row["runtime_requirement"]
        assert row["validation_tier"] == "preview"
    assert not any(model.id in rows for model, *_ in recommend_models(32768))


def test_model_library_explains_requirement_instead_of_offering_install_or_start():
    from bc250_llm_mode.gui.models_page import build_model_items, model_action

    items = [item for item in build_model_items([], context=32768, slots=1)
             if item.catalog_id in {"bonsai2-27b", "bonsai2-27b-crack"}]
    assert len(items) == 2
    for item in items:
        assert item.state == "RUNTIME_REQUIRED"
        assert not item.recommended and not item.verified
        action = model_action(item)
        assert action.code == "fit" and action.label == "View runtime requirements"
        assert action.secondary_code is None
        assert item.runtime_requirement in item.fit_detail


def test_stale_install_action_cannot_bypass_requirement():
    from bc250_llm_mode.gui.models_page import ModelsPage, build_model_items

    item = next(row for row in build_model_items([], context=8192, slots=1)
                if row.catalog_id == "bonsai2-27b")
    notices = []
    page = SimpleNamespace(_selected=lambda: item, shell=SimpleNamespace(
        notice_bar=SimpleNamespace(show_notice=notices.append)))
    ModelsPage._run_action(page, "install-start")
    assert len(notices) == 1
    assert notices[0].title == "This model needs another runtime"


def test_runtime_requirement_does_not_hide_existing_quarantine():
    from bc250_llm_mode.gui.models_page import build_model_items

    row = SimpleNamespace(alias="local-bonsai", display_name="Local Bonsai",
                          catalog_id="bonsai2-27b", quant="PTQ1_0", active=False,
                          trust_state="QUARANTINED", validation_status="quarantined")
    item = next(item for item in build_model_items([row], context=8192, slots=1)
                if item.alias == row.alias)
    assert item.state == "QUARANTINED" and not item.recommended


def test_setup_cannot_continue_or_recommend_runtime_required_model():
    from bc250_llm_mode.gui.setup_forms import SetupForms, fit_message

    model = model_by_id("bonsai2-27b")
    assert fit_message(model, "PTQ1_0", 8192, slots=1) == (model.runtime_requirement, False)
    displayed = {}
    window = SimpleNamespace(workload_goal_var=SimpleNamespace(get=lambda: "quality"),
                             ctx_var=SimpleNamespace(set=lambda _value: None),
                             workload_recommendations=SimpleNamespace(configure=lambda **kw: displayed.update(kw)),
                             _fit=lambda: None)
    SetupForms._workload_goal_changed(window)
    assert "Bonsai" not in displayed["text"]


@pytest.mark.parametrize("model_id", ["bonsai2-27b", "bonsai2-27b-crack"])
def test_durable_acquisition_refuses_incompatible_catalog_before_network(tmp_path, model_id):
    harness = ProductionHarness(tmp_path, gguf(ARCH))
    entry = model_by_id(model_id)
    harness.host.catalog_lookup = lambda _model_id: entry
    harness.host.hub = object()  # no network method may be reached
    with pytest.raises(HostError) as error:
        harness.host.resolve_catalog_source(SimpleNamespace(
            model_id=model_id, quantization=next(iter(entry.allow_globs))))
    assert error.value.code == "MODEL_RUNTIME_REQUIRED"


@pytest.mark.parametrize("fields", [
    (ARCH, ROTATION),
    (ROTATION, ARCH),
    (ARCH, field("general.file_type", 4, struct.pack("<I", 141))),
    (ARCH, field("general.file_type", 4, struct.pack("<I", 143))),
    # A stock-recognized Q2_0 label must not hide rotated weights.
    (ARCH, field("general.file_type", 4, struct.pack("<I", 42)), ROTATION),
])
def test_renaming_or_reordering_metadata_cannot_bypass_custom_runtime_gate(tmp_path, fields):
    path = tmp_path / "ordinary-qwen-Q4_K_M.gguf"
    path.write_bytes(gguf(*fields))
    assert gguf_layout_verdict(path) == VERDICT_RUNTIME_REQUIRED


def test_large_tokenizer_array_is_skipped_and_later_transform_requirement_is_read(tmp_path):
    vocabulary = field("tokenizer.ggml.tokens", 9,
                       struct.pack("<IQ", 8, 5000) + text("word") * 5000)
    path = tmp_path / "model.gguf"
    path.write_bytes(gguf(ARCH, vocabulary, ROTATION))
    assert gguf_layout_verdict(path) == VERDICT_RUNTIME_REQUIRED
    path.write_bytes(gguf(ARCH, vocabulary))
    assert gguf_layout_verdict(path) == VERDICT_STANDARD


def test_scalar_metadata_widths_do_not_desynchronize_header_walk(tmp_path):
    path = tmp_path / "model.gguf"
    path.write_bytes(gguf(ARCH,
        field("bool", 7, struct.pack("<?", True)),
        field("int64", 11, struct.pack("<q", -2)),
        field("float64", 12, struct.pack("<d", 0.5)), ROTATION))
    assert gguf_layout_verdict(path) == VERDICT_RUNTIME_REQUIRED


@pytest.mark.parametrize("payload", [
    # Invalid content after a familiar architecture is no longer ignored.
    gguf(ARCH, field("array", 9, struct.pack("<IQ", 8, MAX_ARRAY_VALUES + 1))),
    gguf(ARCH, field("array", 9, struct.pack("<IQ", 10, 5))),
    gguf(ARCH, field("unknown", 99, b"")),
    gguf(ARCH, ARCH),
])
def test_familiar_architecture_does_not_skip_malformed_remaining_metadata(tmp_path, payload):
    path = tmp_path / "model.gguf"
    path.write_bytes(payload)
    assert gguf_layout_verdict(path) == VERDICT_NOT_GGUF


def test_total_metadata_walk_has_a_byte_limit(tmp_path, monkeypatch):
    import bc250_llm_mode.model_artifact as artifact
    path = tmp_path / "model.gguf"
    path.write_bytes(gguf(ARCH, field("string", 8, text("x" * 100))))
    monkeypatch.setattr(artifact, "MAX_HEADER_BYTES", len(gguf(ARCH)) + 8)
    assert gguf_layout_verdict(path) == VERDICT_NOT_GGUF
    assert MAX_HEADER_BYTES == 64 * 1024 * 1024


def test_local_import_quarantines_custom_runtime_file_and_preserves_source(tmp_path):
    content = gguf(ARCH, ROTATION)
    harness = ProductionHarness(tmp_path, content)
    outcome = harness.engine().execute_one(harness.operation_id)
    assert outcome.reason_code == "ARTIFACT_QUARANTINED"
    assert harness.source.read_bytes() == content
    with harness.units.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_installations").fetchone()[0] == 0
        row = conn.execute("SELECT quarantine_reason_code FROM model_artifacts").fetchone()
        assert row[0] == "MODEL_RUNTIME_REQUIRED"


def test_activation_rechecks_bytes_even_for_previously_registered_alias(world):
    from bc250_llm_mode.activation_adapter import ArtifactRejected
    from bc250_llm_mode.operations.activation import ModelActivateRequestV1
    world.artifacts[world.model_b.id].write_bytes(gguf(ARCH, ROTATION))
    with pytest.raises(ArtifactRejected) as error:
        world.adapter.resolve_candidate(ModelActivateRequestV1(model_alias=world.model_b.id))
    assert error.value.code == "MODEL_LAYOUT_REJECTED_RUNTIME_REQUIRED"


def test_runtime_requirement_is_fingerprinted_without_changing_legacy_policy_identity():
    legacy = model_by_id("lfm25-26b")
    payload = {"schema": 1, "id": legacy.id, "repo": legacy.repo,
               "source_repo": legacy.source_repo, "allow_glob": legacy.allow_globs["Q5_K_M"],
               "avoid": list(legacy.avoid), "conversion": legacy.conversion,
               "checksum_manifest": legacy.checksum_manifest,
               "temporary_disk_gib": legacy.temporary_disk_gib,
               "true_block_count": legacy.true_block_count,
               "validation_tier": legacy.validation_tier}
    prior = "fp:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
    assert catalog_fingerprint(legacy, "Q5_K_M") == prior
    constrained = replace(legacy, runtime_requirement="Needs another runtime")
    assert catalog_fingerprint(constrained, "Q5_K_M") != prior
