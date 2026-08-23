"""Session 3 blockers: unsafe thermal force reset removed from the normal
CLI, and the optimization form collector reads real widget values."""

from __future__ import annotations

import pytest

from bc250_llm_mode.__main__ import _parser
from _native import NativeApp
from bc250_llm_mode.services import ThermalStateService


def test_parser_has_no_force_reset_flag():
    with pytest.raises(SystemExit):
        _parser().parse_args(["thermals", "reset", "--force-reset"])


def test_reset_without_sensor_denied_and_latch_unchanged(tmp_path, monkeypatch):
    store = NativeApp(tmp_path)
    service = ThermalStateService.for_database(store.paths.database_path)
    service.ensure_throttle({"gpu_max_mhz": 1850})
    service.mark_stopped()
    before = service.current()
    state = store.load()

    monkeypatch.setattr("bc250_llm_mode.thermals.read_gpu_temperature", lambda: None)
    from bc250_llm_mode.thermals import reset_latch

    with pytest.raises(RuntimeError, match="sensor"):
        reset_latch(store, state, runner=type("R", (), {"emit": staticmethod(lambda *_: None)})())

    assert service.current() == before