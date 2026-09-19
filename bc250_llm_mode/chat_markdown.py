"""Small linear, bounded Markdown subset for a native text widget.

No HTML interpretation, image loading, URL fetching or executable content.
Unrecognized syntax remains readable text. Styling uses a fixed tag set.
"""
from __future__ import annotations

import re
import io
from dataclasses import dataclass

MAX_SPANS = 2048
MAX_CODE_BLOCKS = 128
_INLINE = re.compile(r"(\*\*[^*\n]{1,2048}\*\*|`[^`\n]{1,2048}`)")


@dataclass(frozen=True)
class Span:
    text: str
    style: str = "body"


def markdown_spans(text: str, *, max_spans: int = MAX_SPANS) -> tuple[Span, ...]:
    if type(max_spans) is not int or not 1 <= max_spans <= MAX_SPANS:
        raise ValueError("Unsupported Markdown span bound")
    spans = []
    code_start = 0
    fence = None
    code_blocks = 0
    offset = 0
    for line in io.StringIO(text):
        if len(spans) >= max_spans - 2:
            spans.append(Span(text[code_start if fence else offset:], "code" if fence else "body"))
            break
        offset += len(line)
        stripped = line.lstrip(" ")
        marker = "```" if stripped.startswith("```") else "~~~" if stripped.startswith("~~~") else None
        if fence:
            if marker == fence:
                spans.append(Span(text[code_start:offset - len(line)], "code"))
                fence = None
            continue
        if marker and code_blocks < MAX_CODE_BLOCKS:
            fence = marker
            code_start = offset
            code_blocks += 1
            continue
        if stripped.startswith("#") and " " in stripped[:8]:
            prefix, content = stripped.split(" ", 1)
            if 1 <= len(prefix) <= 6 and set(prefix) == {"#"}:
                spans.append(Span(content, "heading"))
                continue
        if stripped.startswith(("- ", "* ")):
            line = "• " + stripped[2:]
        elif stripped.startswith("> "):
            spans.append(Span(stripped[2:], "quote"))
            continue
        previous = 0
        for match in _INLINE.finditer(line):
            if len(spans) >= max_spans - 3:
                break
            if match.start() > previous:
                spans.append(Span(line[previous:match.start()]))
            value = match.group()
            bold = value.startswith("**")
            spans.append(Span(value[2:-2] if bold else value[1:-1], "strong" if bold else "inline_code"))
            previous = match.end()
        if previous < len(line):
            spans.append(Span(line[previous:]))
    else:
        if fence:
            spans.append(Span(text[code_start:], "code"))
    return tuple(spans)
