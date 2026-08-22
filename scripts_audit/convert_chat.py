"""Chat conversion: composition injection, remove saves and fallbacks."""

from pathlib import Path

p = Path("bc250_llm_mode/chat.py")
t = p.read_text(encoding="utf-8")

old_head = (
    "def run_chat(store: StateStore | None = None, paths: AppPaths | None = None) -> None:\n"
    "    httpx, PromptSession, FileHistory, Console = _dependencies()\n"
    "    # Path authority: an injected profile wins; otherwise derive from the\n"
    "    # loaded state's own state_path (never a fresh home evaluation).\n"
    "    if store is None:\n"
    '        derived = (paths.state_path if paths else None) or probe.get("state_path")\n'
    "        store = StateStore(derived) if derived else StateStore()\n"
    "    state = store.load()\n"
)
new_head = (
    "def run_chat(application) -> None:\n"
    "    httpx, PromptSession, FileHistory, Console = _dependencies()\n"
    "    store = application.store\n"
    "    snapshot = (\n"
    "        application.query.snapshot()\n"
    "        if application.query is not None else None\n"
    "    )\n"
    "    state = snapshot.data if snapshot is not None else store.load()\n"
)
assert old_head in t, "run_chat head anchor missing"
t = t.replace(old_head, new_head)

pairs = [
    (
        '                console.print_json(data=actions[action]())\n'
        '                store.save(state)\n'
        '            except KeyError:\n'
        '                console.print("[red]Usage: /llm start|stop|restart|status|ensure[/red]")\n',
        '                before_llm = dict(state)\n'
        '                console.print_json(data=actions[action]())\n'
        '                application.persist_state_changes(before_llm, state)\n'
        '            except KeyError:\n'
        '                console.print("[red]Usage: /llm start|stop|restart|status|ensure[/red]")\n',
    ),
    (
        '                console.print_json(data=actions[action]())\n'
        '                store.save(state)\n'
        '            except KeyError:\n'
        '                console.print("[red]Usage: /webui start|stop|restart|status[/red]")\n',
        '                before_webui = dict(state)\n'
        '                console.print_json(data=actions[action]())\n'
        '                application.persist_state_changes(before_webui, state)\n'
        '            except KeyError:\n'
        '                console.print("[red]Usage: /webui start|stop|restart|status[/red]")\n',
    ),
    (
        '                console.print_json(data=actions[action]())\n'
        '                store.save(state)\n'
        '            except KeyError:\n'
        '                console.print("[red]Usage: /serve start|stop|restart|status[/red]")\n',
        '                before_serve = dict(state)\n'
        '                console.print_json(data=actions[action]())\n'
        '                application.persist_state_changes(before_serve, state)\n'
        '            except KeyError:\n'
        '                console.print("[red]Usage: /serve start|stop|restart|status[/red]")\n',
    ),
    (
        '                if action == "desktop":\n'
        '                    stage_desktop_boot(state, runner)\n'
        '                    store.save(state)\n',
        '                before_boot = dict(state)\n'
        '                if action == "desktop":\n'
        '                    stage_desktop_boot(state, runner)\n'
        '                    application.persist_state_changes(before_boot, state)\n',
    ),
]
for old, new in pairs:
    assert old in t, old[:80]
    t = t.replace(old, new)

p.write_text(t, encoding="utf-8")
print("chat saves left:", t.count("store.save("))