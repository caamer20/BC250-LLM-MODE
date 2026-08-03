"""BC-250 detection. DRM card numbers are deliberately never cached."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .constants import AMD_VENDOR_ID, DEFAULT_MODELS_DIR


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _bytes_to_mib(value: str | None) -> int:
    try:
        return int(value or "0") // (1024 * 1024)
    except ValueError:
        return 0


@dataclass(frozen=True)
class HardwareReport:
    gpu_path: str | None
    vram_total_mib: int
    vis_vram_mib: int
    vram_used_mib: int
    gtt_mib: int
    host_ram_mib: int
    host_available_mib: int
    disk_free_gib: float
    is_bc250: bool
    vulkan_device: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def find_amd_gpu(drm_root: Path = Path("/sys/class/drm")) -> Path | None:
    for card in sorted(drm_root.glob("card[0-9]*")):
        device = card / "device"
        if (_read_text(device / "vendor") or "").lower() == AMD_VENDOR_ID:
            return device
    return None


def _meminfo(proc_meminfo: Path) -> tuple[int, int]:
    values: dict[str, int] = {}
    text = _read_text(proc_meminfo) or ""
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        try:
            values[key] = int(raw.strip().split()[0]) // 1024
        except (ValueError, IndexError):
            pass
    return values.get("MemTotal", 0), values.get("MemAvailable", 0)


def _vulkan_name() -> str | None:
    if not shutil.which("vulkaninfo"):
        return None
    try:
        result = subprocess.run(
            ["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (result.stdout + result.stderr).splitlines():
        if "deviceName" in line and "=" in line:
            return line.split("=", 1)[1].strip()
    return None


def detect_hardware(
    models_dir: str | Path = DEFAULT_MODELS_DIR,
    *,
    drm_root: Path = Path("/sys/class/drm"),
    proc_meminfo: Path = Path("/proc/meminfo"),
    check_vulkan: bool = True,
) -> HardwareReport:
    gpu = find_amd_gpu(drm_root)
    target = Path(models_dir).expanduser()
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    disk_free = shutil.disk_usage(probe).free / 1024**3
    total, available = _meminfo(proc_meminfo)
    values = {
        "vram_total_mib": 0,
        "vis_vram_mib": 0,
        "vram_used_mib": 0,
        "gtt_mib": 0,
    }
    if gpu:
        names = {
            "vram_total_mib": "mem_info_vram_total",
            "vis_vram_mib": "mem_info_vis_vram_total",
            "vram_used_mib": "mem_info_vram_used",
            "gtt_mib": "mem_info_gtt_total",
        }
        values = {key: _bytes_to_mib(_read_text(gpu / filename)) for key, filename in names.items()}
    vulkan = _vulkan_name() if check_vulkan else None
    name_hint = " ".join(filter(None, [vulkan, _read_text(gpu / "product_name") if gpu else None]))
    is_bc250 = "bc-250" in name_hint.lower() or "gfx1013" in name_hint.lower()
    warnings: list[str] = []
    errors: list[str] = []
    if not gpu:
        errors.append("No AMD GPU (PCI vendor 0x1002) was found under /sys/class/drm.")
    elif values["vram_total_mib"] < 8192:
        errors.append("GPU VRAM is below 8 GiB. Set the BIOS UMA allocation to about 12 GiB GPU / 4 GiB OS.")
    elif values["vram_total_mib"] < 12200:
        warnings.append("GPU VRAM is below the recommended 12 GiB; larger catalog choices may not fit.")
    if total < 3072:
        errors.append("Host RAM is below 3 GiB; setup cannot run safely.")
    elif total < 4096:
        warnings.append("Host RAM is below 4 GiB; large host-RAM operations are disabled and mmap must remain enabled.")
    if disk_free < 20:
        errors.append("Less than 20 GiB is free on the model filesystem.")
    if gpu and not is_bc250:
        warnings.append("An AMD GPU was found, but its name did not identify it as BC-250/GFX1013.")
    return HardwareReport(
        gpu_path=str(gpu) if gpu else None,
        host_ram_mib=total,
        host_available_mib=available,
        disk_free_gib=round(disk_free, 2),
        is_bc250=is_bc250,
        vulkan_device=vulkan,
        warnings=warnings,
        errors=errors,
        **values,
    )

