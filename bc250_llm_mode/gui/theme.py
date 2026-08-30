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
    muted="#5b6670", accent="#3a68d8", good="#157347", warning="#8a5b00",
    danger="#b42318", focus="#1756a9",
)

DARK = ThemeTokens(
    background="#161b22", surface="#21262d", foreground="#f0f3f6",
    muted="#9da7b1", accent="#6ea8fe", good="#56d364", warning="#e3b341",
    danger="#ff7b72", focus="#79c0ff",
)

THEMES = {"light": LIGHT, "dark": DARK, "system": LIGHT}


def contrast_ratio(first: str, second: str) -> float:
    """Return the WCAG relative-luminance contrast ratio for two hex colors."""
    import re

    def luminance(value: str) -> float:
        if not isinstance(value, str) or not re.fullmatch(
            r"#[0-9a-fA-F]{6}", value
        ):
            raise ValueError("contrast colors must use #RRGGBB")
        channels = []
        for offset in (1, 3, 5):
            component = int(value[offset:offset + 2], 16) / 255
            channels.append(
                component / 12.92
                if component <= 0.04045
                else ((component + 0.055) / 1.055) ** 2.4
            )
        return (
            0.2126 * channels[0]
            + 0.7152 * channels[1]
            + 0.0722 * channels[2]
        )

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def tokens(name: str) -> ThemeTokens:
    try:
        return THEMES[name]
    except KeyError:
        raise ValueError(f"unknown theme {name!r}") from None


def apply_theme(root, name: str, *, scale_percent: int = 100) -> ThemeTokens:
    """Apply a small native ttk palette without loading image resources."""
    from tkinter import ttk

    palette = tokens(name)
    if int(scale_percent) not in {100, 125, 150, 175, 200}:
        raise ValueError("interface scale must be 100, 125, 150, 175, or 200")
    try:
        base = getattr(root, "_bc250_base_tk_scaling", None)
        if base is None:
            base = float(root.tk.call("tk", "scaling"))
            root._bc250_base_tk_scaling = base
        root.tk.call("tk", "scaling", base * int(scale_percent) / 100)
    except (AttributeError, TypeError, ValueError):
        # Headless contract tests and a few minimal Tk builds omit this call.
        pass
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


for _theme in (LIGHT, DARK):
    for _field in (
        "foreground", "muted", "accent", "good", "warning", "danger", "focus",
    ):
        if contrast_ratio(getattr(_theme, _field), _theme.background) < 4.5:
            raise RuntimeError(
                f"theme {_field} does not meet the text contrast floor"
            )
