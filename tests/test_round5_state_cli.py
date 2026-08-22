import json
from types import SimpleNamespace


from bc250_llm_mode import __main__ as cli
from bc250_llm_mode import env
from bc250_llm_mode.constants import KNOWN_GOOD_LLAMACPP
from bc250_llm_mode.state import StateStore


class QuietRunner:
    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.commands = []
        self.messages = []

    def run(self, command, **kwargs):
        command = [str(c) for c in command]
        self.commands.append(command)
        stdout = ""
        for needle, value in self.outputs.items():
            if needle in " ".join(command):
                stdout = value
                break
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    def emit(self, message):
        self.messages.append(message)


def test_schema_v5_migration_declares_new_keys(tmp_path):
    legacy = {
        "schema_version": 3,
        "disclaimer_ack": True,
        "optimizations": {"gpu_enabled": True},
        "bench_history": "garbage",
        "thermal_watchdog_state": "",
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    state = StateStore(path).load()
    assert state["schema_version"] == 5
    assert state["bench_history"] == []
    assert state["autotune_history"] == []
    assert state["thermal_watchdog_state"] == "nominal"
    assert state["llamacpp_build"] is None
    assert state["llamacpp_history"] == []
    # v4 migration must still work underneath.
    assert state["optimizations"]["gpu_tuning_enabled"] is True


def test_fresh_state_defaults_declare_v5_keys(tmp_path):
    state = StateStore(tmp_path / "state.json").load()
    assert state["schema_version"] == 5
    for key in ("bench_history", "autotune_history", "llamacpp_history"):
        assert state[key] == []
    assert state["llamacpp_build"] is None
    assert state["thermal_watchdog_state"] == "nominal"


def _patch_cli(monkeypatch, outputs=None):
    monkeypatch.setattr(cli, "configure_logging", lambda *_a: None)
    runner = QuietRunner(outputs)
    monkeypatch.setattr(cli, "CommandRunner", lambda *_a, **_k: runner)
    return runner


def test_cli_llamacpp_status(tmp_path, monkeypatch, capsys):
    runner = _patch_cli(monkeypatch)
    monkeypatch.setattr(env, "_container_exists", lambda rn, name: False)
    assert cli.main(["--state", str(tmp_path / "state.json"), "llamacpp", "status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pin"] == KNOWN_GOOD_LLAMACPP
    assert report["on_pin"] is False


def test_cli_doctor_reports_llamacpp_pin(tmp_path, monkeypatch, capsys):
    _patch_cli(monkeypatch)
    monkeypatch.setattr(cli, "detect_hardware", lambda *_a, **_k: SimpleNamespace(
        to_dict=lambda: {"valid": True}, valid=True
    ))
    monkeypatch.setattr(cli, "analyze_memory_profile", lambda *_a: SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr(cli, "service_status", lambda *_a: {"active": False})
    monkeypatch.setattr(cli, "open_webui_status", lambda *_a: {"running": False})
    monkeypatch.setattr(cli, "tailscale_status", lambda *_a: {"running": False})
    monkeypatch.setattr(cli, "https_sharing_status", lambda *_a: {"enabled": False})
    assert cli.main(["--state", str(tmp_path / "state.json"), "doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["llamacpp"]["pin"] == KNOWN_GOOD_LLAMACPP
    assert report["llamacpp"]["on_pin"] is False


def test_setup_records_build_metadata(tmp_path, monkeypatch):
    calls = []

    def fake_record(state, runner):
        calls.append(True)
        state["llamacpp_build"] = {"commit": "c", "describe": "b1"}
        return state["llamacpp_build"]

    monkeypatch.setattr(env, "record_llamacpp_build", fake_record)
    monkeypatch.setattr(env.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(env, "_container_exists", lambda rn, name: True)

    def fake_exec(runner, name, *args, **kwargs):
        command = " ".join(str(a) for a in args)
        if "test -x" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "vulkaninfo" in command:
            return SimpleNamespace(returncode=0, stdout="deviceName = AMD Radeon (BC-250)", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(env, "_exec", fake_exec)
    runner = QuietRunner()
    state = {
        "disclaimer_ack": True,
        "container_name": "llm",
        "llama_cpp_path": "/root/llama.cpp",
        "venv_path": "/root/.venvs/hf",
        "models_dir": str(tmp_path),
        "logs_dir": str(tmp_path),
        "app_dir": str(tmp_path),
    }
    env.setup_environment(state, runner)
    assert calls, "setup must record the llama.cpp build"
    assert state["env_ready"] is True