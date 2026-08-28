"""BC-250 UMA profile analysis without pretending firmware memory is hot-resizable."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

BC250_PHYSICAL_MEMORY_GIB = 16
SUPPORTED_GPU_GIB = 12
SUPPORTED_HOST_GIB = 4
MIN_SAFE_HOST_MIB = 3072


@dataclass(frozen=True)
class MemoryProfileAdvice:
    profile: str
    supported: bool
    risk: str
    detected_vram_gib: float
    detected_host_usable_gib: float
    detected_gtt_gib: float
    estimated_bios_split: str
    live_resize_supported: bool
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _value(report: Any, name: str) -> int:
    if isinstance(report, dict):
        return int(report.get(name, 0) or 0)
    return int(getattr(report, name, 0) or 0)


def analyze_memory_profile(report: Any) -> MemoryProfileAdvice:
    """Classify the boot-time UMA carve-out using live AMDGPU/host counters."""
    vram_mib = _value(report, "vram_total_mib")
    host_mib = _value(report, "host_ram_mib")
    gtt_mib = _value(report, "gtt_mib")
    vram_gib = vram_mib / 1024
    host_gib = host_mib / 1024
    gtt_gib = gtt_mib / 1024
    estimated_gpu = max(0, min(BC250_PHYSICAL_MEMORY_GIB, round(vram_gib)))
    estimated_host = max(0, BC250_PHYSICAL_MEMORY_GIB - estimated_gpu)
    split = f"~{estimated_gpu} GiB GPU / ~{estimated_host} GiB OS"

    if vram_mib >= 13 * 1024 or host_mib < MIN_SAFE_HOST_MIB:
        profile = "host-starved"
        supported = False
        risk = "critical"
        recommendation = (
            "This split leaves too little host RAM for Linux, Podman, mmap bookkeeping, and the AMD driver. "
            "Return to approximately 12 GiB GPU / 4 GiB OS in firmware and reboot. The UMA carve-out cannot "
            "be resized safely while Linux is running."
        )
    elif vram_mib >= 11800 and host_mib >= MIN_SAFE_HOST_MIB:
        profile = "supported-12-4"
        supported = True
        risk = "normal"
        recommendation = (
            "Supported BC-250 profile. Keep approximately 12 GiB GPU / 4 GiB OS; use GTT only as slower spill. "
            "Changing the firmware split does not bypass the per-allocation wall."
        )
    else:
        profile = "gpu-limited"
        supported = False
        risk = "warning"
        recommendation = (
            "The GPU carve-out is below the supported 12 GiB target. Select approximately 12 GiB GPU / 4 GiB OS "
            "in firmware and reboot; Linux cannot grow the carve-out at runtime."
        )

    return MemoryProfileAdvice(
        profile=profile,
        supported=supported,
        risk=risk,
        detected_vram_gib=round(vram_gib, 2),
        detected_host_usable_gib=round(host_gib, 2),
        detected_gtt_gib=round(gtt_gib, 2),
        estimated_bios_split=split,
        live_resize_supported=False,
        recommendation=recommendation,
    )
