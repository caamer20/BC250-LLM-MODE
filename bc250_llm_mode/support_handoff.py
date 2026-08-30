"""Privacy-safe local support handoff for typed repair outcomes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .repair_center import REPAIR_ACTION_IDS

SUPPORT_HANDOFF_VERSION = 1
MAX_RECOVERY_COMMANDS = 3
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,95}")


def _opaque(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = str(value)
    return candidate if _OPAQUE.fullmatch(candidate) else None


def _code(value: str | None, fallback: str) -> str:
    candidate = str(value or "")
    return candidate if _CODE.fullmatch(candidate) else fallback


@dataclass(frozen=True)
class SupportHandoff:
    action_id: str
    result_code: str
    probe_code: str
    operation_id: str | None
    prior_state_survives: bool
    support_relevance: str
    offline_commands: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SUPPORT_HANDOFF_VERSION,
            "action_id": self.action_id,
            "result_code": self.result_code,
            "probe_code": self.probe_code,
            "operation_id": self.operation_id,
            "prior_state_survives": self.prior_state_survives,
            "support_relevance": self.support_relevance,
            "support_bundle_available": True,
            "support_bundle_uploaded": False,
            "offline_commands": [list(item) for item in self.offline_commands],
        }


def repair_support_handoff(
    *,
    action_id: str,
    target_id: str | None,
    result_code: str,
    probe_code: str,
    operation_id: str | None,
    prior_state_survives: bool,
    support_relevance: str,
    recovery_required: bool = False,
) -> SupportHandoff:
    """Select bounded argv from a closed table; never include free-form text."""
    if action_id not in REPAIR_ACTION_IDS:
        raise ValueError("unknown repair action for support handoff")
    target = _opaque(target_id)
    operation = _opaque(operation_id)
    commands: list[tuple[str, ...]] = []
    verify = ("bc250-llm-mode", "repair", "verify", action_id)
    if target is not None:
        verify += (target,)
    commands.append(verify)
    if operation is not None:
        commands.append((
            "bc250-llm-mode", "operations", "show", operation, "--json",
        ))
        if recovery_required:
            commands.append((
                "bc250-llm-mode", "operations", "recover", operation,
                "--confirm", "--json",
            ))
    return SupportHandoff(
        action_id=action_id,
        result_code=_code(result_code, "REPAIR_RESULT_UNKNOWN"),
        probe_code=_code(probe_code, "REPAIR_PROBE_UNKNOWN"),
        operation_id=operation,
        prior_state_survives=bool(prior_state_survives),
        support_relevance=_code(support_relevance, "REPAIR"),
        offline_commands=tuple(commands[:MAX_RECOVERY_COMMANDS]),
    )


__all__ = [
    "MAX_RECOVERY_COMMANDS", "SUPPORT_HANDOFF_VERSION", "SupportHandoff",
    "repair_support_handoff",
]
