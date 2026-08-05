from __future__ import annotations

import json
from typing import Any

from .hardware import detect_hardware
from .llmmode import stage_desktop_boot
from .local_models import discover_local_models
from .logging_utils import CommandRunner, configure_logging
from .model_manager import change_context, change_parallel_slots, switch_model
from .openwebui import (
    open_webui_status,
    restart_open_webui,
    start_open_webui,
    stop_open_webui,
)
from .server import (
    health_check,
    restart_and_wait,
    service_status,
    start_service,
    stop_service,
    system_metrics,
)
from .sharing import https_sharing_status, start_https_sharing, stop_https_sharing
from .state import StateStore
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


def _print_help(console) -> None:
    console.print(
        "\n".join((
            "/status                         application, server, and VRAM status",
            "/model [id]                     list or switch installed models",
            "/scan                           find compatible GGUF files on disk",
            "/ctx <tokens>                   change context after a VRAM fit check",
            "/slots <1-8>                    set concurrent users after a VRAM fit check",
            "/llm start|stop|restart|status  manage the single model service",
            "/webui start|stop|restart|status manage optional Open WebUI",
            "/tailscale start|stop|restart|status|connect|disconnect",
            "/serve start|stop|restart|status publish UI and API over tailnet HTTPS",
            "/boot [status|desktop]           next-boot desktop safety policy",
            "/logs [server|setup] [lines]    show recent logs (maximum 1000 lines)",
            "/sys                            GPU temperature/clocks/VRAM",
            "/clear                          clear this session's conversation",
            "/help                           show this list",
            "/quit                           exit",
        ))
    )


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
            }
            try:
                console.print_json(data=actions[action]())
            except KeyError:
                console.print("[red]Usage: /llm start|stop|restart|status[/red]")
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                console.print(f"[red]{exc}[/red]")
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
                console.print_json(data=actions[action]())
                store.save(state)
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
                console.print_json(data=actions[action]())
                store.save(state)
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
                if action == "desktop":
                    stage_desktop_boot(state, runner)
                    store.save(state)
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
        if prompt == "/clear":
            conversation.clear()
            console.print("Conversation cleared.")
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
