"""Locale-ready display formatters; identifiers and serialized data bypass them."""

from __future__ import annotations

import datetime as _datetime
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class FormatConventions:
    decimal_separator: str = "."
    thousands_separator: str = ","
    temperature_unit: str = "C"


DEFAULT_FORMAT = FormatConventions()


def format_number(value: int | float, *, decimals: int = 0, conventions: FormatConventions = DEFAULT_FORMAT) -> str:
    if not 0 <= int(decimals) <= 3:
        raise ValueError("decimal places must be 0..3")
    quantum = Decimal(1).scaleb(-int(decimals))
    rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    rendered = f"{rounded:,.{int(decimals)}f}"
    if conventions.thousands_separator != ",":
        rendered = rendered.replace(",", "\x00")
    if conventions.decimal_separator != ".":
        rendered = rendered.replace(".", conventions.decimal_separator)
    return rendered.replace("\x00", conventions.thousands_separator)


def format_bytes(value: int | float, *, conventions: FormatConventions = DEFAULT_FORMAT) -> str:
    amount = max(0, float(value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    decimals = 0 if index == 0 else 1
    return f"{format_number(amount, decimals=decimals, conventions=conventions)} {units[index]}"


def format_tokens(value: int, *, conventions: FormatConventions = DEFAULT_FORMAT) -> str:
    return f"{format_number(int(value), conventions=conventions)} tokens"


def format_temperature(value_c: int | float, *, conventions: FormatConventions = DEFAULT_FORMAT) -> str:
    if conventions.temperature_unit == "F":
        value = float(value_c) * 9 / 5 + 32
        unit = "°F"
    elif conventions.temperature_unit == "C":
        value = float(value_c)
        unit = "°C"
    else:
        raise ValueError("temperature unit must be C or F")
    return f"{format_number(value, decimals=1, conventions=conventions)} {unit}"


def format_duration(seconds: int | float, *, conventions: FormatConventions = DEFAULT_FORMAT) -> str:
    del conventions
    remaining = max(0, int(round(float(seconds))))
    parts = []
    for unit_seconds, label in ((86400, "day"), (3600, "hour"), (60, "minute")):
        count, remaining = divmod(remaining, unit_seconds)
        if count:
            parts.append(f"{count} {label}{'' if count == 1 else 's'}")
        if len(parts) == 2:
            break
    if not parts:
        parts.append(f"{remaining} second{'' if remaining == 1 else 's'}")
    return " ".join(parts)


def format_timestamp(value: str) -> str:
    try:
        parsed = _datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "Unknown time"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M %Z")


__all__ = [
    "DEFAULT_FORMAT", "FormatConventions", "format_bytes", "format_duration",
    "format_number", "format_temperature", "format_timestamp", "format_tokens",
]
