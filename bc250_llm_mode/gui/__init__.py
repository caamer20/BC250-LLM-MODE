"""Single-window native tkinter setup wizard and operations dashboard."""

import tkinter as tk

from .app import GuiBase, STEP_TITLES
from .dashboard import DashboardMixin
from .forms import FormsMixin
from .steps import StepsMixin
from ..paths import AppPaths
from ..state import StateStore


class Wizard(StepsMixin, DashboardMixin, FormsMixin, GuiBase):
    """Composed application window.

    The public surface is frozen by tests/test_gui_contract.py.
    """


def run_gui(
    store: StateStore | None = None,
    management: bool = False,
    paths: AppPaths | None = None,
) -> None:
    try:
        Wizard(store, management=management, paths=paths).mainloop()
    except tk.TclError as exc:
        raise RuntimeError("A local graphical display is required for the native setup wizard.") from exc


__all__ = ["Wizard", "run_gui", "STEP_TITLES"]
