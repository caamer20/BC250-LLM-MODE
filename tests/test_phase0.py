"""Phase 0: launcher + llm CLI entry-point regressions, covered by execution."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from bc250_llm_mode import __main__ as cli_module  # noqa: E402
from bc250_llm_mode.__main__ import cli, main  # noqa: E402
from bc250_llm_mode.server import generate_launcher  # noqa: E402
from support_legacy_store import LegacyStateStore as StateStore  # noqa: E402


LAUNCHER_STATE_FLAGS = (
    "--n-gpu-layers", "--ctx-size", "--parallel", "--threads",
    "--threads-batch", "--cache-reuse", "--defrag-threshold",
    "--flash-attn", "--temp", "--top-p", "--top-k", "--min-p",
    "--repeat-penalty", "--alias", "--port",
)


def _launcher_state(tmp_path):
    llama_root = tmp_path / "llama.cpp"
    bin_dir = llama_root / "build" / "bin"
    bin_dir.mkdir(parents=True)
    record = tmp_path / "argv.txt"
    stub = bin_dir / "llama-server"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$@" >> "$BC250_RECORD"\n'
        'printf "FORCE_SYNC=%s\\n" "${GGML_VK_FORCE_SYNC-UNSET}" >> "$BC250_RECORD"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    app_dir = tmp_path / "app"
    state_path = app_dir / "state.json"
    state = {
        "app_dir": str(app_dir),
        "state_path": str(state_path),
        "llama_cpp_path": str(llama_root),
        "current_model": "lfm25-26b",
        "current_ctx": 8192,
        "server_port": 8080,
        "installed_models": [{
            "id": "lfm25-26b",
            "path": "/models/lfm25.gguf",
            "display_name": "LFM2.5 2.6B",
            "temperature": 0.3,
            "top_p": 0.9,
            "top_k": 40,
            "min_p": 0.05,
        }],
        "optimizations": {
            "runtime_enabled": True,
            "parallel_slots": 4,
            "kv_cache_type": "q8_0",
            "flash_attention": "auto",
            "batch_size": 2048,
            "ubatch_size": 512,
            "threads": 8,
            "fast_sync": False,
        },
    }
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    # Render the handoff the launcher consumes (the only launch source now).
    from bc250_llm_mode.runtime_handoff import RuntimeHandoffRenderer

    RuntimeHandoffRenderer(app_dir).publish(state, config_revision=1)
    return state, record


def test_launcher_delivers_all_flags_in_a_single_exec(tmp_path):
    state, record = _launcher_state(tmp_path)
    launcher = generate_launcher(state)

    syntax = subprocess.run(["bash", "-n", str(launcher)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr

    result = subprocess.run(
        ["bash", str(launcher)],
        env={**os.environ, "BC250_RECORD": str(record)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    argv = record.read_text(encoding="utf-8").splitlines()
    assert argv, "the dummy llama-server was never executed"

    # The binary is the exec command word (never in "$@"); the model flag
    # pair must be present with exactly one invocation of the dummy.
    pairs = dict(zip(argv, argv[1:] + [""]))
    assert pairs.get("-m") == "/models/lfm25.gguf"
    for flag in LAUNCHER_STATE_FLAGS:
        assert flag in argv, f"missing flag {flag} in delivered argv"
    assert pairs["--threads"] == "8"
    assert pairs["--threads-batch"] == "8"
    assert pairs["--cache-reuse"] == "256"
    assert pairs["--defrag-threshold"] == "0.1"
    assert pairs["--ctx-size"] == "32768"  # 8192 x 4 slots
    assert pairs["--parallel"] == "4"
    assert pairs["-m"] == "/models/lfm25.gguf"


def test_launcher_state_reader_compiles_and_omits_no_mmap(tmp_path):
    state, _record = _launcher_state(tmp_path)
    text = generate_launcher(state).read_text(encoding="utf-8")
    # The single handoff argv builder must compile; no legacy fallback exists.
    assert "<<'PYS'" not in text
    handoff = text.split("<<'PYH'\n", 1)[1].split("\nPYH\n", 1)[0]
    compile(handoff, "handoff-argv-builder", "exec")
    assert "--no-mmap" not in text
