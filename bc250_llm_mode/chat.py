from __future__ import annotations

import httpx
import json
import re
import time
from pathlib import Path
from typing import Any

from .hardware import detect_hardware
from .catalog import recommend_models
from .llmmode import stage_desktop_boot
from .local_models import discover_local_models
from .logging_utils import CommandRunner, configure_logging
from .model_manager import change_context, change_parallel_slots, switch_model

# P3 §9.4 (DEF-004): every HTTP call is bounded. Streaming generation uses
# per-operation timeouts — a connect bound, and a read bound that applies
# to EACH chunk, so slow tokens are fine but a dead/half-open connection
# can never hang the desktop forever.
CHAT_CONNECT_TIMEOUT_S = 10.0
CHAT_READ_TIMEOUT_S = 120.0
CHAT_WRITE_TIMEOUT_S = 30.0
CHAT_HTTP_TIMEOUT = httpx.Timeout(
    CHAT_READ_TIMEOUT_S,
    connect=CHAT_CONNECT_TIMEOUT_S,
    write=CHAT_WRITE_TIMEOUT_S,
    read=CHAT_READ_TIMEOUT_S,
)


def _next_request_id() -> str:
    import uuid

    return f"chat-{uuid.uuid4().hex[:12]}"


def format_chat_error(exc: BaseException, request_id: str) -> str:
    """P7 §13.1: a recoverable message with the request ID — never a raw
    traceback. Shared terminal-chat semantics via chat_lifecycle."""
    from .chat_lifecycle import classify_exception, recoverable_message

    classification = classify_exception(exc)
    return recoverable_message(classification, request_id)
from .optimize import kv_scale_for_settings
from .openwebui import (
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from .server import (
    ensure_server,
    health_check,
    restart_and_wait,
    service_status,
    start_service,
    stop_service,
    system_metrics,
)
from .sharing import https_sharing_status, start_https_sharing, stop_https_sharing
from .paths import AppPaths
from .tailscale import (
    connect_tailscale,
    disconnect_tailscale,
    restart_tailscale,
    start_tailscale,
    stop_tailscale,
    tailscale_status,
)


def _dependencies():
    try:
        import httpx
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from rich.console import Console
    except ImportError as exc:
        raise RuntimeError("Chat dependencies are missing. Install the package with: pip install .") from exc
    return httpx, PromptSession, FileHistory, Console


def stream_completion(
    state: dict[str, Any],
    history: list[dict[str, str]],
    on_text,
    overrides: dict[str, Any] | None = None,
    on_timings=None,
) -> str:
    httpx, *_ = _dependencies()
    port = int(state.get("server_port", 8080))
    # cache_prompt lets llama.cpp reuse the KV cache for the shared prefix,
    # which keeps multi-turn conversations dramatically faster.
    payload = {
        "model": state.get("current_model") or "local",
        "messages": history,
        "stream": True,
        "cache_prompt": True,
    }
    if overrides:
        payload.update({key: value for key, value in overrides.items() if value is not None})
    chunks: list[str] = []
    with httpx.stream(
        "POST", f"http://127.0.0.1:{port}/v1/chat/completions", json=payload, timeout=CHAT_HTTP_TIMEOUT
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
            if on_timings and isinstance(event.get("timings"), dict):
                on_timings(event["timings"])
            if text:
                chunks.append(text)
                on_text(text)
    return "".join(chunks)


def benchmark(
    state: dict[str, Any],
    prompt: str = "Summarize the advantages of local LLM inference in two sentences.",
    *,
    max_tokens: int = 128,
) -> dict[str, Any]:
    """Timed non-streaming generation using llama.cpp's reported timings."""
    if max_tokens < 1 or max_tokens > 2048:
        raise ValueError("max_tokens must be from 1 to 2048")
    httpx, *_ = _dependencies()
    port = int(state.get("server_port", 8080))
    payload = {
        "model": state.get("current_model") or "local",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
        "cache_prompt": True,
    }
    response = httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=payload, timeout=CHAT_HTTP_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}
    return {
        "model": data.get("model") or payload["model"],
        "prompt_per_second": timings.get("prompt_per_second"),
        "predicted_per_second": timings.get("predicted_per_second"),
        "predicted_tokens": timings.get("predicted_n"),
        "max_tokens": max_tokens,
    }


def benchmark_repeat(
    state: dict[str, Any],
    prompt: str = "Summarize the advantages of local LLM inference in two sentences.",
    *,
    max_tokens: int = 128,
    repeat: int = 3,
) -> dict[str, Any]:
    """Run benchmark() repeatedly and report min/median/max generation speed."""
    if not 1 <= repeat <= 10:
        raise ValueError("repeat must be from 1 to 10")
    runs = [
        benchmark(state, prompt, max_tokens=max_tokens)
        for _ in range(repeat)
    ]
    speeds = sorted(
        run["predicted_per_second"] for run in runs if run["predicted_per_second"] is not None
    )
    summary: dict[str, Any] = {"repeat": repeat, "runs": runs}
    if speeds:
        middle = len(speeds) // 2
        median = (
            speeds[middle] if len(speeds) % 2 else (speeds[middle - 1] + speeds[middle]) / 2
        )
        summary.update(
            predicted_per_second_min=speeds[0],
            predicted_per_second_median=median,
            predicted_per_second_max=speeds[-1],
        )
    return summary


def conversations_dir(state: dict[str, Any]) -> Path:
    path = Path(str(state["app_dir"])).expanduser() / "conversations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def conversation_path(state: dict[str, Any], name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name.strip()) or "default"
    return conversations_dir(state) / f"{safe}.json"


def save_conversation(state: dict[str, Any], name: str, conversation: list[dict[str, str]]) -> Path:
    if not conversation:
        raise ValueError("Nothing to save; the conversation is empty")
    path = conversation_path(state, name)
    path.write_text(json.dumps(conversation, indent=2), encoding="utf-8")
    return path


def load_conversation(state: dict[str, Any], name: str) -> list[dict[str, str]]:
    path = conversation_path(state, name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"No saved conversation named {name!r}") from exc
    if (
        not isinstance(data, list)
        or not data
        or not all(
            isinstance(item, dict)
            and item.get("role") in {"user", "assistant", "system"}
            and isinstance(item.get("content"), str)
            for item in data
        )
    ):
        raise ValueError(f"Saved conversation {path.name} is malformed")
    return data


class ThinkFilter:
    """Streams text while suppressing <think>...</think> reasoning spans.

    Handles tags split across chunk boundaries; unterminated think blocks are
    dropped entirely. The full raw text still reaches the stored conversation.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.pending = ""

    def feed(self, chunk: str) -> str:
        self.pending += chunk
        out: list[str] = []
        while self.pending:
            tag = self.CLOSE if self.inside else self.OPEN
            index = self.pending.find(tag)
            if index == -1:
                keep = len(tag) - 1
                if len(self.pending) > keep:
                    emit = self.pending[:-keep] if keep else self.pending
                    if not self.inside:
                        out.append(emit)
                    self.pending = self.pending[len(emit):]
                break
            before = self.pending[:index]
            if before and not self.inside:
                out.append(before)
            self.pending = self.pending[index + len(tag):]
            self.inside = not self.inside
        return "".join(out)

    def flush(self) -> str:
        rest, self.pending = self.pending, ""
        return "" if self.inside else rest


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Cheap ~4 chars/token estimate, good enough for trimming decisions."""
    return sum(len(message.get("content", "")) // 4 + 4 for message in messages)


def trim_conversation(
    conversation: list[dict[str, str]], token_budget: int, *, reserve: int = 512
) -> list[dict[str, str]]:
    """Drop oldest turns until the estimate fits the budget; keeps the last exchange."""
    trimmed = list(conversation)
    while len(trimmed) > 2 and estimate_tokens(trimmed) + reserve > token_budget:
        drop = 2 if trimmed[0].get("role") == "user" else 1
        trimmed = trimmed[drop:]
    return trimmed


def export_conversation(
    state: dict[str, Any],
    name: str,
    conversation: list[dict[str, str]],
    system_prompt: str | None = None,
) -> Path:
    if not conversation:
        raise ValueError("Nothing to export; the conversation is empty")
    safe_name = re.sub(r"\W", "_", name.strip()) or "default"
    path = conversations_dir(state) / f"{safe_name}.md"
    lines = [f"# BC250 LLM MODE — {name}", ""]
    if system_prompt:
        lines += ["> **System:** " + system_prompt.replace("\n", "\n> "), ""]
    for message in conversation:
        role = "You" if message["role"] == "user" else "Model"
        lines += [f"## {role}", "", message["content"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def record_benchmark(store: Any, state: dict[str, Any], result: dict[str, Any]) -> None:
    """Keep the last 20 benchmark results so tuning changes can be compared.

    Durable records go through the capped repository on a dedicated
    per-command connection (no prompts or generated content stored).
    Handles without a database (in-memory test doubles) mutate only the
    passed draft; nothing durable is ever written wholesale.
    """
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **{key: result.get(key) for key in (
            "model", "prompt_per_second", "predicted_per_second", "predicted_tokens", "max_tokens",
        )},
        "model": result.get("model") or state.get("current_model"),
        "context": state.get("current_ctx"),
        "slots": (state.get("optimizations") or {}).get("parallel_slots"),
    }

    paths = getattr(store, "paths", None)
    if paths is not None:
        # U1.1 §8.6: frontends never import repositories; the composed
        # application owns the durable write.
        store.record_benchmark(entry)
        return

    history = [item for item in (state.get("bench_history") or []) if isinstance(item, dict)]
    state["bench_history"] = ([*history, entry])[-20:]


def _print_help(console) -> None:
    console.print(
        "\n".join((
            "/status                         application, server, and VRAM status",
            "/model [id]                     list or switch installed models",
            "/scan                           find compatible GGUF files on disk",
            "/ctx <tokens>                   change context after a VRAM fit check",
            "/slots <1-8>                    set concurrent users after a VRAM fit check",
            "/llm start|stop|restart|status|ensure manage the single model service",
            "/recommend [tag]               suggest catalog models that fit the current budget",
            "/webui start|stop|restart|status manage optional Open WebUI",
            "/tailscale start|stop|restart|status|connect|disconnect",
            "/serve start|stop|restart|status publish UI and API over tailnet HTTPS",
            "/boot [status|desktop]           next-boot desktop safety policy",
            "/logs [server|setup] [lines]    show recent logs (maximum 1000 lines)",
            "/sys                            GPU temperature/clocks/VRAM",
            "/bench [tokens]                 measure generation speed (tokens/second)",
            "/save [name]                    save this conversation to disk",
            "/load [name]                    load a saved conversation",
            "/system [text|clear]            show, set, or clear the system prompt",
            "/temp <0.0-2.0|off>             per-request temperature override",
            "/think on|off                   hide (or show) <think> reasoning blocks",
            "/trim [messages]                drop oldest turns (auto-trim also runs near the context limit)",
            "/export [name]                  export this conversation as Markdown",
            "/retry                          regenerate the last answer",
            "/clear                          clear this session's conversation",
            "/help                           show this list",
            "/quit                           exit",
        ))
    )


def run_chat(application) -> None:
    httpx, PromptSession, FileHistory, Console = _dependencies()
    state = application.read_model()
    console = Console()
    log = configure_logging(state["logs_dir"])
    runner = CommandRunner(log, lambda line: console.print(f"[dim]{line}[/dim]"))
    history_path = str(application.paths.app_dir / "chat_history")
    session = PromptSession(history=FileHistory(history_path))
    conversation: list[dict[str, str]] = []
    overrides: dict[str, Any] = {}
    system_prompt: str | None = None
    think_hidden = False

    def request_history() -> list[dict[str, str]]:
        # Auto-trim so long sessions stay inside the per-slot context budget.
        conversation[:] = trim_conversation(conversation, int(state.get("current_ctx", 8192)))
        if system_prompt:
            return [{"role": "system", "content": system_prompt}, *conversation]
        return list(conversation)

    def generate() -> None:
        console.print("[bold green]assistant>[/bold green] ", end="")
        filter_ = ThinkFilter() if think_hidden else None

        def printer(text: str) -> None:
            if filter_ is not None:
                text = filter_.feed(text)
            if text:
                console.print(text, end="")

        def show_timings(timings: dict[str, Any]) -> None:
            speed = timings.get("predicted_per_second")
            if speed:
                console.print(f"  [dim]· {float(speed):.1f} tok/s[/dim]", end="")

        answer = stream_completion(
            state, request_history(), printer, overrides, show_timings
        )
        if filter_ is not None:
            tail = filter_.flush()
            if tail:
                console.print(tail, end="")
        console.print()
        conversation.append({"role": "assistant", "content": answer})

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
            _print_help(console)
            continue
        if prompt == "/status":
            try:
                report = detect_hardware(state["models_dir"], check_vulkan=False)
                llm = service_status(state, runner)
                result: dict[str, Any] = {
                    "llm": llm,
                    "openwebui": open_webui_status(state, runner),
                    "tailscale": tailscale_status(runner),
                    "https_sharing": https_sharing_status(state, runner),
                    "model": state.get("current_model"),
                    "ctx": state.get("current_ctx"),
                    "parallel_slots": state.get("optimizations", {}).get("parallel_slots", 4),
                    "boot_policy": state.get("boot_policy", "desktop"),
                    "llm_autostart": False,
                    "vram_used_mib": report.vram_used_mib,
                    "vram_total_mib": report.vram_total_mib,
                }
                if llm["active"]:
                    try:
                        result["server"] = health_check(state, timeout=3)
                    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                        result["server"] = {"healthy": False, "error": str(exc)}
                console.print_json(data=result)
            except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                console.print(f"[red]Status unavailable:[/red] {exc}. Run setup/repair or inspect the logs.")
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
                    state = switch_model(store, state, parts[1], runner)
                except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                    console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/ctx"):
            parts = prompt.split()
            try:
                console.print(change_context(store, state, int(parts[1]), runner))
            except (IndexError, ValueError, KeyError, StopIteration) as exc:
                console.print(f"[red]Usage: /ctx <tokens> — {exc}[/red]")
            continue
        if prompt.startswith("/slots"):
            parts = prompt.split()
            try:
                console.print(change_parallel_slots(store, state, int(parts[1]), runner))
            except (IndexError, ValueError, KeyError, StopIteration) as exc:
                console.print(f"[red]Usage: /slots <1-8> — {exc}[/red]")
            continue
        if prompt == "/scan":
            discovery = discover_local_models(state)
            for item in discovery.models:
                console.print(f"{item.id}  {item.quant}  {item.weights_gib:.2f} GiB  {item.path}")
            console.print(f"Found {len(discovery.models)} model(s); rejected {len(discovery.rejected)} artifact(s).")
            continue
        if prompt.startswith("/llm"):
            parts = prompt.split()
            action = parts[1] if len(parts) > 1 else "status"
            actions = {
                "start": lambda: start_service(state, runner),
                "stop": lambda: stop_service(state, runner),
                "restart": lambda: restart_and_wait(state, runner),
                "status": lambda: service_status(state, runner),
                "ensure": lambda: ensure_server(state, runner),
            }
            try:
                before_llm = dict(state)
                console.print_json(data=actions[action]())
                application.commit_settings_changes(before_llm, state)
            except KeyError:
                console.print("[red]Usage: /llm start|stop|restart|status|ensure[/red]")
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/recommend"):
            parts = prompt.split(maxsplit=1)
            tag = parts[1] if len(parts) > 1 else None
            try:
                ranked = recommend_models(
                    int(state.get("current_ctx", 8192)),
                    parallel_slots=int(state.get("optimizations", {}).get("parallel_slots", 4)),
                    kv_scale=kv_scale_for_settings(state.get("optimizations")),
                    tag=tag,
                    limit=8,
                )
                if not ranked:
                    console.print("No catalog model safely fits the current context/slots budget.")
                for model, quant, fit in ranked:
                    console.print(
                        f"[cyan]{model.id}[/cyan] {quant} — {fit.detail}\n"
                        f"  {model.display_name}: {model.notes}"
                    )
            except ValueError as exc:
                console.print(f"[red]Usage: /recommend [tag] — {exc}[/red]")
            continue
        if prompt.startswith("/webui"):
            parts = prompt.split()
            action = parts[1] if len(parts) > 1 else "status"
            actions = {
                "start": lambda: start_open_webui(state, runner),
                "stop": lambda: stop_open_webui(state, runner),
                "restart": lambda: restart_open_webui(state, runner),
                "status": lambda: open_webui_status(state, runner),
            }
            try:
                before_webui = dict(state)
                console.print_json(data=actions[action]())
                application.commit_settings_changes(before_webui, state)
            except KeyError:
                console.print("[red]Usage: /webui start|stop|restart|status[/red]")
            except (OSError, RuntimeError, ValueError) as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/tailscale"):
            parts = prompt.split()
            action = parts[1] if len(parts) > 1 else "status"
            actions = {
                "start": lambda: start_tailscale(runner),
                "stop": lambda: stop_tailscale(runner),
                "restart": lambda: restart_tailscale(runner),
                "status": lambda: tailscale_status(runner),
                "connect": lambda: connect_tailscale(runner),
                "disconnect": lambda: disconnect_tailscale(runner),
            }
            try:
                console.print_json(data=actions[action]())
            except KeyError:
                console.print("[red]Usage: /tailscale start|stop|restart|status|connect|disconnect[/red]")
            except (OSError, RuntimeError, ValueError) as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/serve"):
            parts = prompt.split()
            action = parts[1] if len(parts) > 1 else "status"
            actions = {
                "start": lambda: start_https_sharing(state, runner),
                "stop": lambda: stop_https_sharing(state, runner),
                "restart": lambda: start_https_sharing(state, runner),
                "status": lambda: https_sharing_status(state, runner),
            }
            try:
                before_serve = dict(state)
                console.print_json(data=actions[action]())
                application.commit_settings_changes(before_serve, state)
            except KeyError:
                console.print("[red]Usage: /serve start|stop|restart|status[/red]")
            except (OSError, RuntimeError, ValueError) as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/logs"):
            parts = prompt.split()
            kind = parts[1] if len(parts) > 1 else "server"
            try:
                lines = int(parts[2]) if len(parts) > 2 else 80
                if kind not in {"server", "setup"} or not 1 <= lines <= 1000:
                    raise ValueError("kind must be server/setup and lines 1–1000")
                path = (
                    str(state.get("server_log", "/var/log/bc250-llm-server.log"))
                    if kind == "server"
                    else f"{state['logs_dir']}/setup.log"
                )
                result = runner.run(["tail", "-n", str(lines), path], check=False)
                if result.returncode:
                    raise RuntimeError(f"Could not read {path}")
            except (IndexError, ValueError, RuntimeError) as exc:
                console.print(f"[red]Usage: /logs [server|setup] [lines] — {exc}[/red]")
            continue
        if prompt.startswith("/boot"):
            parts = prompt.split()
            action = parts[1] if len(parts) > 1 else "status"
            try:
                before_boot = dict(state)
                if action == "desktop":
                    stage_desktop_boot(state, runner)
                    application.commit_settings_changes(before_boot, state)
                elif action != "status":
                    raise ValueError("action must be status or desktop")
                target = runner.run(["systemctl", "get-default"], check=False).stdout.strip()
                console.print_json(data={
                    "policy": state.get("boot_policy", "desktop"),
                    "next_boot_target": target or "unknown",
                    "llm_autostart": False,
                })
            except (OSError, RuntimeError, ValueError) as exc:
                console.print(f"[red]Usage: /boot [status|desktop] — {exc}[/red]")
            continue
        if prompt == "/sys":
            console.print_json(data=system_metrics())
            continue
        if prompt.startswith("/bench"):
            parts = prompt.split(maxsplit=1)
            try:
                max_tokens = int(parts[1]) if len(parts) > 1 else 128
                result = benchmark(state, max_tokens=max_tokens)
                console.print_json(data=result)
                record_benchmark(application, state, result)
            except (IndexError, ValueError) as exc:
                console.print(f"[red]Usage: /bench [tokens 1-2048] — {exc}[/red]")
            except (httpx.HTTPError, OSError, RuntimeError, KeyError) as exc:
                console.print(f"[red]Benchmark failed:[/red] {exc}. Is the model server running?")
            continue
        if prompt.startswith("/save"):
            parts = prompt.split(maxsplit=1)
            name = parts[1] if len(parts) > 1 else "default"
            try:
                path = save_conversation(state, name, conversation)
                console.print(f"Saved to [cyan]{path}[/cyan]")
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/load"):
            parts = prompt.split(maxsplit=1)
            name = parts[1] if len(parts) > 1 else "default"
            try:
                loaded = load_conversation(state, name)
                system_prompt = next(
                    (item["content"] for item in loaded if item["role"] == "system"), system_prompt
                )
                conversation.clear()
                conversation.extend(item for item in loaded if item["role"] != "system")
                console.print(f"Loaded {len(conversation)} message(s) from {name!r}.")
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if prompt.startswith("/system"):
            parts = prompt.split(maxsplit=1)
            if len(parts) == 1:
                console.print(system_prompt or "No system prompt is set.")
            elif parts[1].strip().lower() == "clear":
                system_prompt = None
                console.print("System prompt cleared.")
            else:
                system_prompt = parts[1].strip()
                console.print("System prompt set.")
            continue
        if prompt.startswith("/temp"):
            parts = prompt.split(maxsplit=1)
            try:
                if len(parts) == 1 or parts[1].strip().lower() == "off":
                    overrides.pop("temperature", None)
                    console.print("Temperature override cleared; server default applies.")
                else:
                    value = float(parts[1])
                    if not 0.0 <= value <= 2.0:
                        raise ValueError("temperature must be between 0.0 and 2.0")
                    overrides["temperature"] = value
                    console.print(f"Temperature override set to {value}.")
            except ValueError as exc:
                console.print(f"[red]Usage: /temp <0.0-2.0|off> — {exc}[/red]")
            continue
        if prompt.startswith("/think"):
            parts = prompt.split(maxsplit=1)
            mode = parts[1].strip().lower() if len(parts) > 1 else ""
            if mode == "on":
                think_hidden = True
                console.print("<think> blocks will be hidden; they are still sent to the model.")
            elif mode == "off":
                think_hidden = False
                console.print("<think> blocks will be shown.")
            else:
                console.print("Reasoning blocks are currently " + ("hidden." if think_hidden else "shown.") + " Use /think on|off.")
            continue
        if prompt.startswith("/trim"):
            parts = prompt.split()
            try:
                keep = int(parts[1]) if len(parts) > 1 else 10
                if keep < 2:
                    raise ValueError("keep at least 2 messages")
                if len(conversation) > keep:
                    dropped = len(conversation) - keep
                    conversation[:] = conversation[-keep:]
                    console.print(f"Dropped {dropped} oldest message(s); {len(conversation)} remain (~{estimate_tokens(conversation)} tokens).")
                else:
                    console.print(f"Only {len(conversation)} message(s); nothing to trim (~{estimate_tokens(conversation)} tokens).")
            except ValueError as exc:
                console.print(f"[red]Usage: /trim [messages >= 2] — {exc}[/red]")
            continue
        if prompt.startswith("/export"):
            parts = prompt.split(maxsplit=1)
            name = parts[1] if len(parts) > 1 else "transcript"
            try:
                path = export_conversation(state, name, conversation, system_prompt)
                console.print(f"Exported to [cyan]{path}[/cyan]")
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if prompt == "/retry":
            while conversation and conversation[-1]["role"] == "assistant":
                conversation.pop()
            if not conversation:
                console.print("[red]Nothing to retry yet.[/red]")
                continue
            try:
                generate()
            except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError) as exc:
                console.print(f"\n[red]{format_chat_error(exc, _next_request_id())}[/red]")
            continue
        if prompt == "/clear":
            conversation.clear()
            console.print("Conversation cleared.")
            continue
        if prompt.startswith("/"):
            console.print("Unknown command; use /help")
            continue
        conversation.append({"role": "user", "content": prompt})
        try:
            generate()
        except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError) as exc:
            conversation.pop()
            console.print(f"\n[red]{format_chat_error(exc, _next_request_id())}[/red]")
