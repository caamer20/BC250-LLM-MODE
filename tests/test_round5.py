import subprocess

import pytest

from bc250_llm_mode.constants import KNOWN_GOOD_LLAMACPP, TAG_PATTERN


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


def test_legacy_synchronous_lifecycle_stays_deleted():
    """U1.2 §15.6: the synchronous env lifecycle never returns.

    Behavior coverage now lives in the durable workflow suites
    (test_runtime_workflow.py / test_runtime_exchange_death.py).
    """
    import bc250_llm_mode.env as env

    for name in ("evaluate_pin", "llamacpp_status", "record_llamacpp_build",
                 "rollback_llamacpp", "update_llamacpp"):
        assert not hasattr(env, name)
