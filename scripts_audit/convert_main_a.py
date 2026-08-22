"""__main__ conversion part A: app wiring, repair, llm, llamacpp."""

from pathlib import Path

p = Path("bc250_llm_mode/__main__.py")
text = p.read_text(encoding="utf-8")

text = text.replace(
    "    if args.state:\n"
    "        store = StateStore(args.state)\n"
    "        state = store.load()\n"
    "    else:\n"
    "        application = Application.compose()\n",
    "    if args.state:\n"
    "        store = StateStore(args.state)\n"
    "        state = store.load()\n"
    "        application = Application.wrap(store)\n"
    "    else:\n"
    "        application = Application.compose()\n",
)

text = text.replace(
    '        state["setup_phase"] = 0\n'
    '        state["setup_complete"] = False\n'
    "        store.save(state)\n",
    "        if application.setup is not None:\n"
    '            reset = application.setup.reset_for_repair("repair command")\n'
    '            state.update(setup_complete=False, setup_phase=reset["phase"])\n'
    "        else:\n"
    '            state.update(setup_complete=False, setup_phase=0)\n',
)

text = text.replace(
    "            result = actions[args.action]()\n"
    "        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:\n"
    '            print(f"error: {exc}", file=sys.stderr)\n'
    "            return 1\n"
    '        if args.action != "status":\n'
    "            store.save(state)\n",
    "        before = dict(state)\n"
    "            result_placeholder = None\n",
)

# Rebuild llm handler block cleanly (previous replace is a marker swap).
text = text.replace(
    "        before = dict(state)\n"
    "            result_placeholder = None\n",
    "        before = dict(state)\n"
    "        try:\n"
    "            result = actions[args.action]()\n"
    "        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:\n"
    '            print(f"error: {exc}", file=sys.stderr)\n'
    "            return 1\n"
    '        if args.action != "status":\n'
    "            application.persist_state_changes(before, state)\n",
)

text = text.replace(
    '        if args.action == "update":\n'
    "            result = update_llamacpp(state, runner, tag=args.tag)\n"
    "        else:\n"
    "            result = rollback_llamacpp(state, runner)\n"
    "        store.save(state)\n",
    "        before = dict(state)\n"
    '        if args.action == "update":\n'
    "            result = application.component.update_llamacpp(\n"
    "                state, runner, tag=args.tag\n"
    "            )\n"
    "        else:\n"
    "            result = application.component.rollback_llamacpp(state, runner)\n"
    "        application.persist_state_changes(before, state)\n",
)

p.write_text(text, encoding="utf-8")
print("A done; remaining saves:", text.count("store.save("))