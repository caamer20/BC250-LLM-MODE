from types import SimpleNamespace

from bc250_llm_mode.memory_profile import analyze_memory_profile


def report(vram, host, gtt=2560):
    return SimpleNamespace(vram_total_mib=vram, host_ram_mib=host, gtt_mib=gtt)


def test_supported_12_4_profile():
    advice = analyze_memory_profile(report(12288, 3623))
    assert advice.profile == "supported-12-4"
    assert advice.supported is True
    assert advice.estimated_bios_split == "~12 GiB GPU / ~4 GiB OS"
    assert advice.live_resize_supported is False


def test_14_2_profile_is_rejected_as_host_starved():
    advice = analyze_memory_profile(report(14336, 1700, 1024))
    assert advice.profile == "host-starved"
    assert advice.supported is False
    assert advice.risk == "critical"
    assert "12 GiB GPU / 4 GiB OS" in advice.recommendation


def test_low_gpu_carveout_recommends_firmware_change():
    advice = analyze_memory_profile(report(8192, 7600))
    assert advice.profile == "gpu-limited"
    assert advice.supported is False
    assert "cannot grow" in advice.recommendation
