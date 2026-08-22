from __future__ import annotations

import json
import os
import pwd
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .logging_utils import CommandRunner
from .optimize import normalized_settings
from .privilege import elevated


def current_model_record(state: dict[str, Any]) -> dict[str, Any]:
    model_id = state.get("current_model")
    for record in state.get("installed_models", []):
        if record.get("id") == model_id:
            return record
    raise RuntimeError("No current installed model is selected")


LAUNCHER_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
HANDOFF="${BC250_HANDOFF_PATH:-@HANDOFF@}"
LEGACY_STATE="${BC250_STATE_PATH:-@LEGACY_STATE@}"
ARGV=()
FAST_SYNC=0
append_argv() {
  while IFS= read -r argv_line; do
    ARGV+=("$argv_line")
  done
}
if [ -f "$HANDOFF" ]; then
  FAST_SYNC=$(python3 -c 'import json, sys; h=json.load(open(sys.argv[1])); print(0 if h.get("fast_sync") else 1)' "$HANDOFF")
  append_argv < <(python3 - "$HANDOFF" <<'PYH'
import json
import os
import sys

h = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [
    os.path.join(h["llama_cpp_path"], "build", "bin", "llama-server"),
    "-m", h["model_path"],
    "--host", "127.0.0.1",
    "--port", str(h["port"]),
    "--n-gpu-layers", "99",
    "--ctx-size", str(h["ctx_total"]),
    "--flash-attn", h["flash_attention"],
    "--batch-size", str(h["batch_size"]),
    "--ubatch-size", str(h["ubatch_size"]),
    "--cache-type-k", h["kv_cache_type"],
    "--cache-type-v", h["kv_cache_type"],
    "--parallel", str(h["parallel_slots"]),
    "--alias", h["alias"],
    "--temp", str(h.get("temperature", 0.3)),
    "--top-p", str(h.get("top_p", 0.9)),
    "--top-k", str(h.get("top_k", 40)),
    "--min-p", str(h.get("min_p", 0.05)),
    "--repeat-penalty", str(h.get("repeat_penalty", 1.05)),
]
threads = h.get("threads")
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
if not (isinstance(threads, int) and 1 <= threads <= 64):
    threads = detected
if isinstance(threads, int) and threads >= 1:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYH
)"
else
  FAST_SYNC=$(python3 -c 'import json, sys; s=json.load(open(sys.argv[1])); o=s.get("optimizations") or {}; enabled=o.get("runtime_enabled", True); print(1 if (enabled and o.get("fast_sync")) else 0)' "$LEGACY_STATE")
  append_argv < <(python3 - "$LEGACY_STATE" "@SERVER@" <<'PYS'
import json
import sys

state_path = sys.argv[1]
server_binary = sys.argv[2]
s = json.load(open(state_path, encoding="utf-8"))
r = next(x for x in s["installed_models"] if x["id"] == s["current_model"])
o = s.get("optimizations") or {}
if not o.get("runtime_enabled", True):
    o = {}
slots = int(o.get("parallel_slots", 4))
argv = [
    r["path"],
    "--host", "127.0.0.1",
    "--port", str(s.get("server_port", 8080)),
    "--n-gpu-layers", "99",
    "--ctx-size", str(int(s.get("current_ctx", 8192)) * slots),
    "--flash-attn", str(o.get("flash_attention", "auto")),
    "--batch-size", str(int(o.get("batch_size", 2048))),
    "--ubatch-size", str(int(o.get("ubatch_size", 512))),
    "--cache-type-k", str(o.get("kv_cache_type", "q8_0")),
    "--cache-type-v", str(o.get("kv_cache_type", "q8_0")),
    "--parallel", str(slots),
    "--alias", str(r.get("display_name") or r.get("id") or "local").replace(chr(10), " ").strip(),
    "--temp", str(r.get("temperature", 0.3)),
    "--top-p", str(r.get("top_p", 0.9)),
    "--top-k", str(r.get("top_k", 40)),
    "--min-p", str(r.get("min_p", 0.05)),
    "--repeat-penalty", str(r.get("repeat_penalty", 1.05)),
]
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
threads = int(o.get("threads", 0) or 0)
if not 1 <= threads <= 64:
    threads = detected
if 1 <= threads <= 64:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYS
)"
fi
export GGML_VK_DISABLE_F16=1
if [ "$FAST_SYNC" != "1" ]; then
  export GGML_VK_FORCE_SYNC=1
fi
exec "${ARGV[@]}"
"""


LAUNCHER_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
HANDOFF="${BC250_HANDOFF_PATH:-@HANDOFF@}"
LEGACY_STATE="${BC250_STATE_PATH:-@LEGACY_STATE@}"
ARGV=()
FAST_SYNC=0
append_argv() {
  while IFS= read -r argv_line; do
    ARGV+=("$argv_line")
  done
}
if [ -f "$HANDOFF" ]; then
  FAST_SYNC=$(python3 -c 'import json, sys; h=json.load(open(sys.argv[1])); print(0 if h.get("fast_sync") else 1)' "$HANDOFF")
  append_argv < <(python3 - "$HANDOFF" <<'PYH'
import json
import os
import sys

h = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [
    os.path.join(h["llama_cpp_path"], "build", "bin", "llama-server"),
    "-m", h["model_path"],
    "--host", "127.0.0.1",
    "--port", str(h["port"]),
    "--n-gpu-layers", "99",
    "--ctx-size", str(h["ctx_total"]),
    "--flash-attn", h["flash_attention"],
    "--batch-size", str(h["batch_size"]),
    "--ubatch-size", str(h["ubatch_size"]),
    "--cache-type-k", h["kv_cache_type"],
    "--cache-type-v", h["kv_cache_type"],
    "--parallel", str(h["parallel_slots"]),
    "--alias", h["alias"],
    "--temp", str(h.get("temperature", 0.3)),
    "--top-p", str(h.get("top_p", 0.9)),
    "--top-k", str(h.get("top_k", 40)),
    "--min-p", str(h.get("min_p", 0.05)),
    "--repeat-penalty", str(h.get("repeat_penalty", 1.05)),
]
threads = h.get("threads")
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
if not (isinstance(threads, int) and 1 <= threads <= 64):
    threads = detected
if isinstance(threads, int) and threads >= 1:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYH
)
else
  FAST_SYNC=$(python3 -c 'import json, sys; s=json.load(open(sys.argv[1])); o=s.get("optimizations") or {}; enabled=o.get("runtime_enabled", True); print(1 if (enabled and o.get("fast_sync")) else 0)' "$LEGACY_STATE")
  append_argv < <(python3 - "$LEGACY_STATE" "@SERVER@" <<'PYS'
import json
import sys

state_path = sys.argv[1]
server_binary = sys.argv[2]
s = json.load(open(state_path, encoding="utf-8"))
r = next(x for x in s["installed_models"] if x["id"] == s["current_model"])
o = s.get("optimizations") or {}
if not o.get("runtime_enabled", True):
    o = {}
slots = int(o.get("parallel_slots", 4))
argv = [
    r["path"],
    "--host", "127.0.0.1",
    "--port", str(s.get("server_port", 8080)),
    "--n-gpu-layers", "99",
    "--ctx-size", str(int(s.get("current_ctx", 8192)) * slots),
    "--flash-attn", str(o.get("flash_attention", "auto")),
    "--batch-size", str(int(o.get("batch_size", 2048))),
    "--ubatch-size", str(int(o.get("ubatch_size", 512))),
    "--cache-type-k", str(o.get("kv_cache_type", "q8_0")),
    "--cache-type-v", str(o.get("kv_cache_type", "q8_0")),
    "--parallel", str(slots),
    "--alias", str(r.get("display_name") or r.get("id") or "local").replace(chr(10), " ").strip(),
    "--temp", str(r.get("temperature", 0.3)),
    "--top-p", str(r.get("top_p", 0.9)),
    "--top-k", str(r.get("top_k", 40)),
    "--min-p", str(r.get("min_p", 0.05)),
    "--repeat-penalty", str(r.get("repeat_penalty", 1.05)),
]
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
threads = int(o.get("threads", 0) or 0)
if not 1 <= threads <= 64:
    threads = detected
if 1 <= threads <= 64:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYS
)
fi
export GGML_VK_DISABLE_F16=1
if [ "$FAST_SYNC" != "1" ]; then
  export GGML_VK_FORCE_SYNC=1
fi
exec "${ARGV[@]}"
"""


LAUNCHER_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
HANDOFF="${BC250_HANDOFF_PATH:-@HANDOFF@}"
LEGACY_STATE="${BC250_STATE_PATH:-@LEGACY_STATE@}"
ARGV=()
FAST_SYNC=0
append_argv() {
  while IFS= read -r argv_line; do
    ARGV+=("$argv_line")
  done
}
if [ -f "$HANDOFF" ]; then
  FAST_SYNC=$(python3 -c 'import json, sys; h=json.load(open(sys.argv[1])); print(0 if h.get("fast_sync") else 1)' "$HANDOFF")
  append_argv < <(python3 - "$HANDOFF" <<'PYH'
import json
import os
import sys

h = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [
    os.path.join(h["llama_cpp_path"], "build", "bin", "llama-server"),
    "-m", h["model_path"],
    "--host", "127.0.0.1",
    "--port", str(h["port"]),
    "--n-gpu-layers", "99",
    "--ctx-size", str(h["ctx_total"]),
    "--flash-attn", h["flash_attention"],
    "--batch-size", str(h["batch_size"]),
    "--ubatch-size", str(h["ubatch_size"]),
    "--cache-type-k", h["kv_cache_type"],
    "--cache-type-v", h["kv_cache_type"],
    "--parallel", str(h["parallel_slots"]),
    "--alias", h["alias"],
    "--temp", str(h.get("temperature", 0.3)),
    "--top-p", str(h.get("top_p", 0.9)),
    "--top-k", str(h.get("top_k", 40)),
    "--min-p", str(h.get("min_p", 0.05)),
    "--repeat-penalty", str(h.get("repeat_penalty", 1.05)),
]
threads = h.get("threads")
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
if not (isinstance(threads, int) and 1 <= threads <= 64):
    threads = detected
if isinstance(threads, int) and threads >= 1:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYH
)
else
  FAST_SYNC=$(python3 -c 'import json, sys; s=json.load(open(sys.argv[1])); o=s.get("optimizations") or {}; enabled=o.get("runtime_enabled", True); print(1 if (enabled and o.get("fast_sync")) else 0)' "$LEGACY_STATE")
  append_argv < <(python3 - "$LEGACY_STATE" "@SERVER@" <<'PYS'
import json
import sys

state_path = sys.argv[1]
server_binary = sys.argv[2]
s = json.load(open(state_path, encoding="utf-8"))
r = next(x for x in s["installed_models"] if x["id"] == s["current_model"])
o = s.get("optimizations") or {}
if not o.get("runtime_enabled", True):
    o = {}
slots = int(o.get("parallel_slots", 4))
argv = [
    server_binary,
    r["path"],
    "--host", "127.0.0.1",
    "--port", str(s.get("server_port", 8080)),
    "--n-gpu-layers", "99",
    "--ctx-size", str(int(s.get("current_ctx", 8192)) * slots),
    "--flash-attn", str(o.get("flash_attention", "auto")),
    "--batch-size", str(int(o.get("batch_size", 2048))),
    "--ubatch-size", str(int(o.get("ubatch_size", 512))),
    "--cache-type-k", str(o.get("kv_cache_type", "q8_0")),
    "--cache-type-v", str(o.get("kv_cache_type", "q8_0")),
    "--parallel", str(slots),
    "--alias", str(r.get("display_name") or r.get("id") or "local").replace(chr(10), " ").strip(),
    "--temp", str(r.get("temperature", 0.3)),
    "--top-p", str(r.get("top_p", 0.9)),
    "--top-k", str(r.get("top_k", 40)),
    "--min-p", str(r.get("min_p", 0.05)),
    "--repeat-penalty", str(r.get("repeat_penalty", 1.05)),
]
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
threads = int(o.get("threads", 0) or 0)
if not 1 <= threads <= 64:
    threads = detected
if 1 <= threads <= 64:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYS
)
fi
export GGML_VK_DISABLE_F16=1
if [ "$FAST_SYNC" != "1" ]; then
  export GGML_VK_FORCE_SYNC=1
fi
exec "${ARGV[@]}"
"""


LAUNCHER_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
HANDOFF="${BC250_HANDOFF_PATH:-@HANDOFF@}"
LEGACY_STATE="${BC250_STATE_PATH:-@LEGACY_STATE@}"
ARGV=()
FAST_SYNC=0
append_argv() {
  while IFS= read -r argv_line; do
    ARGV+=("$argv_line")
  done
}
if [ -f "$HANDOFF" ]; then
  FAST_SYNC=$(python3 -c 'import json, sys; h=json.load(open(sys.argv[1])); print(0 if h.get("fast_sync") else 1)' "$HANDOFF")
  append_argv < <(python3 - "$HANDOFF" <<'PYH'
import json
import os
import sys

h = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [
    os.path.join(h["llama_cpp_path"], "build", "bin", "llama-server"),
    "-m", h["model_path"],
    "--host", "127.0.0.1",
    "--port", str(h["port"]),
    "--n-gpu-layers", "99",
    "--ctx-size", str(h["ctx_total"]),
    "--flash-attn", h["flash_attention"],
    "--batch-size", str(h["batch_size"]),
    "--ubatch-size", str(h["ubatch_size"]),
    "--cache-type-k", h["kv_cache_type"],
    "--cache-type-v", h["kv_cache_type"],
    "--parallel", str(h["parallel_slots"]),
    "--alias", h["alias"],
    "--temp", str(h.get("temperature", 0.3)),
    "--top-p", str(h.get("top_p", 0.9)),
    "--top-k", str(h.get("top_k", 40)),
    "--min-p", str(h.get("min_p", 0.05)),
    "--repeat-penalty", str(h.get("repeat_penalty", 1.05)),
]
threads = h.get("threads")
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
if not (isinstance(threads, int) and 1 <= threads <= 64):
    threads = detected
if isinstance(threads, int) and threads >= 1:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYH
)
else
  FAST_SYNC=$(python3 -c 'import json, sys; s=json.load(open(sys.argv[1])); o=s.get("optimizations") or {}; enabled=o.get("runtime_enabled", True); print(1 if (enabled and o.get("fast_sync")) else 0)' "$LEGACY_STATE")
  append_argv < <(python3 - "$LEGACY_STATE" "@SERVER@" <<'PYS'
import json
import sys

state_path = sys.argv[1]
server_binary = sys.argv[2]
s = json.load(open(state_path, encoding="utf-8"))
r = next(x for x in s["installed_models"] if x["id"] == s["current_model"])
o = s.get("optimizations") or {}
if not o.get("runtime_enabled", True):
    o = {}
slots = int(o.get("parallel_slots", 4))
argv = [
    server_binary,
    "-m", r["path"],
    "--host", "127.0.0.1",
    "--port", str(s.get("server_port", 8080)),
    "--n-gpu-layers", "99",
    "--ctx-size", str(int(s.get("current_ctx", 8192)) * slots),
    "--flash-attn", str(o.get("flash_attention", "auto")),
    "--batch-size", str(int(o.get("batch_size", 2048))),
    "--ubatch-size", str(int(o.get("ubatch_size", 512))),
    "--cache-type-k", str(o.get("kv_cache_type", "q8_0")),
    "--cache-type-v", str(o.get("kv_cache_type", "q8_0")),
    "--parallel", str(slots),
    "--alias", str(r.get("display_name") or r.get("id") or "local").replace(chr(10), " ").strip(),
    "--temp", str(r.get("temperature", 0.3)),
    "--top-p", str(r.get("top_p", 0.9)),
    "--top-k", str(r.get("top_k", 40)),
    "--min-p", str(r.get("min_p", 0.05)),
    "--repeat-penalty", str(r.get("repeat_penalty", 1.05)),
]
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
threads = int(o.get("threads", 0) or 0)
if not 1 <= threads <= 64:
    threads = detected
if 1 <= threads <= 64:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYS
)
fi
export GGML_VK_DISABLE_F16=1
if [ "$FAST_SYNC" != "1" ]; then
  export GGML_VK_FORCE_SYNC=1
fi
exec "${ARGV[@]}"
"""


LAUNCHER_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
HANDOFF="${BC250_HANDOFF_PATH:-@HANDOFF@}"
LEGACY_STATE="${BC250_STATE_PATH:-@LEGACY_STATE@}"
ARGV=()
FAST_SYNC=0
append_argv() {
  while IFS= read -r argv_line; do
    ARGV+=("$argv_line")
  done
}
if [ -f "$HANDOFF" ]; then
  FAST_SYNC=$(python3 -c 'import json, sys; h=json.load(open(sys.argv[1])); print(1 if h.get("fast_sync") else 0)' "$HANDOFF")
  append_argv < <(python3 - "$HANDOFF" <<'PYH'
import json
import os
import sys

h = json.load(open(sys.argv[1], encoding="utf-8"))
argv = [
    os.path.join(h["llama_cpp_path"], "build", "bin", "llama-server"),
    "-m", h["model_path"],
    "--host", "127.0.0.1",
    "--port", str(h["port"]),
    "--n-gpu-layers", "99",
    "--ctx-size", str(h["ctx_total"]),
    "--flash-attn", h["flash_attention"],
    "--batch-size", str(h["batch_size"]),
    "--ubatch-size", str(h["ubatch_size"]),
    "--cache-type-k", h["kv_cache_type"],
    "--cache-type-v", h["kv_cache_type"],
    "--parallel", str(h["parallel_slots"]),
    "--alias", h["alias"],
    "--temp", str(h.get("temperature", 0.3)),
    "--top-p", str(h.get("top_p", 0.9)),
    "--top-k", str(h.get("top_k", 40)),
    "--min-p", str(h.get("min_p", 0.05)),
    "--repeat-penalty", str(h.get("repeat_penalty", 1.05)),
]
threads = h.get("threads")
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
if not (isinstance(threads, int) and 1 <= threads <= 64):
    threads = detected
if isinstance(threads, int) and threads >= 1:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYH
)
else
  FAST_SYNC=$(python3 -c 'import json, sys; s=json.load(open(sys.argv[1])); o=s.get("optimizations") or {}; enabled=o.get("runtime_enabled", True); print(1 if (enabled and o.get("fast_sync")) else 0)' "$LEGACY_STATE")
  append_argv < <(python3 - "$LEGACY_STATE" "@SERVER@" <<'PYS'
import json
import sys

state_path = sys.argv[1]
server_binary = sys.argv[2]
s = json.load(open(state_path, encoding="utf-8"))
r = next(x for x in s["installed_models"] if x["id"] == s["current_model"])
o = s.get("optimizations") or {}
if not o.get("runtime_enabled", True):
    o = {}
slots = int(o.get("parallel_slots", 4))
argv = [
    server_binary,
    "-m", r["path"],
    "--host", "127.0.0.1",
    "--port", str(s.get("server_port", 8080)),
    "--n-gpu-layers", "99",
    "--ctx-size", str(int(s.get("current_ctx", 8192)) * slots),
    "--flash-attn", str(o.get("flash_attention", "auto")),
    "--batch-size", str(int(o.get("batch_size", 2048))),
    "--ubatch-size", str(int(o.get("ubatch_size", 512))),
    "--cache-type-k", str(o.get("kv_cache_type", "q8_0")),
    "--cache-type-v", str(o.get("kv_cache_type", "q8_0")),
    "--parallel", str(slots),
    "--alias", str(r.get("display_name") or r.get("id") or "local").replace(chr(10), " ").strip(),
    "--temp", str(r.get("temperature", 0.3)),
    "--top-p", str(r.get("top_p", 0.9)),
    "--top-k", str(r.get("top_k", 40)),
    "--min-p", str(r.get("min_p", 0.05)),
    "--repeat-penalty", str(r.get("repeat_penalty", 1.05)),
]
cores = set()
socket_id = None
try:
    info = open("/proc/cpuinfo", encoding="utf-8").read()
    for line in info.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key == "physical id":
            socket_id = value
        elif key == "core id" and socket_id is not None:
            cores.add((socket_id, value))
            socket_id = None
    detected = len(cores) or sum(
        1 for entry in info.splitlines() if entry.startswith("processor"))
except OSError:
    detected = 0
threads = int(o.get("threads", 0) or 0)
if not 1 <= threads <= 64:
    threads = detected
if 1 <= threads <= 64:
    argv += ["--threads", str(threads), "--threads-batch", str(threads)]
argv += ["--cache-reuse", "256", "--defrag-threshold", "0.1"]
for item in argv:
    print(item)
PYS
)
fi
export GGML_VK_DISABLE_F16=1
if [ "$FAST_SYNC" != "1" ]; then
  export GGML_VK_FORCE_SYNC=1
fi
exec "${ARGV[@]}"
"""


def generate_launcher(state: dict[str, Any]) -> Path:
    """Generate the runtime launcher (handoff-first, legacy fallback).

    Handoff mode (SQLite era): reads ``runtime-handoff.json`` — a rendered
    artifact regenerated on every committed state save — and execs the argv
    it describes. Legacy fallback (pre-cutover installs): reads
    ``state.json`` directly. Both paths deliver one argument per line into a
    single ``exec``, so no positional CFG array and no fragile
    continuations remain.
    """
    app_dir = Path(str(state["app_dir"])).expanduser()
    app_dir.mkdir(parents=True, exist_ok=True)
    launcher = app_dir / "run-model.sh"
    state_path = Path(str(state.get("state_path", app_dir / "state.json"))).expanduser()
    llama_server = Path(str(state["llama_cpp_path"])) / "build/bin/llama-server"
    handoff_path = app_dir / "runtime-handoff.json"
    content = (
        LAUNCHER_TEMPLATE
        .replace("@HANDOFF@", str(handoff_path))
        .replace("@LEGACY_STATE@", str(state_path))
        .replace("@SERVER@", str(llama_server))
    )
    launcher.write_text(content, encoding="utf-8")
    launcher.chmod(0o755)
    return launcher

def _service_text(state: dict[str, Any], launcher: Path) -> str:
    container = state.get("container_name", "llm")
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    identity = ""
    if uid:
        identity = (
            f"User={account.pw_name}\nGroup={account.pw_gid}\n"
            f"Environment=HOME={account.pw_dir}\nEnvironment=XDG_RUNTIME_DIR=/run/user/{uid}\n"
        )
    podman = shutil.which("podman") or "/usr/bin/podman"
    runtime_prep = ""
    if uid == 0:
        install = shutil.which("install") or "/usr/bin/install"
        # Root-created Distrobox containers bind-mount /run/user/0. That path
        # is volatile and may not exist on a headless boot until root logs in.
        runtime_prep = f"ExecStartPre={install} -d -m 0700 -o 0 -g 0 /run/user/0\n"
    # Bazzite maps /root to /var/roothome. systemd/SELinux can reject an
    # append: output target there with status 209/STDOUT before ExecStart runs.
    # /var/log is the appropriate system-service log location.
    server_log = (
        "/var/log/bc250-llm-server.log"
        if uid == 0
        else str(Path(str(state["logs_dir"])).expanduser() / "llama-server.log")
    )
    state["server_log"] = server_log
    tuning = normalized_settings(state.get("optimizations"))
    limits = ""
    resource_guards = ""
    restart_delay = 10
    if tuning["safeguards_enabled"]:
        limits = (
            f"StartLimitIntervalSec={int(tuning['restart_window_sec'])}\n"
            f"StartLimitBurst={int(tuning['restart_burst'])}\n"
        )
        restart_delay = int(tuning["restart_delay_sec"])
        # The host only owns ~4 GiB after the 12/4 UMA carve-out. Capping the
        # service means the OOM killer and systemd take out the *server*, never
        # the Bazzite desktop, and the server yields I/O to interactive use.
        resource_guards = (
            "MemoryHigh=3000M\nMemoryMax=3500M\nOOMScoreAdjust=500\n"
            "IOSchedulingClass=idle\n"
        )
    return f"""[Unit]
Description=BC250 llama.cpp model server
After=network-online.target
Wants=network-online.target
{limits}

[Service]
{identity}{runtime_prep}{resource_guards}ExecStartPre=-{podman} start {container}
ExecStart={podman} exec --user root {container} {launcher}
Restart=on-failure
RestartSec={restart_delay}
StandardOutput=append:{server_log}
StandardError=append:{server_log}

[Install]
WantedBy=multi-user.target
"""


def install_service(
    state: dict[str, Any], runner: CommandRunner, *, enable_and_start: bool = True
) -> Path:
    from .runtime_handoff import regenerate_for_app_state

    regenerate_for_app_state(state)
    launcher = generate_launcher(state)
    service_name = str(state.get("service_name", "bc250-llm.service"))
    destination = Path("/etc/systemd/system") / service_name
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(_service_text(state, launcher))
        temporary = handle.name
    try:
        runner.run(elevated(["install", "-m", "0644", temporary, str(destination)]))
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    runner.run(elevated(["systemctl", "daemon-reload"]))
    if os.getuid():
        runner.run(elevated(["loginctl", "enable-linger", pwd.getpwuid(os.getuid()).pw_name]), check=False)
    if enable_and_start:
        # LLM Mode is a current-boot session. Never leave inference enabled
        # across reboot; start it explicitly for this boot only.
        runner.run(elevated(["systemctl", "disable", service_name]), check=False)
        runner.run(elevated(["systemctl", "start", service_name]))
        state.update(boot_policy="desktop", desktop_on_reboot=True, llm_autostart=False)
    else:
        runner.emit(f"Refreshed {service_name} definition without changing its enabled/running state")
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 9)
    return destination


def restart_service(state: dict[str, Any], runner: CommandRunner) -> None:
    # Refresh the launcher and regenerate a missing/stale runtime handoff on
    # every controlled restart so newly added per-model profiles and any
    # committed-but-unrendered changes take effect without reinstalling.
    from .runtime_handoff import regenerate_for_app_state

    regenerate_for_app_state(state)
    generate_launcher(state)
    runner.run(elevated(["systemctl", "restart", str(state["service_name"])]))


def _unit_property(runner: CommandRunner, service: str, prop: str) -> str:
    result = runner.run(
        ["systemctl", "show", service, f"--property={prop}", "--value"], check=False
    )
    return result.stdout.strip()


def service_status(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Return live systemd state without assuming the service is installed."""
    service = str(state.get("service_name", "bc250-llm.service"))
    load = _unit_property(runner, service, "LoadState")
    active = _unit_property(runner, service, "ActiveState") if load != "not-found" else "inactive"
    sub = _unit_property(runner, service, "SubState") if load != "not-found" else "dead"
    unit_file = _unit_property(runner, service, "UnitFileState") if load != "not-found" else "disabled"
    return {
        "service": service,
        "installed": bool(load and load != "not-found"),
        "load_state": load or "unknown",
        "active": active == "active",
        "active_state": active or "unknown",
        "sub_state": sub or "unknown",
        "enabled": unit_file in {"enabled", "enabled-runtime", "static"},
        "unit_file_state": unit_file or "unknown",
    }


def start_service(
    state: dict[str, Any], runner: CommandRunner, *, wait_for_health: bool = True
) -> dict[str, Any]:
    """Start only the systemd-owned llama server, optionally waiting for its API."""
    current_model_record(state)
    runner.run(elevated(["systemctl", "start", str(state["service_name"])]))
    if wait_for_health:
        return health_check(state, runner)
    return service_status(state, runner)


def stop_service(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    service = str(state["service_name"])
    runner.run(elevated(["systemctl", "stop", service]), check=False)
    # Podman's attached exec can report a nonzero exit while llama-server is
    # gracefully terminating. An explicit user Stop should settle at inactive,
    # while genuine unexpected failures still retain Restart=on-failure.
    runner.run(elevated(["systemctl", "reset-failed", service]), check=False)
    return service_status(state, runner)


def restart_and_wait(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    current_model_record(state)
    restart_service(state, runner)
    return health_check(state, runner)


def _json_get(url: str, timeout: float = 5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def health_check(state: dict[str, Any], runner: CommandRunner | None = None, timeout: int = 120) -> dict[str, Any]:
    port = int(state.get("server_port", 8080))
    deadline = time.monotonic() + timeout
    last_error = "server did not respond"
    while time.monotonic() < deadline:
        try:
            health = _json_get(f"http://127.0.0.1:{port}/health")
            models = _json_get(f"http://127.0.0.1:{port}/v1/models")
            try:
                props = _json_get(f"http://127.0.0.1:{port}/props")
            except (OSError, urllib.error.URLError, ValueError):
                props = {}
            actual_ctx = (
                props.get("default_generation_settings", {}).get("n_ctx")
                if isinstance(props, dict)
                else None
            )
            metrics = system_metrics()
            result = {
                "healthy": True,
                "model_id": state.get("current_model"),
                "n_ctx": int(actual_ctx or state.get("current_ctx", 8192)),
                "requested_ctx": int(state.get("current_ctx", 8192)),
                "parallel_slots": int(
                    props.get("total_slots")
                    if isinstance(props, dict) and props.get("total_slots") is not None
                    else normalized_settings(state.get("optimizations"))["parallel_slots"]
                ),
                "vram_used_mib": metrics.get("vram_used_mib"),
                "vram_total_mib": metrics.get("vram_total_mib"),
                "health": health,
                "models": models,
                "model_alias": props.get("model_alias") if isinstance(props, dict) else None,
            }
            if runner:
                runner.emit(
                    f"Server healthy on 127.0.0.1:{port}; model={result['model_id']} "
                    f"n_ctx={result['n_ctx']} VRAM={result['vram_used_mib']}/{result['vram_total_mib']} MiB"
                )
            return result
        except (OSError, urllib.error.URLError, ValueError) as exc:
            last_error = str(exc)
            time.sleep(2)
    if runner:
        show_server_failure(state, runner, last_error)
    raise TimeoutError(f"Model server was not healthy within {timeout}s: {last_error}")


def diagnose_server_log(text: str) -> str:
    lower = text.lower()
    if "erroroutofdevicememory" in lower and any(word in lower for word in ("max", "fused", "imatrix")):
        return "This is a fused MAX repack; it cannot load on this card. Choose a standard-layout catalog model."
    if "missing tensor" in lower and "nextn" in lower:
        return "MTP metadata mismatch detected; run the nextn metadata repair."
    if "missing tensor" in lower and "blk." in lower:
        return "Block-count metadata mismatch detected; run GGUF verification/repair."
    if "vulkan" in lower and any(word in lower for word in ("fail", "error", "no device")):
        return "Vulkan initialization failed; re-run inference environment setup."
    if "erroroutofdevicememory" in lower:
        return "GPU allocation failed. Choose a smaller quant/context and verify the BIOS 12 GiB UMA allocation."
    if "no module named" in lower:
        return "The inference environment is incomplete; re-run environment setup."
    return "Review the server log above; it is the ground truth for model load failures."


def show_server_failure(state: dict[str, Any], runner: CommandRunner, error: str = "") -> str:
    server_log = str(state.get("server_log", "/root/llama-server.log"))
    command = ["tail", "-n", "120", server_log]
    result = runner.run(elevated(command) if server_log.startswith("/root/") else command, check=False)
    guidance = diagnose_server_log(result.stdout)
    runner.emit(f"Server health failure: {error}")
    runner.emit(f"Guidance: {guidance}")
    return guidance


def ensure_server(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Self-healing entry point: healthy stays up, stopped starts, sick restarts."""
    status = service_status(state, runner)
    if not status.get("active"):
        if not state.get("current_model"):
            raise RuntimeError("No model is installed yet; complete setup before ensuring the server")
        if runner:
            runner.emit("Model server is stopped; starting it and waiting for health")
        return {"ensured": "started", **restart_and_wait(state, runner)}
    try:
        health = health_check(state, timeout=3)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
        if runner:
            runner.emit(f"Active server is unhealthy ({exc}); restarting it")
        return {"ensured": "restarted", **restart_and_wait(state, runner)}
    return {"ensured": "already-healthy", **health}


def system_metrics() -> dict[str, Any]:
    # Discover on every invocation because cardN renumbers.
    from .hardware import _bytes_to_mib, _read_text, find_amd_gpu

    gpu = find_amd_gpu()
    if not gpu:
        return {"error": "No AMD GPU found"}
    hwmon_values: dict[str, str] = {}
    for hwmon in (gpu / "hwmon").glob("hwmon*"):
        for filename in ("temp1_input", "freq1_input", "power1_average"):
            value = _read_text(hwmon / filename)
            if value:
                hwmon_values[filename] = value
    return {
        "gpu_path": str(gpu),
        "vram_used_mib": _bytes_to_mib(_read_text(gpu / "mem_info_vram_used")),
        "vram_total_mib": _bytes_to_mib(_read_text(gpu / "mem_info_vram_total")),
        **hwmon_values,
    }
