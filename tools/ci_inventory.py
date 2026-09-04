"""Pytest plugin: persist exact selected/executed IDs and reject omissions."""
import json
import os
import platform
from pathlib import Path

_selected = []
_finished = set()
_skips = {}
_slow = []


def pytest_collection_finish(session):
    _selected[:] = [item.nodeid for item in session.items]
    _slow[:] = [item.nodeid for item in session.items if item.get_closest_marker("slow")]


def pytest_runtest_logreport(report):
    if report.when == "teardown":
        _finished.add(report.nodeid)
    if report.skipped:
        _skips[report.nodeid] = str(report.longrepr)


def pytest_sessionfinish(session, exitstatus):
    missing = sorted(set(_selected) - _finished)
    extra = sorted(_finished - set(_selected))
    if missing or extra or not _selected or len(_selected) != len(set(_selected)):
        session.exitstatus = 1
        exitstatus = 1
    path = Path(os.environ.get("BC250_TEST_INVENTORY", "test-inventory.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"selected": _selected,
        "finished": sorted(_finished), "missing": missing, "extra": extra,
        "skips": _skips, "slow": _slow, "python": platform.python_version(),
        "platform": platform.platform(), "exit_status": int(exitstatus)}, indent=2) + "\n")
