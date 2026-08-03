from __future__ import annotations

import json
from typing import Any

from .catalog import calculate_fit
from .hardware import detect_hardware
from .local_models import installed_fit_entry
from .logging_utils import CommandRunner, configure_logging
from .optimize import kv_scale_for_settings
from .server import health_check, restart_service, system_metrics
from .state import StateStore


def _dependencies():
    try:
        import httpx
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from rich.console import Console
    except ImportError as exc:
        raise RuntimeError("Chat dependencies are missing. Install the package with: pip install .") from exc
    return httpx, PromptSession, FileHistory, Console


def stream_completion(state: dict[str, Any], history: list[dict[str, str]], on_text) -> str:
    httpx, *_ = _dependencies()
    port = int(state.get("server_port", 8080))
    payload = {"model": state.get("current_model") or "local", "messages": history, "stream": True}
    chunks: list[str] = []
    with httpx.stream(
        "POST", f"http://127.0.0.1:{port}/v1/chat/completions", json=payload, timeout=None
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                text = event["choices"][0]["delta"].get("content") or ""
            except (KeyError, IndexError, json.JSONDecodeError):
                continue
            if text:
                chunks.append(text)
                on_text(text)
    return "".join(chunks)


def _switch_model(store: StateStore, state: dict[str, Any], model_id: str, runner: CommandRunner) -> dict[str, Any]:
    ids = {record.get("id") for record in state.get("installed_models", [])}
    if model_id not in ids:
        raise ValueError(f"Model is not installed: {model_id}")
    record = next(item for item in state["installed_models"] if item.get("id") == model_id)
    entry = installed_fit_entry(record)
    fit = calculate_fit(
        entry,
        str(record["quant"]),
        int(state.get("current_ctx", 8192)),
        kv_scale=kv_scale_for_settings(state.get("optimizations")),
    )
    if fit.verdict == "NO-FIT":
        raise ValueError(fit.detail)
    state["current_model"] = model_id
    store.save(state)
    restart_service(state, runner)
    health_check(state, runner)
    return state


def run_chat(store: StateStore | None = None) -> None:
    httpx, PromptSession, FileHistory, Console = _dependencies()
    store = store or StateStore()
    state = store.load()
    console = Console()
    log = configure_logging(state["logs_dir"])
    runner = CommandRunner(log, lambda line: console.print(f"[dim]{line}[/dim]"))
    history_path = str(store.path.parent / "chat_history")
    session = PromptSession(history=FileHistory(history_path))
    conversation: list[dict[str, str]] = []
    console.print("[bold cyan]BC250 LLM MODE[/bold cyan] — type /help for commands")
    while True:
        try:
            prompt = session.prompt("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not prompt:
            continue
        if prompt == "/quit":
            break
        if prompt == "/help":
            console.print("/help  /status  /model [id]  /ctx <tokens>  /sys  /quit")
            continue
        if prompt == "/status":
            try:
                result = health_check(state, timeout=3)
                report = detect_hardware(state["models_dir"], check_vulkan=False)
                console.print(
                    f"healthy={result['healthy']} model={state.get('current_model')} "
                    f"ctx={state.get('current_ctx')} VRAM={report.vram_used_mib}/{report.vram_total_mib} MiB"
                )
            except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                console.print(f"[red]Server unavailable:[/red] {exc}. Run setup/repair or inspect the service.")
            continue
        if prompt.startswith("/model"):
            parts = prompt.split(maxsplit=1)
            installed = state.get("installed_models", [])
            if len(parts) == 1:
                for item in installed:
                    marker = "*" if item.get("id") == state.get("current_model") else " "
                    console.print(f"{marker} {item.get('id')} ({item.get('quant')})")
            else:
                try:
                    state = _switch_model(store, state, parts[1], runner)
                except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                    console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/ctx"):
            parts = prompt.split()
            try:
                ctx = int(parts[1])
                if ctx < 512 or ctx > 262144:
                    raise ValueError("context must be from 512 to 262144")
                record = next(x for x in state["installed_models"] if x["id"] == state["current_model"])
                entry = installed_fit_entry(record)
                fit = calculate_fit(
                    entry,
                    record["quant"],
                    ctx,
                    kv_scale=kv_scale_for_settings(state.get("optimizations")),
                )
                if fit.verdict == "NO-FIT":
                    raise ValueError(fit.detail)
                state["current_ctx"] = ctx
                store.save(state)
                restart_service(state, runner)
                health_check(state, runner)
                console.print(fit.detail)
            except (IndexError, ValueError, KeyError, StopIteration) as exc:
                console.print(f"[red]Usage: /ctx <tokens> — {exc}[/red]")
            continue
        if prompt == "/sys":
            console.print_json(data=system_metrics())
            continue
        if prompt.startswith("/"):
            console.print("Unknown command; use /help")
            continue
        conversation.append({"role": "user", "content": prompt})
        console.print("[bold green]assistant>[/bold green] ", end="")
        try:
            answer = stream_completion(state, conversation, lambda text: console.print(text, end=""))
            console.print()
            conversation.append({"role": "assistant", "content": answer})
        except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError) as exc:
            conversation.pop()
            console.print(f"\n[red]Server unavailable:[/red] {exc}. Run setup/repair or inspect the service.")
