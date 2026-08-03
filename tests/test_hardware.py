from pathlib import Path

from bc250_llm_mode.hardware import detect_hardware, find_amd_gpu


def write(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_vendor_detection_ignores_card_number(tmp_path):
    drm = tmp_path / "drm"
    write(drm / "card0/device/vendor", "0x10de\n")
    write(drm / "card7/device/vendor", "0x1002\n")
    assert find_amd_gpu(drm) == drm / "card7/device"


def test_report_reads_byte_counters(tmp_path):
    drm = tmp_path / "drm"
    gpu = drm / "card1/device"
    write(gpu / "vendor", "0x1002")
    write(gpu / "product_name", "BC-250 GFX1013")
    for name, mib in {
        "mem_info_vram_total": 12288,
        "mem_info_vis_vram_total": 12288,
        "mem_info_vram_used": 256,
        "mem_info_gtt_total": 2560,
    }.items():
        write(gpu / name, str(mib * 1024 * 1024))
    meminfo = tmp_path / "meminfo"
    write(meminfo, "MemTotal:        3774873 kB\nMemAvailable:    2097152 kB\n")
    models = tmp_path / "models"
    report = detect_hardware(models, drm_root=drm, proc_meminfo=meminfo, check_vulkan=False)
    assert report.gpu_path == str(gpu)
    assert report.vram_total_mib == 12288
    assert report.gtt_mib == 2560
    assert report.is_bc250

