import struct

import numpy as np
from gguf import GGUFWriter

from bc250_llm_mode import prepare as prepare_module
from bc250_llm_mode.local_models import LocalModel
from bc250_llm_mode.prepare import (
    MetadataPatch,
    patch_uint32_metadata,
    verify_and_heal_gguf,
)


class QuietRunner:
    def emit(self, _message):
        pass


def field(key: str, value: int) -> bytes:
    return key.encode() + struct.pack("<I", 4) + struct.pack("<I", value)


def test_guarded_patch_and_idempotency(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_bytes(b"GGUF" + field("qwen35.block_count", 33) + b"padding")
    patch = MetadataPatch("qwen35.block_count", 33, 32)
    messages = patch_uint32_metadata(target, [patch])
    assert "33 -> 32" in messages[0]
    assert target.with_suffix(".gguf.bak").exists()
    assert "already equals 32" in patch_uint32_metadata(target, [patch])[0]


def test_patch_refuses_unknown_value(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_bytes(field("qwen35.block_count", 34))
    try:
        patch_uint32_metadata(target, [MetadataPatch("qwen35.block_count", 33, 32)])
    except ValueError as exc:
        assert "Refusing" in str(exc)
    else:
        raise AssertionError("unsafe patch was accepted")


def test_real_gguf_reader_self_heals_block_and_nextn(tmp_path):
    target = tmp_path / "model-Q5_K_M.gguf"
    writer = GGUFWriter(target, "qwen35")
    writer.add_block_count(3)
    writer.add_uint32("qwen35.nextn_predict_layers", 1)
    # Keep it over the verifier's one-MiB sanity floor without using much memory.
    for index in range(2):
        writer.add_tensor(f"blk.{index}.weight", np.ones((512, 512), dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    result = verify_and_heal_gguf(target)
    assert result["block_count"] == 2
    assert result["tensor_block_count"] == 2
    assert any("nextn_predict_layers" in message for message in result["messages"])


def test_local_prepare_preserves_container_visible_symlink_path(tmp_path, monkeypatch):
    real_home = tmp_path / "var" / "roothome"
    real_home.mkdir(parents=True)
    root_link = tmp_path / "root"
    root_link.symlink_to(real_home, target_is_directory=True)
    target = root_link / "model-Q5_K_M.gguf"
    target.write_bytes(b"placeholder")
    monkeypatch.setattr(
        prepare_module,
        "verify_and_heal_gguf",
        lambda _path, _runner: {"architecture": "qwen35"},
    )
    model = LocalModel(
        "local-test", "model", str(target), "Q5_K_M", 6.0, "qwen35", 9.7, 72.0
    )
    state = {"installed_models": [], "setup_phase": 7}

    prepared = prepare_module.prepare_local_model(state, model, runner=QuietRunner())

    assert str(prepared) == str(target.absolute())
    assert state["installed_models"][0]["path"] == str(target.absolute())
