import subprocess

import pytest

from bc250_llm_mode.constants import KNOWN_GOOD_LLAMACPP, TAG_PATTERN
from bc250_llm_mode.env import (
    evaluate_pin,
    llamacpp_status,
    record_llamacpp_build,
    rollback_llamacpp,
    update_llamacpp,
)


class FakeRunner:
    """Records commands; scripted stdout per git subcommand."""

    def __init__(self, outputs=None, fail_on=None):
        self.commands = []
        self.outputs = outputs or {}
        self.fail_on = fail_on or ()
        self.messages = []

    def run(self, command, **kwargs):
        command = [str(c) for c in command]
        self.commands.append(command)
        joined = " ".join(command)
        for needle in self.fail_on:
            if needle in joined:
                return subprocess.CompletedProcess(command, 1, "", "boom")
        stdout = ""
        for needle, value in self.outputs.items():
            if needle in joined:
                stdout = value
                break
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def emit(self, message):
        self.messages.append(message)


GIT_OUTPUTS = {
    "rev-parse HEAD": "abc123def456",
    "describe --tags": KNOWN_GOOD_LLAMACPP,
}


def _ready_state():
    return {
        "disclaimer_ack": True,
        "env_ready": True,
        "container_name": "llm",
        "llama_cpp_path": "/root/llama.cpp",
        "llamacpp_build": None,
        "llamacpp_history": [],
    }


def test_pin_constant_is_a_safe_tag():
    assert TAG_PATTERN.fullmatch(KNOWN_GOOD_LLAMACPP)
    assert KNOWN_GOOD_LLAMACPP.startswith("b")


def test_evaluate_pin():
    assert evaluate_pin(f"{KNOWN_GOOD_LLAMACPP}-abc")
    assert not evaluate_pin("b9999-other")
    assert not evaluate_pin(None)
    assert not evaluate_pin("")


def test_record_build_pushes_previous_into_history():
    state = _ready_state()
    state["llamacpp_build"] = {"commit": "old1", "describe": "b1111", "recorded": "yesterday"}
    info = record_llamacpp_build(state, FakeRunner(GIT_OUTPUTS))
    assert info["commit"] == "abc123def456"
    assert state["llamacpp_history"][-1]["commit"] == "old1"


def test_status_reports_drift_when_nothing_recorded():
    state = _ready_state()
    state["env_ready"] = False
    report = llamacpp_status(state, FakeRunner(GIT_OUTPUTS))
    assert report["on_pin"] is False
    assert report["pin"] == KNOWN_GOOD_LLAMACPP


def test_update_rejects_invalid_tag_before_any_command():
    runner = FakeRunner(GIT_OUTPUTS)
    with pytest.raises(ValueError, match="Invalid llama.cpp tag"):
        update_llamacpp(_ready_state(), runner, tag="bad tag; rm -rf /")
    assert runner.commands == [], "no host command may run for an invalid tag"


def test_update_requires_ready_environment():
    state = _ready_state()
    state["env_ready"] = False
    with pytest.raises(RuntimeError, match="not ready"):
        update_llamacpp(state, FakeRunner(GIT_OUTPUTS))


def test_update_happy_path_orders_staged_build_then_atomic_swap(monkeypatch):
    state = _ready_state()
    runner = FakeRunner(GIT_OUTPUTS)
    restart_at = []

    def fake_restart(st, rn):
        restart_at.append(len(rn.commands))
        return {"healthy": True}

    monkeypatch.setattr("bc250_llm_mode.server.restart_and_wait", fake_restart)
    result = update_llamacpp(state, runner, tag="b8000")
    assert result["updated_to"] == "b8000"
    joined = [" ".join(c) for c in runner.commands]
    # The active source checkout must never be checked out to the new tag.
    assert not any("checkout" in c for c in joined), "active source must stay untouched"
    build_idx = next(i for i, c in enumerate(joined) if "llama.cpp-staging" in c and "cmake" in c)
    clone_idx = next(i for i, c in enumerate(joined) if "git clone" in c)
    swap_idx = next(i for i, c in enumerate(joined) if "mv /root/llama.cpp /root/llama.cpp-backup" in c)
    # The staging clone and its cmake build share one shell invocation.
    assert clone_idx <= build_idx < swap_idx < restart_at[0]
    assert "git fetch origin tag b8000" in joined[0]
    assert any("mv /root/llama.cpp-staging /root/llama.cpp" in c for c in joined)
    assert state["llamacpp_build"]["describe"] == KNOWN_GOOD_LLAMACPP
    # Only one physical backup exists; history must not claim more.
    assert len(state["llamacpp_history"]) <= 1


def test_update_failure_restores_previous_build(monkeypatch):
    state = _ready_state()
    state["llamacpp_build"] = {"commit": "old", "describe": "b1111"}
    runner = FakeRunner(GIT_OUTPUTS)
    calls = {"n": 0}

    def flaky_restart(st, rn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("server never became healthy")
        return {"healthy": True}

    monkeypatch.setattr("bc250_llm_mode.server.restart_and_wait", flaky_restart)
    with pytest.raises(TimeoutError):
        update_llamacpp(state, runner)
    joined = [" ".join(c) for c in runner.commands]
    assert any("mv /root/llama.cpp-backup /root/llama.cpp" in c for c in joined), (
        "previous build must be restored"
    )
    assert state["llamacpp_build"]["describe"] == "b1111"


def test_rollback_requires_history():
    with pytest.raises(RuntimeError, match="No previous llama.cpp build"):
        rollback_llamacpp(_ready_state(), FakeRunner(GIT_OUTPUTS))


def test_rollback_swaps_back_and_restores_recorded_build(monkeypatch):
    state = _ready_state()
    state["llamacpp_build"] = {"commit": "new", "describe": KNOWN_GOOD_LLAMACPP}
    state["llamacpp_history"] = [{"commit": "old", "describe": "b1111", "recorded": "x"}]
    runner = FakeRunner(GIT_OUTPUTS)
    monkeypatch.setattr(
        "bc250_llm_mode.server.restart_and_wait", lambda st, rn: {"healthy": True}
    )
    result = rollback_llamacpp(state, runner)
    assert result["rolled_back_to"] == "b1111"
    assert state["llamacpp_build"]["describe"] == "b1111"
    assert state["llamacpp_history"] == []
    joined = [" ".join(c) for c in runner.commands]
    assert any("mv /root/llama.cpp-backup /root/llama.cpp" in c for c in joined)