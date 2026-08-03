import pytest

from bc250_llm_mode.optimize import (
    DEFAULT_OPTIMIZATIONS,
    _update_toml_value,
    apply_optimizations,
    kv_scale_for_settings,
    normalized_settings,
    validate_settings,
)


class NoCommandRunner:
    def __init__(self):
        self.commands = []
        self.messages = []

    def run(self, command, **_kwargs):
        self.commands.append(command)
        raise AssertionError(f"unexpected host command: {command}")

    def emit(self, message):
        self.messages.append(message)


def test_defaults_only_enable_safe_runtime_tuning():
    settings = normalized_settings(None)
    assert settings["runtime_enabled"] is True
    assert settings["gpu_enabled"] is False
    assert settings["memory_enabled"] is False
    assert settings["trim_services_enabled"] is False
    assert settings["safeguards_enabled"] is False


def test_balanced_values_validate():
    settings = validate_settings(DEFAULT_OPTIMIZATIONS)
    assert settings["batch_size"] == 1024
    assert settings["ubatch_size"] == 256
    assert settings["gpu_min_mhz"] == 500
    assert settings["gpu_max_mhz"] == 1850


def test_kv_scale_tracks_runtime_toggle():
    assert kv_scale_for_settings({"runtime_enabled": True, "kv_cache_type": "q4_0"}) == 0.5
    assert kv_scale_for_settings({"runtime_enabled": False, "kv_cache_type": "q4_0"}) == 1.0


def test_default_apply_has_no_host_side_commands():
    state = {"disclaimer_ack": True, "setup_phase": 5}
    runner = NoCommandRunner()
    apply_optimizations(state, DEFAULT_OPTIMIZATIONS, runner)
    assert runner.commands == []
    assert state["setup_phase"] == 6
    assert state["optimizations_applied"] is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("batch_size", 96),
        ("ubatch_size", 1024),
        ("gpu_min_mhz", 400),
        ("gpu_max_mhz", 2050),
        ("thermal_throttle_c", 95),
        ("swappiness", 201),
    ],
)
def test_values_are_bounded(key, value):
    settings = normalized_settings(None)
    settings[key] = value
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_relationships_are_validated():
    settings = normalized_settings(None)
    settings["batch_size"] = 128
    settings["ubatch_size"] = 256
    with pytest.raises(ValueError, match="cannot exceed"):
        validate_settings(settings)
    settings = normalized_settings(None)
    settings["thermal_throttle_c"] = 80
    settings["thermal_recovery_c"] = 78
    with pytest.raises(ValueError, match="at least 5"):
        validate_settings(settings)


def test_cyan_toml_update_is_section_scoped_and_preserves_comments():
    source = """[frequency-range]
min = 1000 # MHz
max = 1850

[other]
min = 99
"""
    changed = _update_toml_value(source, "frequency-range", "min", 500)
    assert "min = 500 # MHz" in changed
    assert "[other]\nmin = 99" in changed
