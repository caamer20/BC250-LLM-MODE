"""Session 4.1 §3.1: reproducible test-count evidence.

The handoff once reported 313 tests while the checkout collected 301 —
counts must never be inferred from progress dots. This conftest records the
authoritative collected count after collection and prints it in the terminal
summary so every report can cite a reproducible number.
"""

_COLLECTED = {"count": 0}


def pytest_collection_finish(session) -> None:
    _COLLECTED["count"] = len(session.items)


def pytest_terminal_summary(terminalreporter, *_args, **_kwargs) -> None:
    terminalreporter.section("collection")
    terminalreporter.write_line(
        f"collected test count (authoritative): {_COLLECTED['count']}"
    )
