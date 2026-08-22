"""__main__ conversion part B: webui, share, install-model, boot, modes."""

from pathlib import Path

p = Path("bc250_llm_mode/__main__.py")
text = p.read_text(encoding="utf-8")

text = text.replace(
    '        actions = {\n'
    '            "start": lambda: start_open_webui(state, runner),\n'
    '            "stop": lambda: stop_open_webui(state, runner),\n'
    '            "restart": lambda: restart_open_webui(state, runner),\n'
    '            "status": lambda: open_webui_status(state, runner),\n'
    "        }\n"
    "        result = actions[args.action]()\n"
    "        store.save(state)\n",
    '        svc = application.openwebui\n'
    "        actions = {\n"
    '            "start": lambda: svc.start(state, runner),\n'
    '            "stop": lambda: svc.stop(state, runner),\n'
    '            "restart": lambda: svc.restart(state, runner),\n'
    '            "status": lambda: svc.status(state, runner),\n'
    "        }\n"
    "        before = dict(state)\n"
    "        result = actions[args.action]()\n"
    "        application.persist_state_changes(before, state)\n",
)

text = text.replace(
    '        actions = {\n'
    '            "start": lambda: start_https_sharing(state, runner),\n'
    '            "stop": lambda: stop_https_sharing(state, runner),\n'
    '            "restart": lambda: start_https_sharing(state, runner),\n'
    '            "status": lambda: https_sharing_status(state, runner),\n'
    "        }\n"
    "        result = actions[args.action]()\n"
    "        store.save(state)\n",
    '        svc = application.sharing\n'
    "        actions = {\n"
    '            "start": lambda: svc.start(state, runner),\n'
    '            "stop": lambda: svc.stop(state, runner),\n'
    '            "restart": lambda: svc.start(state, runner),\n'
    '            "status": lambda: https_sharing_status(state, runner),\n'
    "        }\n"
    "        before = dict(state)\n"
    "        result = actions[args.action]()\n"
    "        application.persist_state_changes(before, state)\n",
)

text = text.replace(
    '        state["current_ctx"] = args.ctx\n'
    "        store.save(state)\n",
    "        application.model_install.register_context_change(state, args.ctx)\n",
)

text = text.replace(
    "            stage_desktop_boot(state, runner)\n"
    "            store.save(state)\n",
    "            application.host_mode.enforce_desktop_next_boot(state, runner)\n",
)

text = text.replace(
    "        apply_llm_mode(state, runner)\n"
    '        if state.get("setup_complete") and state.get("current_model"):\n'
    "            install_service(state, runner)\n"
    "        store.save(state)\n",
    "        application.host_mode.enter_llm_mode(\n"
    "            state, runner,\n"
    "            install_service_fn=install_service,\n"
    '            install=bool(state.get("setup_complete") and state.get("current_model")),\n'
    "        )\n",
)

text = text.replace(
    "        state = switch_to_desktop_mode(state, runner, activate_now=args.now)\n"
    "        store.save(state)\n",
    "        application.host_mode.return_to_desktop(\n"
    "            state, runner, activate_now=args.now\n"
    "        )\n",
)

text = text.replace(
    "        state = uninstall(\n"
    "            state, runner, remove_container=args.remove_container,\n"
    "            remove_models=args.remove_models\n"
    "        )\n"
    "        store.save(state)\n",
    "        application.maintenance.uninstall(\n"
    "            state, runner,\n"
    "            remove_container=args.remove_container,\n"
    "            remove_models=args.remove_models,\n"
    "        )\n",
)

p.write_text(text, encoding="utf-8")
print("B done; remaining saves:", text.count("store.save("))