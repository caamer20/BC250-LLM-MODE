from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

DISCLAIMER_TEXT = """============================================================
 BC250 LLM MODE — SAFETY & SETUP WARNING. READ CAREFULLY.
============================================================

THERMAL RISK
The AMD BC-250 can get INCREDIBLY HOT under sustained LLM inference load.
Sustained high temperatures may throttle performance or shorten the
hardware's lifespan. Ensure your system has adequate cooling and MONITOR
GPU TEMPERATURE during use.

COMPUTE UNITS (PERFORMANCE)
For best compute performance, UNLOCK ALL 40 COMPUTE UNITS (CUs) in your
BIOS/firmware if that option is available for your board. Running with
fewer CUs enabled will reduce throughput.

VRAM ALLOCATION (BIOS) — REQUIRED
This tool needs a large GPU memory carve-out. In your BIOS, set the VRAM /
UMA / "Integrated GPU memory" allocation to reserve enough memory for the
GPU while leaving enough for the operating system. On a 16 GB system the
RECOMMENDED split is ~12 GiB for the GPU and ~4 GiB for the system.
  - Too little GPU memory  -> larger models will NOT fit.
  - Too little system RAM  -> the OS/host can become unstable.
VERIFY THIS SETTING BEFORE CONTINUING.

SYSTEM BEHAVIOR
This tool configures your system for headless LLM inference and disables
power-saving/sleep features. Use at your own risk; you are responsible for
monitoring thermals and for any BIOS changes you make.

[ ] I understand the BC-250 may run very hot and I will monitor it.
[ ] I understand I should unlock all 40 CUs for best compute.
[ ] I have set the BIOS VRAM allocation appropriately (~12 GiB GPU / ~4 GiB system).
Type "I ACCEPT" to continue: ______"""


def acknowledgment_valid(thermal: bool, compute_units: bool, vram: bool, typed: str) -> bool:
    return thermal and compute_units and vram and typed.strip() == "I ACCEPT"


def acknowledge(state: dict[str, Any]) -> dict[str, Any]:
    state["disclaimer_ack"] = True
    state["ack_timestamp"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    state["setup_phase"] = max(int(state.get("setup_phase", 0)), 2)
    return state


def require_acknowledgment(state: dict[str, Any]) -> None:
    if not state.get("disclaimer_ack"):
        raise PermissionError("Safety disclaimer has not been acknowledged; run the GUI setup first.")
