"""Strict, bounded evidence-record I/O for the release tooling (C1 §C1.1).

Reads evidence JSON documents from a contained directory. Fail-closed: files
that are too large, not valid JSON, not a JSON object, or escape the evidence
root are rejected with a stable reason rather than raising or being trusted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_EVIDENCE_BYTES = 1_000_000  # 1 MB per evidence record


def read_evidence_file(path: str | Path) -> tuple[dict[str, Any] | None, str]:
    """Read one evidence JSON file. Returns ``(record_or_None, reason)``;
    ``reason`` is empty on success."""
    p = Path(path)
    try:
        if not p.is_file():
            return None, "not-a-file"
        if p.stat().st_size > MAX_EVIDENCE_BYTES:
            return None, "too-large"
        raw = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"unreadable:{type(exc).__name__}"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return None, "invalid-json"
    if not isinstance(doc, dict):
        return None, "not-an-object"
    return doc, ""


def load_evidence_dir(evidence_dir: str | Path) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Load every ``*.json`` evidence record under a directory (sorted for
    determinism). Returns ``(records, errors)`` where errors are
    ``(relative_name, reason)`` pairs. Never raises on malformed input."""
    root = Path(evidence_dir)
    records: list[dict[str, Any]] = []
    errors: list[tuple[str, str]] = []
    if not root.is_dir():
        return records, [("<dir>", "missing-evidence-dir")]
    for path in sorted(root.rglob("*.json")):
        # Containment: skip anything resolving outside the evidence root.
        try:
            path.relative_to(root)
        except ValueError:
            errors.append((path.name, "escapes-evidence-root"))
            continue
        record, reason = read_evidence_file(path)
        if record is None:
            errors.append((path.name, reason))
        else:
            records.append(record)
    return records, errors
