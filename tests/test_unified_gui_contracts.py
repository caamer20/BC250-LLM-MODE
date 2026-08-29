from __future__ import annotations

import queue
import time

import pytest

from bc250_llm_mode.gui.refresh import RefreshCoordinator
from bc250_llm_mode.gui.routes import (
    PRIMARY_ROUTES,
    Route,
    SETUP_CHAPTERS,
    available_routes,
    parse_route,
    setup_chapter_for,
)
from bc250_llm_mode.gui.tasks import MAX_RESULT_EVENTS, TaskLanes
from bc250_llm_mode.gui.theme import tokens
from bc250_llm_mode.gui.view_state import Confirmation, Notice, home_primary_action
from bc250_llm_mode.services import SETUP_STAGES


def test_every_setup_stage_maps_to_one_of_five_chapters():
    assert len(SETUP_CHAPTERS) == 5
    assert {setup_chapter_for(stage) for stage in SETUP_STAGES} == set(range(5))
    with pytest.raises(ValueError):
        setup_chapter_for("UNKNOWN")


def test_routes_are_closed_and_setup_gates_navigation():
    assert available_routes(setup_complete=False) == (Route.SETUP,)
    assert available_routes(setup_complete=True) == PRIMARY_ROUTES
    assert parse_route("models") is Route.MODELS
    with pytest.raises(ValueError):
        parse_route("run-any-shell-command")


def test_home_action_safety_and_recovery_outrank_chat():
    def snapshot(**states):
        return {"cards": {name: {"health": {"state": state}} for name, state in states.items()}}

    action = home_primary_action(snapshot(thermal="BLOCKED", operations="RECOVERY_REQUIRED", inference="READY"))
    assert action.code == "thermal"
    action = home_primary_action(snapshot(thermal="READY", operations="RECOVERY_REQUIRED", inference="READY"))
    assert action.code == "recovery"
    action = home_primary_action(snapshot(thermal="READY", operations="READY", model="READY", runtime="READY", inference="READY"))
    assert action.code == "chat"


def test_notice_and_confirmation_are_bounded():
    Notice("warning", "Needs attention", "Review the current operation.", dismissible=False)
    Confirmation("Remove model", "One managed alias is removed.", "Quarantine permits bounded restore.", "Remove", True)
    with pytest.raises(ValueError):
        Notice("debug", "x", "y")
    with pytest.raises(ValueError):
        Notice("info", "x", "y" * 3000)


def test_theme_vocabulary_is_closed():
    assert tokens("light").accent
    assert tokens("dark").foreground
    with pytest.raises(ValueError):
        tokens("downloaded-theme")


def test_task_lanes_bound_threads_queue_and_duplicate_actions():
    lanes = TaskLanes()
    gate = queue.Queue()
    try:
        assert lanes.results.maxsize == MAX_RESULT_EVENTS
        assert lanes.action.submit(1, lambda: gate.get(timeout=1))
        time.sleep(0.02)
        assert not lanes.action.submit(1, lambda: None)
        gate.put("done")
        result = lanes.results.get(timeout=1)
        assert result.value == "done"
    finally:
        lanes.close()


class _Root:
    def __init__(self):
        self.next_id = 0
        self.pending = {}

    def after(self, _delay, fn):
        self.next_id += 1
        self.pending[self.next_id] = fn
        return self.next_id

    def after_cancel(self, token):
        self.pending.pop(token, None)


def test_refresh_coordinator_owns_exactly_one_timer():
    root = _Root()
    calls = []
    coordinator = RefreshCoordinator(root, lambda: calls.append(True))
    coordinator.start()
    first = coordinator.token
    coordinator.start()
    assert coordinator.token == first
    coordinator.request_now()
    assert len(root.pending) == 1
    callback = root.pending.pop(coordinator.token)
    callback()
    assert calls == [True]
    assert len(root.pending) == 1
    coordinator.close()
    assert not root.pending
