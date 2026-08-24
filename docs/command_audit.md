# Command-safety audit (R1.3)

Generated from live source analysis; regenerate the raw table with
`scripts`-equivalent analysis after any command-construction change.
The full site inventory lives in [`command_audit_table.md`](command_audit_table.md).

## Disposition taxonomy

| Class | Meaning | Action |
| --- | --- | --- |
| PROBE | `check=False` existence/status/read probes (`container exists`, `get-default`, `tail`, `cat`) | Required and checked; failure handled by caller |
| CLEANUP | Best-effort stop/remove on revert paths (`podman stop --ignore`, `rm -f`, `reset-failed`) | Allowed; must stay non-critical and reported |
| ELEVATED-MUTATION | `elevated()` systemctl/rpm-ostree/udev/sysctl/cyan mutations | **R5 migration target** — replace with the allowlisted privileged helper; frozen by guard test until then |
| SHELL-STAGING | `bash -lc` build/update scripts in `env.py` | Paths derive from the validated state root + `TAG_PATTERN`-checked tags; R4.5 converts to typed argv/staging. Root containment validation tracked as R1.2 work |
| FS-MUTATION | `rm`/`mv` in llama.cpp update/rollback and uninstall | Bounded to fixed suffixes of the validated root (`-staging`, `-backup`, `-rolled`); uninstall model deletion is containment-checked |

## File-level dispositions

- `bootstrap.py` — `rpm-ostree install python3-tkinter`: ELEVATED-MUTATION
  (interactive, user-visible, documented in README preflight).
- `desktop.py`, `llmmode.py` — systemctl target/mask/isolate/disable + udev/karg
  mutations: ELEVATED-MUTATION (R5 helper).
- `env.py` — container probes (PROBE); `dnf`/clone/cmake build scripts
  (SHELL-STAGING: root from validated state, tag validated); update/rollback
  `rm`/`mv` (FS-MUTATION, fixed suffixes).
- `optimize.py` — cyan governor config write, udev/sysctl:
  ELEVATED-MUTATION (R5); targets are fixed allowlisted paths only.
- `server.py`, `openwebui.py`, `model_manager.py` — unprivileged podman/systemd
  probes/starts (PROBE / required-checked); unit installed via privileged copy
  (ELEVATED-MUTATION, R5).
- `uninstall.py` — revert/cleanup (CLEANUP) plus elevated unit removal
  (ELEVATED-MUTATION, R5); model deletion is containment-checked.
- `__main__.py`, `chat.py` — log/default-target probes (PROBE).

## Regression guard

`tests/test_production_gates.py::test_elevated_call_sites_frozen` freezes the
`elevated(` call-site count at **45** (the audited value; +1 in Session 5C for
the read-only `server.service_observation` `systemctl is-active` probe, which
is elevation-wrapped like every other systemd call). Any new elevation must
update this document and the guard together; R5 replaces them wholesale.
