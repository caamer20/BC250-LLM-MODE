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


class _WidgetHost:
    """Duck-typed stand-in carrying only the optimization widget variables."""

    def __init__(self, state_data, *, runtime=True, slots=4):
        from bc250_llm_mode.optimize import TRIMMABLE_SERVICES

        self.state_data = state_data
        self.opt_runtime = _B(runtime)
        self.opt_flash = _S("auto")
        self.opt_batch = _I(2048)
        self.opt_ubatch = _I(512)
        self.opt_kv = _S("q8_0")
        self.opt_parallel = _I(slots)
        self.opt_gpu = _B(False)
        self.opt_gpu_min = _I(500)
        self.opt_gpu_max = _I(1850)
        self.opt_throttle = _I(85)
        self.opt_recovery = _I(75)
        self.opt_memory = _B(False)
        self.opt_swappiness = _I(100)
        self.opt_safeguards = _B(True)
        self.opt_restart_window = _I(120)
        self.opt_restart_burst = _I(3)
        self.opt_restart_delay = _I(10)
        self.opt_log_max = _I(50)
        self.opt_trim = _B(False)
        self.opt_service_vars = {unit: _B(False) for unit in TRIMMABLE_SERVICES}


class _B:
    def __init__(self, v):
        self._v = bool(v)

    def get(self):
        return self._v


class _I:
    def __init__(self, v):
        self._v = int(v)

    def get(self):
        return self._v


class _S:
    def __init__(self, v):
        self._v = str(v)

    def get(self):
        return self._v


def test_collect_optimization_settings_reads_real_widget_values():
    """Regression: the form collector must read the populated widget
    variables (previously passed an undefined name and raised NameError)."""
    from bc250_llm_mode.gui.forms import FormsMixin

    host = _WidgetHost(_form_state("lfm25-26b", "Q5_K_M", 128000), runtime=True)
    settings = FormsMixin._collect_optimization_settings(host)
    assert settings["runtime_enabled"] is True
    assert settings["parallel_slots"] == 4
    assert settings["batch_size"] == 2048

    host_disabled = _WidgetHost(
        _form_state("lfm25-26b", "Q5_K_M", 128000), runtime=False, slots=1
    )
    settings_off = FormsMixin._collect_optimization_settings(host_disabled)
    assert settings_off["runtime_enabled"] is False


def test_optimization_settings_propagate_invalid_values():
    with pytest.raises(ValueError):
        optimization_settings_from_values(_form_state(), {"parallel_slots": 99})
