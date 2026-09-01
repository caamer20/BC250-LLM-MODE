# Command-safety audit (R1.3)

The original R1.3 inventory was generated from the then-live source. Its raw
historical snapshot lives in
[`command_audit_table.md`](command_audit_table.md); line numbers and deleted
pre-operation-engine call sites in that snapshot are not a current census.
Current command-construction changes are recorded as dated addenda below and
remain guarded by architecture tests plus the frozen elevation census.

## Disposition taxonomy

| Class | Meaning | Action |
| --- | --- | --- |
| PROBE | `check=False` existence/status/read probes (`container exists`, `get-default`, `tail`, `cat`) | Required and checked; failure handled by caller |
| CLEANUP | Best-effort stop/remove on revert paths (`podman stop --ignore`, `rm -f`, `reset-failed`) | Allowed; must stay non-critical and reported |
| ELEVATED-MUTATION | `elevated()` systemctl/rpm-ostree/udev/sysctl/cyan mutations | **R5 migration target** — replace with the allowlisted privileged helper; frozen by guard test until then |
| SHELL-STAGING | `bash -lc` build/update scripts in `env.py` | Paths derive from the validated state root + `TAG_PATTERN`-checked tags; R4.5 converts to typed argv/staging. Root containment validation tracked as R1.2 work |
| FS-MUTATION | `rm`/`mv` in llama.cpp update/rollback and uninstall | Bounded to fixed suffixes of the validated root (`-staging`, `-backup`, `-rolled`); uninstall model deletion is containment-checked |

## File-level dispositions

- `bootstrap.py` — one reviewed platform package plan: Bazzite
  `rpm-ostree install python3-tkinter` or CachyOS
  `pacman -S --needed --noconfirm tk`: ELEVATED-MUTATION (interactive,
  user-visible, documented in README preflight).
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
`elevated(` call-site count at **57** (the audited value; +1 in Session 5C for
the read-only `server.service_observation` `systemctl is-active` probe, which
is elevation-wrapped like every other systemd call). Any new elevation must
update this document and the guard together; R5 replaces them wholesale.

The additional 12 reviewed call sites belong to EUF-2's package-owned gateway
service lifecycle: atomic unit installation, daemon reload, disable/start/
stop/restart, and exact owned-unit removal. They use fixed service names and
validated absolute paths; no credential, client input, or source-checkout path
enters their argv. The generated unit remains disabled at boot.

## 2026-08-28 — ADR 007 CachyOS addendum

- `host_platform.py` constructs package argv only from closed enums and fixed
  package maps; no detected os-release value enters a command.
- `pacman -Qu` is a read-only PROBE. Any reported pending upgrade refuses the
  Tk install. `pacman -Sy` and automatic `pacman -Syu` are absent and guarded
  by tests.
- Bootstrap still contains exactly one `elevated()` package-install call site;
  selecting the Bazzite or CachyOS plan did not increase the frozen census.
- The Bazzite `rpm-ostree kargs` PROBE moved behind the composed platform
  authority. CachyOS boot managers are observation-only and produce no
  persistent boot command.
- Current-boot systemd/udev mutations still use `llmmode.py`'s existing audited
  `_run_root` boundary. Frontends no longer import that module directly.

## 2026-08-29 — GUI-8 unified-window reconciliation

- The historical raw snapshot still names the deleted `gui/dashboard.py` site;
  it remains an immutable historical record, not a live source reference.
- The unified GUI contains no direct systemctl, Podman, Tailscale, Pacman,
  rpm-ostree, or subprocess invocation. Home, Models, Activity, System,
  Settings, Help, native Chat, and five-chapter setup route through the single
  composed application/service boundary.
- Removing the legacy dashboard and mixin hierarchy added no elevated call
  site and did not change the frozen privileged-helper migration census.
