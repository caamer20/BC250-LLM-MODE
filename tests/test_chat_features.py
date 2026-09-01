import json
from types import SimpleNamespace

import pytest

from bc250_llm_mode import __main__ as cli
from bc250_llm_mode import chat


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "model": "test-model",
            "timings": {"prompt_per_second": 512.0, "predicted_per_second": 21.5, "predicted_n": 128},
        }


class FakeStream:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def raise_for_status(self) -> None:
        self.response.raise_for_status()

    def iter_lines(self):
        return iter((
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"delta":{}}],"timings":{"predicted_per_second":19.0}}',
            "data: [DONE]",
        ))


class FakeHttpx:
    def __init__(self) -> None:
        self.stream_calls: list[dict] = []
        self.post_calls: list[dict] = []

    def stream(self, method, url, **kwargs):
        self.stream_calls.append({"method": method, "url": url, **kwargs})
        return FakeStream(FakeResponse())

    def post(self, url, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        return FakeResponse()


@pytest.fixture
def fake_httpx(monkeypatch):
    fake = FakeHttpx()
    monkeypatch.setattr(chat, "_dependencies", lambda: (fake, None, None, None))
    return fake


def test_stream_completion_enables_prompt_caching_and_overrides(fake_httpx):
    state = {"server_port": 8080, "current_model": "m"}
    history = [{"role": "user", "content": "hi"}]
    text = chat.stream_completion(
        state, history, lambda _t: None, overrides={"temperature": 0.7, "bogus": None}
    )
    assert text == "Hello"
    call = fake_httpx.stream_calls[0]
    assert call["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert call["json"]["cache_prompt"] is True
    assert call["json"]["temperature"] == 0.7
    assert "bogus" not in call["json"]


def test_benchmark_reports_llamacpp_timings(fake_httpx):
    result = chat.benchmark({"server_port": 8081}, max_tokens=64)
    assert result["predicted_per_second"] == 21.5
    assert result["max_tokens"] == 64
    assert fake_httpx.post_calls[0]["url"] == "http://127.0.0.1:8081/v1/chat/completions"
    payload = fake_httpx.post_calls[0]["json"]
    assert payload["stream"] is False and payload["max_tokens"] == 64


def test_benchmark_rejects_out_of_range_tokens(fake_httpx):
    with pytest.raises(ValueError):
        chat.benchmark({"server_port": 8080}, max_tokens=0)


def test_conversation_save_load_roundtrip(tmp_path):
    state = {"app_dir": str(tmp_path)}
    conversation = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    path = chat.save_conversation(state, "session one", conversation)
    assert path.name == "session_one.json"
    loaded = chat.load_conversation(state, "session one")
    assert loaded == conversation


def test_save_empty_and_load_missing_are_errors(tmp_path):
    state = {"app_dir": str(tmp_path)}
    with pytest.raises(ValueError):
        chat.save_conversation(state, "empty", [])
    with pytest.raises(ValueError):
        chat.load_conversation(state, "missing")


def test_load_rejects_malformed_payload(tmp_path):
    state = {"app_dir": str(tmp_path)}
    chat.conversations_dir(state)
    (tmp_path / "conversations" / "bad.json").write_text('{"nope": true}', encoding="utf-8")
    with pytest.raises(ValueError):
        chat.load_conversation(state, "bad")


def _patch_cli_services(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda *_args: None)
    runner = SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
        emit=lambda *_line: None,
    )
    monkeypatch.setattr(cli, "CommandRunner", lambda *_args, **_kwargs: runner)
    monkeypatch.setattr(cli, "detect_hardware", lambda *_a, **_k: SimpleNamespace(
        to_dict=lambda: {"valid": True}, valid=True
    ))
    monkeypatch.setattr(cli, "analyze_memory_profile", lambda *_a: SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr(cli, "service_status", lambda *_a: {"active": False, "enabled": False})
    monkeypatch.setattr(cli, "open_webui_status", lambda *_a: {"running": False})
    monkeypatch.setattr(cli, "tailscale_status", lambda *_a: {"running": False})
    monkeypatch.setattr(cli, "https_sharing_status", lambda *_a: {"enabled": False})


def test_cli_models_search_lists_matches_with_recommendations(tmp_path, monkeypatch, capsys):
    _patch_cli_services(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert cli.main(["models", "search", "reasoning"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["count"] >= 5
    top = report["results"][0]
    assert {"id", "display_name", "recommended_quant", "verdict"} <= set(top)


def test_cli_doctor_prints_json_report(tmp_path, monkeypatch, capsys):
    _patch_cli_services(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert cli.main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state_readable"] is True
    assert "models_dir_free_gib" in report


def test_cli_bench_uses_chat_benchmark(tmp_path, monkeypatch, capsys):
    _patch_cli_services(monkeypatch)
    sent = {}

    def fake_benchmark(state, prompt, *, max_tokens):
        sent.update({"prompt": prompt, "max_tokens": max_tokens})
        return {"predicted_per_second": 20.0}

    monkeypatch.setattr(cli, "benchmark", fake_benchmark)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert cli.main(["bench", "--max-tokens", "32", "--repeat", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["predicted_per_second"] == 20.0
    assert sent["max_tokens"] == 32


# --- Round 2: recommendations, streaming timings, repeat benchmarks, self-healing ---


def test_recommend_models_ranks_best_fitting_first():
    from bc250_llm_mode.catalog import recommend_models, validation_tier

    ranked = recommend_models(32768, parallel_slots=4)
    assert ranked
    assert all(fit.verdict == "FITS" for _model, _quant, fit in ranked)
    assert all(validation_tier(model) == "supported" for model, _quant, _fit in ranked)
    assert [model.id for model, _quant, _fit in ranked] == sorted(
        model.id for model, _quant, _fit in ranked)


def test_recommend_models_filters_by_tag_and_limit():
    from bc250_llm_mode.catalog import recommend_models

    ranked = recommend_models(8192, parallel_slots=4, tag="code", limit=3)
    assert 0 < len(ranked) <= 3
    for model, _quant, _fit in ranked:
        assert "code" in model.task_tags


def test_recommend_models_never_suggests_non_fitting():
    from bc250_llm_mode.catalog import recommend_models

    # Phi-4 14B cannot fit four 32K slots in any quantization.
    assert all(
        model.id != "phi4-14b"
        for model, _quant, _fit in recommend_models(32768, parallel_slots=4)
    )


def test_catalog_rows_shape_and_fit(tmp_path):
    from bc250_llm_mode.catalog import catalog_rows

    rows = catalog_rows("gemma-2-9b-it")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "gemma-2-9b-it"
    # Q6 is TIGHT at 8K; Q5_K_M is the highest-fidelity quant that safely fits.
    assert row["recommended_quant"] == "Q5_K_M"
    assert row["verdict"] == "FITS"
    assert row["required_gib"] == pytest.approx(9.94, abs=0.02)


def test_stream_completion_reports_timings(fake_httpx):
    seen = []
    chat.stream_completion(
        {"server_port": 8080}, [{"role": "user", "content": "hi"}], lambda _t: None,
        on_timings=seen.append,
    )
    assert seen == [{"predicted_per_second": 19.0}]


def test_benchmark_repeat_aggregates_statistics(fake_httpx):
    summary = chat.benchmark_repeat({"server_port": 8080}, repeat=3, max_tokens=16)
    assert summary["repeat"] == 3
    assert len(summary["runs"]) == 3
    assert summary["predicted_per_second_min"] == 21.5
    assert summary["predicted_per_second_median"] == 21.5
    assert summary["predicted_per_second_max"] == 21.5


def test_benchmark_repeat_rejects_bad_repeat(fake_httpx):
    with pytest.raises(ValueError):
        chat.benchmark_repeat({"server_port": 8080}, repeat=0)


def test_cli_models_recommend(tmp_path, monkeypatch, capsys):
    _patch_cli_services(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert cli.main([
        "models", "recommend", "--ctx", "32768", "--slots", "4", "--limit", "3",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["context_tokens"] == 32768
    assert 0 < report["count"] <= 3
    assert {row["verdict"] for row in report["results"]} == {"FITS"}


def test_ensure_server_starts_when_stopped(monkeypatch):
    from bc250_llm_mode import server

    monkeypatch.setattr(server, "service_status", lambda *_a: {"active": False})
    monkeypatch.setattr(server, "restart_and_wait", lambda *_a: {"healthy": True, "n_ctx": 8192})
    result = server.ensure_server({"current_model": "m"}, runner=None)
    assert result["ensured"] == "started"
    assert result["healthy"] is True


def test_ensure_server_keeps_healthy_server(monkeypatch):
    from bc250_llm_mode import server

    monkeypatch.setattr(server, "service_status", lambda *_a: {"active": True})
    monkeypatch.setattr(server, "health_check", lambda *_a, **_k: {"healthy": True})
    result = server.ensure_server({}, runner=None)
    assert result["ensured"] == "already-healthy"


def test_ensure_server_restarts_unhealthy_server(monkeypatch):
    from bc250_llm_mode import server

    def broken(*_a, **_k):
        raise TimeoutError("no health")

    monkeypatch.setattr(server, "service_status", lambda *_a: {"active": True})
    monkeypatch.setattr(server, "health_check", broken)
    monkeypatch.setattr(server, "restart_and_wait", lambda *_a: {"healthy": True})
    messages = []
    runner = SimpleNamespace(emit=messages.append)
    result = server.ensure_server({}, runner=runner)
    assert result["ensured"] == "restarted"
    assert messages


def test_ensure_server_requires_an_installed_model(monkeypatch):
    from bc250_llm_mode import server

    monkeypatch.setattr(server, "service_status", lambda *_a: {"active": False})
    with pytest.raises(RuntimeError, match="No model is installed"):
        server.ensure_server({}, runner=None)
