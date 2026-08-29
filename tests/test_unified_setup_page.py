from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _gui_stubs import install  # noqa: E402

install()

from bc250_llm_mode.app import Application  # noqa: E402
from bc250_llm_mode.disclaimer import DISCLAIMER_TEXT, acknowledgment_valid  # noqa: E402
from bc250_llm_mode.gui.routes import setup_chapter_for  # noqa: E402
from bc250_llm_mode.gui.setup_page import (  # noqa: E402
    LEGACY_STEP_CHAPTERS,
    WORKLOAD_GOALS,
    SetupPageMixin,
    hardware_status_rows,
    hardware_technical_detail,
    setup_resume_view,
    workload_goal,
)
from bc250_llm_mode.hardware import HardwareReport  # noqa: E402
from bc250_llm_mode.memory_profile import analyze_memory_profile  # noqa: E402
from bc250_llm_mode.paths import AppPaths  # noqa: E402
from bc250_llm_mode.services import SETUP_STAGES  # noqa: E402


def _report() -> HardwareReport:
    return HardwareReport(
        gpu_path="/sys/class/drm/card1/device",
        vram_total_mib=12288,
        vis_vram_mib=12288,
        vram_used_mib=0,
        gtt_mib=2560,
        host_ram_mib=4096,
        host_available_mib=3072,
        disk_free_gib=80.0,
        is_bc250=True,
        vulkan_device="AMD BC-250 (RADV GFX1013)",
    )


def test_canonical_stage_progress_is_monotone_across_five_chapters():
    chapters = [setup_chapter_for(stage) for stage in SETUP_STAGES]
    assert chapters == sorted(chapters)
    assert set(chapters) == set(range(5))
    assert LEGACY_STEP_CHAPTERS == (0, 1, 2, 2, 3, 3, 4, 4, 4, 4, 4)


@pytest.mark.parametrize("stage", SETUP_STAGES)
def test_every_canonical_stage_has_a_bounded_resume_screen(stage):
    view = setup_resume_view(stage, visible_step=0)
    assert 0 <= view.resume_step <= 10
    assert 1 <= view.progress_value <= 5
    assert view.stage == stage


def test_resume_status_names_reboot_pause_and_recovery_truthfully():
    reboot = setup_resume_view(
        "TKINTER_READY", visible_step=2, reboot_required=True
    )
    assert "Restart required" in reboot.status
    recovery = setup_resume_view(
        "MODEL_SELECTED", visible_step=6,
        active_operation={"active_count": 1, "recovery_required_count": 1},
    )
    assert "Recovery required" in recovery.status
    paused = setup_resume_view(
        "MODEL_SELECTED", visible_step=6,
        active_operation={"active_count": 1, "paused_count": 1},
    )
    assert "paused" in paused.status


def test_unacknowledged_read_only_check_can_resume_the_safety_screen():
    view = setup_resume_view("WELCOME", visible_step=0, legacy_phase=1)
    assert view.resume_step == 1
    assert view.durable_chapter == 0


def test_machine_rows_are_readable_and_technical_detail_is_bounded():
    report = _report()
    memory = analyze_memory_profile(report)
    platform = {
        "distro_id": "bazzite",
        "distro_name": "Bazzite",
        "integration_tier": "native",
    }
    rows = hardware_status_rows(report, memory, platform)
    assert [row.label for row in rows] == [
        "Host platform", "Compute device", "Fast GPU memory", "Host memory",
        "Model storage", "Vulkan", "UMA profile",
    ]
    assert all(row.state != "blocked" for row in rows)
    assert all("/sys/class/drm" not in row.value for row in rows)
    technical = hardware_technical_detail(report, memory, platform)
    assert len(technical) <= 8192
    assert "/sys/class/drm/card1/device" in technical


def test_workload_goals_are_closed_and_goal_first():
    assert [goal.goal_id for goal in WORKLOAD_GOALS] == [
        "everyday", "long", "several", "quality", "advanced"
    ]
    assert workload_goal("several").slots == 4
    assert workload_goal("long").context == 32768
    with pytest.raises(ValueError):
        workload_goal("arbitrary-shell-profile")


def test_exact_disclaimer_gate_is_unchanged_and_setup_has_no_messagebox():
    assert "BC250 LLM MODE — SAFETY & SETUP WARNING. READ CAREFULLY." in DISCLAIMER_TEXT
    assert "UNLOCK ALL 40 COMPUTE UNITS" in DISCLAIMER_TEXT
    assert "~12 GiB GPU / ~4 GiB system" in DISCLAIMER_TEXT
    assert acknowledgment_valid(True, True, True, "I ACCEPT")
    assert not acknowledgment_valid(True, True, True, "I accept")
    source = Path("bc250_llm_mode/gui/setup_page.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "messagebox" not in source


class _SetupHarness(SetupPageMixin):
    def __init__(self, application: Application) -> None:
        self.application = application
        self.state_data = application.read_model()
        self._synced = dict(self.state_data)
        self.hardware_report = _report()

    def refresh_snapshot(self) -> None:
        self.state_data = self.application.read_model()
        self._synced = dict(self.state_data)

    def commit_narrow(self) -> int:
        return 0


def test_setup_page_records_the_full_canonical_chain(tmp_path):
    application = Application.compose(AppPaths.temporary(tmp_path))
    harness = _SetupHarness(application)
    application.setup.acknowledge_safety()
    harness._record_machine_check()
    transitions = (
        ("TKINTER_READY", "LLM_MODE_CONFIGURED"),
        ("LLM_MODE_CONFIGURED", "RUNTIME_READY"),
        ("RUNTIME_READY", "MODEL_SELECTED"),
        ("MODEL_SELECTED", "MODEL_PREPARED"),
        ("MODEL_PREPARED", "PROFILE_APPLIED"),
        ("PROFILE_APPLIED", "SERVICE_INSTALLED"),
        ("SERVICE_INSTALLED", "OPTIONALS_CONFIGURED"),
        ("OPTIONALS_CONFIGURED", "VERIFIED"),
    )
    for expected, next_stage in transitions:
        harness._record_setup_stage(expected, next_stage, {"test": True})
    application.setup.mark_setup_complete()
    assert application.setup.current_workflow()["stage"] == "COMPLETE"
