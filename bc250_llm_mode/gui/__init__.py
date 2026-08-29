"""Native GUI entry surface.

The package initializer deliberately imports no tkinter module.  Pure route,
presentation, queue, and refresh contracts must remain usable by headless CLI
and packaging checks even when the host Python has no Tk extension.
"""

from __future__ import annotations


def _window_class():
    from .shell import ApplicationWindow

    return ApplicationWindow


_WIZARD = None


def __getattr__(name: str):
    global _WIZARD
    if name == "STEP_TITLES":
        from .app import STEP_TITLES

        return STEP_TITLES
    if name == "Wizard":
        if _WIZARD is None:
            _WIZARD = _window_class()
        return _WIZARD
    raise AttributeError(name)


def run_gui(application, management: bool = False) -> None:
    try:
        import tkinter as tk

        window = _window_class()
        window(application, management=management).mainloop()
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Tkinter is not available. Install the reviewed host Tk package "
            "and launch from a local graphical session."
        ) from exc
    except tk.TclError as exc:
        raise RuntimeError(
            "A local graphical display is required for BC250 LLM MODE."
        ) from exc


__all__ = ["Wizard", "run_gui", "STEP_TITLES"]
