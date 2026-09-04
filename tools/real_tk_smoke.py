"""Real-widget Linux smoke gate; execute separately from pytest's Tk stubs.

Use `xvfb-run -a python -m tools.real_tk_smoke`. All state is temporary and host
observations/actions are suppressed. This qualifies real widget construction,
routing and focus reachability, not physical inference or human accessibility.
"""
from __future__ import annotations
import json
import tempfile
import threading
import time
from pathlib import Path


def main():
    import tkinter as tk
    from bc250_llm_mode.app import Application
    from bc250_llm_mode.paths import AppPaths
    from bc250_llm_mode.gui.shell import ApplicationWindow
    from bc250_llm_mode.gui.routes import Route
    from bc250_llm_mode.gui.theme import apply_theme
    from bc250_llm_mode.gui.view_state import Notice, Confirmation
    # Keep observations off actual host services while exercising actual Tcl/Tk.
    ApplicationWindow.request_observation = lambda *_args, **_kwargs: None
    ApplicationWindow._work = lambda *_args, **_kwargs: None
    results = []
    with tempfile.TemporaryDirectory(prefix="bc250-real-tk-") as temporary:
        app = Application.compose(AppPaths.temporary(Path(temporary)))
        state = app.read_model()
        app.commit_settings_changes(state, {**state, "setup_complete": True, "disclaimer_ack": True})
        window = ApplicationWindow(app, management=True)
        errors = []
        window.report_callback_exception = lambda *error: errors.append(str(error[1]))
        try:
            window.geometry("960x700")
            for scale in (100, 200):
                apply_theme(window, "light", scale_percent=scale)
                for route in Route:
                    if route is Route.SETUP:
                        continue
                    started = time.monotonic()
                    window.navigate(route)
                    window.update()
                    window.update_idletasks()
                    assert window._route == route, route
                    assert window._page.winfo_exists(), route
                    window._page.focus_primary()
                    window.update()
                    assert not errors, errors
                    # Actual focus chain must terminate/repeat within the widget bound.
                    focus = window._page
                    seen = set()
                    for _ in range(1000):
                        next_focus = focus.tk_focusNext()
                        if next_focus is None or str(next_focus) in seen:
                            break
                        seen.add(str(next_focus))
                        focus = next_focus
                    else:
                        raise AssertionError("focus chain exceeds widget bound")
                    results.append({"route": route.value, "scale": scale, "milliseconds": int((time.monotonic()-started)*1000), "focus_targets": len(seen)})
                window.notice_bar.show_notice(Notice("warning", "Synthetic failure", "Open Activity for details."))
                window.drawer.show_confirmation(Confirmation("Synthetic confirmation", "No host effect", "Cancel retains all data", "Continue"), lambda: None)
                window.update()
                assert not errors, errors
        finally:
            window._refresh_coordinator.close()
            window.destroy()
    print(json.dumps({"tk": tk.TkVersion, "routes": results, "thread_count": threading.active_count(), "physical_qualification": "pending"}, indent=2))


if __name__ == "__main__":
    main()
