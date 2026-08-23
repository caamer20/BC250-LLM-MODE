from pathlib import Path

import pytest

from bc250_llm_mode import chat, tune
from bc250_llm_mode.catalog import CATALOG, calculate_fit, model_by_id


class FakeRunner:
    def __init__(self):
        self.messages = []

    def run(self, command, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(list(command), 0, "", "")

    def emit(self, message):
        self.messages.append(message)


# --- Catalog round 4: four new models ---


def test_catalog_now_has_twenty_four_entries():
    assert len(CATALOG) == 24
    assert len({model.id for model in CATALOG}) == len(CATALOG)


@pytest.mark.parametrize(
    ("model_id", "quant", "ctx", "slots", "verdict"),
    [
        ("ornith-1.5-9b", "Q6_K", 8192, 4, "FITS"),
        ("ornith-1.5-9b", "Q8_0", 8192, 4, "NO-FIT"),
        ("qwen38-2b-distill", "Q8_0", 16384, 4, "FITS"),
        ("qwen38-2b-distill", "Q4_K_M", 32768, 4, "TIGHT"),
        ("qwen25-coder-3b", "Q8_0", 32768, 4, "FITS"),
        ("qwen25-coder-14b", "Q4_K_M", 8192, 1, "TIGHT"),
        ("qwen25-coder-14b", "Q3_K_M", 8192, 1, "FITS"),
        ("qwen25-coder-14b", "Q4_K_M", 8192, 2, "NO-FIT"),
    ],
)
def test_round4_model_fits(model_id, quant, ctx, slots, verdict):
    assert calculate_fit(model_by_id(model_id), quant, ctx, parallel_slots=slots).verdict == verdict


def test_discovery_matches_round4_names():
    from bc250_llm_mode.local_models import _catalog_match

    cases = {
        "/models/Ornith-1.5-9B-Q5_K_M.gguf": "ornith-1.5-9b",
        "/models/Qwen3.8-2B-Q6_K.gguf": "qwen38-2b-distill",
        "/models/Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf": "qwen25-coder-3b",
        "/models/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf": "qwen25-coder-14b",
    }
    for path, expected in cases.items():
        assert _catalog_match(Path(path)).id == expected


# --- Chat: ThinkFilter, trimming, export, history ---


def test_think_filter_handles_split_tags():
    f = chat.ThinkFilter()
    out = [f.feed(c) for c in ("<th", "ink>secret reasoning</", "think>visible answer")]
    out.append(f.flush())
    assert "".join(out) == "visible answer"


def test_think_filter_shows_plain_text_untouched():
    f = chat.ThinkFilter()
    out = f.feed("no tags here") + f.flush()
    assert out == "no tags here"


def test_trim_conversation_keeps_last_exchange():
    convo = [
        {"role": "user", "content": "x" * 8000},
        {"role": "assistant", "content": "y" * 8000},
        {"role": "user", "content": "final question"},
    ]
    trimmed = chat.trim_conversation(convo, token_budget=1000)
    assert trimmed[-1]["content"] == "final question"
    assert chat.estimate_tokens(trimmed) + 512 <= 2000


def test_export_conversation_writes_markdown(tmp_path):
    state = {"app_dir": str(tmp_path)}
    convo = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    path = chat.export_conversation(state, "my session", convo, system_prompt="be brief")
    text = path.read_text(encoding="utf-8")
    assert path.name == "my_session.md"
    assert "## You" in text and "## Model" in text
    assert "> **System:** be brief" in text


def test_record_benchmark_caps_history(tmp_path):
    from _native import NativeApp

    store = NativeApp(tmp_path)
    state = store.load()
    for i in range(25):
        chat.record_benchmark(store, state, {"model": "m", "predicted_per_second": float(i)})
    history = store.load()["bench_history"]
    assert len(history) == 20 and history[-1]["predicted_per_second"] == 24.0


# --- Auto-tuner ---


class RecordingStore:
    def __init__(self, state):
        self.state = state

    def save(self, state):
        self.state = state


def _tune_state(tmp_path):
    return {
        "disclaimer_ack": True,
        "current_ctx": 8192,
        "current_model": "lfm25-26b",
        "installed_models": [{"id": "lfm25-26b", "quant": "Q5_K_M"}],
        "app_dir": str(tmp_path),
        "logs_dir": str(tmp_path),
    }


def test_autotune_picks_fastest_fit_combo(monkeypatch, tmp_path):
    state = _tune_state(tmp_path)
    store = RecordingStore(state)

    def fake_benchmark_repeat(st, *, max_tokens, repeat):
        return {"predicted_per_second_median": 40.0 if st["optimizations"]["ubatch_size"] >= 512 else 25.0}

    monkeypatch.setattr("bc250_llm_mode.chat.benchmark_repeat", fake_benchmark_repeat)
    monkeypatch.setattr("bc250_llm_mode.server.restart_and_wait", lambda st, runner: {"healthy": True})
    report = tune.autotune(store, state, FakeRunner(), repeat=1, max_tokens=32)
    assert report["winner"]["ubatch_size"] == 512
    assert report["winner"]["predicted_per_second_median"] == 40.0
    assert state["optimizations"]["ubatch_size"] == 512
    assert state["autotune_history"]


def test_autotune_requires_a_current_model(tmp_path):
    state = _tune_state(tmp_path)
    state["current_model"] = None
    with pytest.raises(RuntimeError, match="Install a model"):
        tune.autotune(RecordingStore(state), state, FakeRunner())


def test_autotune_skips_combos_beyond_model_context_limit(monkeypatch, tmp_path):
    state = _tune_state(tmp_path)
    state["current_ctx"] = 131072  # above LFM2.5's trained 128K: nothing can run
    store = RecordingStore(state)
    monkeypatch.setattr(
        "bc250_llm_mode.chat.benchmark_repeat",
        lambda st, *, max_tokens, repeat: {"predicted_per_second_median": 10.0},
    )
    monkeypatch.setattr(
        "bc250_llm_mode.server.restart_and_wait", lambda st, runner: {"healthy": True}
    )
    report = tune.autotune(store, state, FakeRunner(), repeat=1)
    assert report["results"] == [] and report["winner"] is None
    # Original settings restored and still healthy.
    assert store.state["optimizations"]["kv_cache_type"] == "q8_0"
