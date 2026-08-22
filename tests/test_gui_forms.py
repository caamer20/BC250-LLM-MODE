import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.catalog import model_by_id  # noqa: E402
from bc250_llm_mode.gui.forms import fit_message, optimization_settings_from_values  # noqa: E402


def test_fit_message_fits_with_concurrency_suffix():
    message, may_continue = fit_message(model_by_id("lfm25-26b"), "Q5_K_M", 128000, slots=4)
    assert may_continue is True
    assert "FITS" in message
    assert "128,000 tokens per user across 4 slots" in message


def test_fit_message_suggests_q4_kv_when_tight():
    # Phi-4 Q4 at 32K single-slot overflows with Q8 KV but fits with Q4 KV.
    message, may_continue = fit_message(model_by_id("phi4-14b"), "Q4_K_M", 32768, slots=1)
    assert may_continue is True
    assert "Q4 KV can fit" in message


def test_fit_message_hard_no_fit():
    # Gemma 2's wide KV cache fails even with Q4 KV at 8K x 4.
    message, may_continue = fit_message(model_by_id("gemma-2-9b-it"), "Q6_K", 8192, slots=4)
    assert may_continue is False
    assert "NO-FIT" in message
    assert "Q4 KV" not in message


def _form_state(model_id="phi4-14b", quant="Q4_K_M", ctx=32768):
    return {
        "selected_source": "catalog",
        "selected_model": model_id,
        "selected_quant": quant,
        "current_ctx": ctx,
    }


def test_optimization_settings_validate_and_pass_through():
    checked = optimization_settings_from_values(
        _form_state("lfm25-26b", "Q5_K_M", 128000), {"parallel_slots": 4}
    )
    assert checked["runtime_enabled"] is True
    assert checked["parallel_slots"] == 4


def test_optimization_settings_reject_no_fit():
    with pytest.raises(ValueError, match="do not fit"):
        optimization_settings_from_values(
            _form_state(ctx=131072), {"parallel_slots": 1}
        )


def test_optimization_settings_propagate_invalid_values():
    with pytest.raises(ValueError):
        optimization_settings_from_values(_form_state(), {"parallel_slots": 99})
