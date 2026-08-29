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
    if name in {"Wizard", "ApplicationWindow"}:
        if _WIZARD is None:
            _WIZARD = _window_class()
        return _WIZARD
    raise AttributeError(name)


def run_gui(application, management: bool = False, *, route: str | None = None) -> None:
    from ..instance_broker import BrokerRequest, GuiInstanceBroker

    broker = None
    if getattr(getattr(application, "paths", None), "app_dir", None) is not None:
        broker = GuiInstanceBroker(application.paths.app_dir)
        request = BrokerRequest("ROUTE" if route else "ACTIVATE", route=route)
        if not broker.acquire():
            if broker.activate_existing(request):
                return
            raise RuntimeError(
                "Another GUI instance owns this profile but could not be activated. "
                "Close it normally and try again."
            )
    try:
        import tkinter as tk

        # The explicit global permits narrow headless entry-boundary fakes;
        # production resolves the one ApplicationWindow lazily.
        window = globals().get("Wizard") or _window_class()
        instance = window(application, management=management)
        if broker is not None:
            instance.instance_broker = broker
        if route:
            instance.navigate(route)
        instance.mainloop()
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Tkinter is not available. Install the reviewed host Tk package "
            "and launch from a local graphical session."
        ) from exc
    except tk.TclError as exc:
        raise RuntimeError(
            "A local graphical display is required for BC250 LLM MODE."
        ) from exc
    finally:
        if broker is not None:
            broker.close()


__all__ = ["ApplicationWindow", "Wizard", "run_gui"]
