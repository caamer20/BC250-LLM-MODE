| Site | Classes | Code |
| --- | --- | --- |
| bc250_llm_mode/__main__.py:277 | check=False | `unit_text = quiet_runner.run(["cat", unit_path], check=False).stdout` |
| bc250_llm_mode/__main__.py:482 | check=False | `default_target = runner.run(["systemctl", "get-default"], check=False).stdout.strip()` |
| bc250_llm_mode/__main__.py:498 | check=False | `result = runner.run(["tail", "-n", str(args.lines), path], check=False)` |
| bc250_llm_mode/bootstrap.py:35 | check=False | `return subprocess.run(["zenity", *args], capture_output=True, text=True, check=False)` |
| bc250_llm_mode/bootstrap.py:61 | elevated(),rm/mv/cp/install | `runner.run(elevated(["rpm-ostree", "install", "python3-tkinter"]))` |
| bc250_llm_mode/chat.py:554 | check=False | `result = runner.run(["tail", "-n", str(lines), path], check=False)` |
| bc250_llm_mode/chat.py:569 | check=False | `target = runner.run(["systemctl", "get-default"], check=False).stdout.strip()` |
| bc250_llm_mode/desktop.py:22 | elevated(),check=False | `runner.run(elevated(["systemctl", "disable", "--now", service]), check=False)` |
| bc250_llm_mode/desktop.py:23 | elevated(),check=False | `runner.run(elevated(["systemctl", "reset-failed", service]), check=False)` |
| bc250_llm_mode/desktop.py:34 | check=False | `runner.run(["podman", "stop", "--ignore", "--time", "10", str(webui)], check=False)` |
| bc250_llm_mode/desktop.py:37 | check=False | `runner.run(["podman", "stop", "--ignore", "--time", "10", str(container)], check=False)` |
| bc250_llm_mode/desktop.py:41 | elevated() | `runner.run(elevated(["systemctl", "isolate", "graphical.target"]))` |
| bc250_llm_mode/env.py:21 | check=False | `result = runner.run(["podman", "container", "exists", name], check=False)` |
| bc250_llm_mode/env.py:40 | check=False | `runner.run(["podman", "start", name], check=False)` |
| bc250_llm_mode/env.py:46 | check=False,shell -lc | `runner, name, "bash", "-lc", f"test -x {server_binary} && test -x {quantize_binary}", check=False` |
| bc250_llm_mode/env.py:51 | shell -lc,interpolated cmd | `_exec(runner, name, "bash", "-lc", f"dnf install -y {BUILD_PACKAGES}")` |
| bc250_llm_mode/env.py:53 | interpolated cmd | `f"test -d {llama_root}/.git || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git {llama_` |
| bc250_llm_mode/env.py:54 | interpolated cmd | `f"cmake -S {llama_root} -B {llama_root}/build -G Ninja -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release; ` |
| bc250_llm_mode/env.py:55 | interpolated cmd | `f"cmake --build {llama_root}/build --target llama-server llama-cli llama-quantize -j2"` |
| bc250_llm_mode/env.py:57 | shell -lc | `_exec(runner, name, "bash", "-lc", clone_script)` |
| bc250_llm_mode/env.py:61 | shell -lc | `runner, name, "bash", "-lc",` |
| bc250_llm_mode/env.py:63 | check=False | `check=False,` |
| bc250_llm_mode/env.py:68 | shell -lc | `_exec(runner, name, "bash", "-lc", "dnf install -y python3 python3-pip python3-devel")` |
| bc250_llm_mode/env.py:73 | shell -lc | `_exec(runner, name, "bash", "-lc", venv_script)` |
| bc250_llm_mode/env.py:75 | check=False | `vulkan = _exec(runner, name, "vulkaninfo", "--summary", check=False)` |
| bc250_llm_mode/env.py:77 | shell -lc | `_exec(runner, name, "bash", "-lc", "dnf install -y vulkan-tools")` |
| bc250_llm_mode/env.py:78 | check=False | `vulkan = _exec(runner, name, "vulkaninfo", "--summary", check=False)` |
| bc250_llm_mode/env.py:93 | check=False,shell -lc,interpolated cmd | `runner, name, "bash", "-lc", f"git -C {root} {args}", check=False` |
| bc250_llm_mode/env.py:162 | shell -lc | `_exec(runner, name, "bash", "-lc", (` |
| bc250_llm_mode/env.py:163 | interpolated cmd | `f"cd {root} && git fetch origin tag {target} --depth 1 --no-tags --force"` |
| bc250_llm_mode/env.py:167 | shell -lc | `_exec(runner, name, "bash", "-lc", (` |
| bc250_llm_mode/env.py:168 | interpolated cmd | `f"rm -rf {stage} && git clone --depth 1 --branch {target} {root} {stage} && "` |
| bc250_llm_mode/env.py:169 | interpolated cmd | `f"cmake -S {stage} -B {stage}/build -G Ninja -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release && "` |
| bc250_llm_mode/env.py:170 | interpolated cmd | `f"cmake --build {stage}/build --target llama-server llama-cli llama-quantize -j2 && "` |
| bc250_llm_mode/env.py:176 | shell -lc | `_exec(runner, name, "bash", "-lc", (` |
| bc250_llm_mode/env.py:188 | shell -lc | `_exec(runner, name, "bash", "-lc", (` |
| bc250_llm_mode/env.py:215 | shell -lc | `_exec(runner, name, "bash", "-lc", (` |
| bc250_llm_mode/env.py:221 | shell -lc | `_exec(runner, name, "bash", "-lc", (` |
| bc250_llm_mode/gui/dashboard.py:590 | check=False | `self._dashboard_action(lambda r: r.run(["tail", "-n", "120", path], check=False))` |
| bc250_llm_mode/hardware.py:77 | check=False | `["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=15, check=False` |
| bc250_llm_mode/llmmode.py:28 | elevated() | `return runner.run(elevated(args), check=check)` |
| bc250_llm_mode/llmmode.py:49 | elevated() | `elevated(["tee", str(vendor_file.parent / "power/control")]),` |
| bc250_llm_mode/llmmode.py:51 | check=False | `check=False,` |
| bc250_llm_mode/llmmode.py:63 | check=False | `_run_root(runner, ["systemctl", "unmask", *SLEEP_TARGETS], check=False)` |
| bc250_llm_mode/llmmode.py:67 | check=False | `check=False,` |
| bc250_llm_mode/llmmode.py:70 | check=False | `_run_root(runner, ["systemctl", "disable", service], check=False)` |
| bc250_llm_mode/llmmode.py:72 | check=False | `staged = runner.run(["rpm-ostree", "kargs"], check=False).stdout` |
| bc250_llm_mode/llmmode.py:76 | rm/mv/cp/install | `_run_root(runner, ["rm", "-f", str(UDEV_RULE_PATH)])` |
| bc250_llm_mode/llmmode.py:77 | check=False | `_run_root(runner, ["udevadm", "control", "--reload"], check=False)` |
| bc250_llm_mode/llmmode.py:106 | check=False | `_run_root(runner, ["systemctl", "mask", "--runtime", unit], check=False)` |
| bc250_llm_mode/llmmode.py:120 | rm/mv/cp/install | `["install", "-D", "-m", "0644", temporary, str(RUNTIME_UDEV_RULE_PATH)],` |
| bc250_llm_mode/llmmode.py:147 | check=False | `_run_root(runner, ["systemctl", "unmask", *SLEEP_TARGETS], check=False)` |
| bc250_llm_mode/llmmode.py:148 | check=False | `_run_root(runner, ["systemctl", "unmask", "--runtime", *SLEEP_TARGETS], check=False)` |
| bc250_llm_mode/llmmode.py:149 | check=False | `_run_root(runner, ["systemctl", "unmask", "display-manager.service", "sddm.service"], check=False)` |
| bc250_llm_mode/llmmode.py:153 | check=False | `check=False,` |
| bc250_llm_mode/llmmode.py:156 | rm/mv/cp/install | `_run_root(runner, ["rm", "-f", str(RUNTIME_UDEV_RULE_PATH)])` |
| bc250_llm_mode/llmmode.py:157 | check=False | `_run_root(runner, ["udevadm", "control", "--reload"], check=False)` |
| bc250_llm_mode/openwebui.py:20 | check=False | `exists = runner.run(["podman", "container", "exists", name], check=False).returncode == 0` |
| bc250_llm_mode/openwebui.py:33 | check=False | `["podman", "inspect", "--format", "{{.State.Status}}", container], check=False` |
| bc250_llm_mode/openwebui.py:71 | check=False | `runner.run(["podman", "start", container], check=False)` |
| bc250_llm_mode/openwebui.py:84 | check=False | `runner.run(["podman", "start", str(status["container"])], check=False)` |
| bc250_llm_mode/openwebui.py:91 | check=False | `runner.run(["podman", "stop", "--time", "10", str(status["container"])], check=False)` |
| bc250_llm_mode/optimize.py:149 | elevated(),rm/mv/cp/install | `runner.run(elevated(["install", "-D", "-m", "0644", temporary, str(path)]))` |
| bc250_llm_mode/optimize.py:184 | elevated(),rm/mv/cp/install | `runner.run(elevated(["cp", "--preserve=mode,ownership,timestamps", str(CYAN_CONFIG), str(CYAN_BACKUP` |
| bc250_llm_mode/optimize.py:196 | elevated() | `runner.run(elevated(["systemctl", "restart", CYAN_SERVICE]))` |
| bc250_llm_mode/optimize.py:203 | elevated(),rm/mv/cp/install | `runner.run(elevated(["cp", "--preserve=mode,ownership,timestamps", str(CYAN_BACKUP), str(CYAN_CONFIG` |
| bc250_llm_mode/optimize.py:204 | elevated(),rm/mv/cp/install | `runner.run(elevated(["rm", "-f", str(CYAN_BACKUP)]))` |
| bc250_llm_mode/optimize.py:205 | elevated(),check=False | `runner.run(elevated(["systemctl", "restart", CYAN_SERVICE]), check=False)` |
| bc250_llm_mode/optimize.py:210 | check=False | `enabled = runner.run(["systemctl", "is-enabled", unit], check=False).stdout.strip() == "enabled"` |
| bc250_llm_mode/optimize.py:211 | check=False | `active = runner.run(["systemctl", "is-active", unit], check=False).stdout.strip() == "active"` |
| bc250_llm_mode/optimize.py:223 | elevated(),check=False | `runner.run(elevated(["systemctl", "stop", unit]), check=False)` |
| bc250_llm_mode/optimize.py:224 | elevated(),check=False | `runner.run(elevated(["systemctl", "disable", unit]), check=False)` |
| bc250_llm_mode/optimize.py:228 | elevated(),check=False | `runner.run(elevated(["systemctl", "enable", unit]), check=False)` |
| bc250_llm_mode/optimize.py:230 | elevated(),check=False | `runner.run(elevated(["systemctl", "start", unit]), check=False)` |
| bc250_llm_mode/optimize.py:245 | elevated() | `runner.run(elevated(["sysctl", "-w", f"vm.swappiness={value}"]))` |
| bc250_llm_mode/optimize.py:248 | elevated(),check=False | `runner.run(elevated(["sysctl", "-w", f"vm.swappiness={original}"]), check=False)` |
| bc250_llm_mode/optimize.py:250 | elevated(),check=False,rm/mv/cp/install | `runner.run(elevated(["rm", "-f", str(SYSCTL_CONFIG)]), check=False)` |
| bc250_llm_mode/optimize.py:274 | elevated(),check=False,rm/mv/cp/install | `runner.run(elevated(["rm", "-f", str(LOGROTATE_CONFIG)]), check=False)` |
| bc250_llm_mode/privilege.py:7 | elevated() | `def elevated(command: list[str]) -> list[str]:` |
| bc250_llm_mode/server.py:179 | elevated(),rm/mv/cp/install | `runner.run(elevated(["install", "-m", "0644", temporary, str(destination)]))` |
| bc250_llm_mode/server.py:185 | elevated() | `runner.run(elevated(["systemctl", "daemon-reload"]))` |
| bc250_llm_mode/server.py:187 | elevated(),check=False | `runner.run(elevated(["loginctl", "enable-linger", pwd.getpwuid(os.getuid()).pw_name]), check=False)` |
| bc250_llm_mode/server.py:191 | elevated(),check=False | `runner.run(elevated(["systemctl", "disable", service_name]), check=False)` |
| bc250_llm_mode/server.py:192 | elevated() | `runner.run(elevated(["systemctl", "start", service_name]))` |
| bc250_llm_mode/server.py:204 | elevated() | `runner.run(elevated(["systemctl", "restart", str(state["service_name"])]))` |
| bc250_llm_mode/server.py:209 | check=False | `["systemctl", "show", service, f"--property={prop}", "--value"], check=False` |
| bc250_llm_mode/server.py:238 | elevated() | `runner.run(elevated(["systemctl", "start", str(state["service_name"])]))` |
| bc250_llm_mode/server.py:246 | elevated(),check=False | `runner.run(elevated(["systemctl", "stop", service]), check=False)` |
| bc250_llm_mode/server.py:250 | elevated(),check=False | `runner.run(elevated(["systemctl", "reset-failed", service]), check=False)` |
| bc250_llm_mode/server.py:333 | elevated(),check=False | `result = runner.run(elevated(command) if server_log.startswith("/root/") else command, check=False)` |
| bc250_llm_mode/sharing.py:31 | check=False | `[cli, "serve", "status", "--json"], check=False, emit_output=False` |
| bc250_llm_mode/sharing.py:119 | elevated(),check=False | `runner.run(elevated([cli, "funnel", f"--https={port}", "off"]), check=False)` |
| bc250_llm_mode/sharing.py:120 | elevated() | `runner.run(elevated([cli, "serve", "--bg", f"--https={port}", target]))` |
| bc250_llm_mode/sharing.py:144 | elevated(),check=False | `runner.run(elevated([cli, "serve", f"--https={port}", "off"]), check=False)` |
| bc250_llm_mode/sharing.py:145 | elevated(),check=False | `runner.run(elevated([cli, "funnel", f"--https={port}", "off"]), check=False)` |
| bc250_llm_mode/tailscale.py:17 | check=False | `["systemctl", "show", SERVICE, f"--property={prop}", "--value"], check=False` |
| bc250_llm_mode/tailscale.py:45 | check=False | `status = runner.run([cli, "status", "--json"], check=False, emit_output=False)` |
| bc250_llm_mode/tailscale.py:72 | elevated() | `runner.run(elevated(["systemctl", "start", SERVICE]))` |
| bc250_llm_mode/tailscale.py:78 | elevated(),check=False | `runner.run(elevated(["systemctl", "stop", SERVICE]), check=False)` |
| bc250_llm_mode/tailscale.py:84 | elevated() | `runner.run(elevated(["systemctl", "restart", SERVICE]))` |
| bc250_llm_mode/tailscale.py:90 | elevated() | `runner.run(elevated(["systemctl", "start", SERVICE]))` |
| bc250_llm_mode/tailscale.py:91 | elevated() | `runner.run(elevated(["tailscale", "up"]))` |
| bc250_llm_mode/tailscale.py:98 | elevated(),check=False | `runner.run(elevated(["tailscale", "down"]), check=False)` |
| bc250_llm_mode/uninstall.py:22 | elevated(),check=False | `runner.run(elevated(["systemctl", "disable", "--now", service]), check=False)` |
| bc250_llm_mode/uninstall.py:23 | elevated(),check=False | `runner.run(elevated(["systemctl", "reset-failed", service]), check=False)` |
| bc250_llm_mode/uninstall.py:31 | elevated(),rm/mv/cp/install | `runner.run(elevated(["rm", "-f", str(service_path)]))` |
| bc250_llm_mode/uninstall.py:32 | elevated(),check=False | `runner.run(elevated(["systemctl", "daemon-reload"]), check=False)` |
| bc250_llm_mode/uninstall.py:46 | check=False,rm/mv/cp/install | `runner.run(["podman", "rm", "--force", str(state.get("container_name", "llm"))], check=False)` |
| bc250_llm_mode/uninstall.py:49 | check=False,rm/mv/cp/install | `runner.run(["podman", "rm", "--force", str(webui)], check=False)` |
TOTAL_SITES=109
