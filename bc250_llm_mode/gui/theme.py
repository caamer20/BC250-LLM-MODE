"""Small semantic ttk theme vocabulary; no image or network dependency."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    background: str
    surface: str
    foreground: str
    muted: str
    accent: str
    good: str
    warning: str
    danger: str
    focus: str


LIGHT = ThemeTokens(
    background="#f4f6f8", surface="#ffffff", foreground="#17212b",
    muted="#5b6670", accent="#3a68d8", good="#157347", warning="#9a6700",
    danger="#b42318", focus="#1f6feb",
)

DARK = ThemeTokens(
    background="#161b22", surface="#21262d", foreground="#f0f3f6",
    muted="#9da7b1", accent="#6ea8fe", good="#56d364", warning="#e3b341",
    danger="#ff7b72", focus="#79c0ff",
)

THEMES = {"light": LIGHT, "dark": DARK, "system": LIGHT}


def tokens(name: str) -> ThemeTokens:
    try:
        return THEMES[name]
    except KeyError:
        raise ValueError(f"unknown theme {name!r}") from None


def apply_theme(root, name: str) -> ThemeTokens:
    """Apply a small native ttk palette without loading image resources."""
    from tkinter import ttk

    palette = tokens(name)
    style = ttk.Style(root)
    style.configure(".", background=palette.background, foreground=palette.foreground)
    style.configure("TFrame", background=palette.background)
    style.configure("TLabel", background=palette.background, foreground=palette.foreground)
    style.configure("TLabelframe", background=palette.background)
    style.configure("TLabelframe.Label", background=palette.background, foreground=palette.foreground)
    style.configure("NoticeTitle.TLabel", font=("TkDefaultFont", 10, "bold"))
    style.configure("DrawerTitle.TLabel", font=("TkDefaultFont", 11, "bold"))
    try:
        root.configure(background=palette.background)
    except Exception:
        pass
    return palette
