"""Shared headless-tkinter stubs for GUI-touching tests.

Install before importing any ``bc250_llm_mode.gui`` module; idempotent.
"""

from __future__ import annotations

import sys
import types


class _AnyWidget:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __getattr__(self, item):
        return _AnyWidget()

    def __call__(self, *_args, **_kwargs):
        return _AnyWidget()

    def __iter__(self):
        return iter(())

    def __getitem__(self, _item):
        return "widget"

    def __len__(self):
        return 0

    def __bool__(self):
        return True

    def __int__(self):
        # Widget values are not numbers; product code catches ValueError.
        raise ValueError("stub widget has no numeric value")

    def __float__(self):
        raise ValueError("stub widget has no numeric value")


class _StrVar:
    def __init__(self, *_a, **_k) -> None:
        pass

    def get(self):
        return ""

    def set(self, _value):
        pass


class _IntVar:
    def __init__(self, *_a, **_k) -> None:
        pass

    def get(self):
        return 8192

    def set(self, _value):
        pass


class _BoolVar:
    def __init__(self, *_a, **_k) -> None:
        pass

    def get(self):
        return False

    def set(self, _value):
        pass


class _StubModule(types.ModuleType):
    """Module stub that fabricates any missing attribute as an inert widget."""

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        widget = type(item, (_AnyWidget,), {})
        setattr(self, item, widget)
        return widget


def install() -> None:
    """Idempotent headless-tkinter installation for GUI-touching tests."""
    if "bc250_llm_mode.gui" in sys.modules:
        return
    for name in ("tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"):
        sys.modules.setdefault(name, _StubModule(name))
    tk = sys.modules["tkinter"]
    # Bind submodules explicitly: `from tkinter import ttk` prefers a parent
    # attribute over importing the submodule.
    for sub in ("ttk", "filedialog", "messagebox"):
        setattr(tk, sub, sys.modules[f"tkinter.{sub}"])
    tk.Tk = _AnyWidget
    tk.StringVar = _StrVar
    tk.IntVar = _IntVar
    tk.BooleanVar = _BoolVar
    tk.TclError = type("TclError", (Exception,), {})
