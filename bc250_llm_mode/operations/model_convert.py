"""Durable ``MODEL_CONVERT v1`` — bounded conversion gate (P6 §12.4).

§12.4 permits conversion ONLY when every precondition holds: a pinned,
verified converter executable/container; typed argv (no shell); operation-
owned contained paths; bounded output size/CPU/memory/wall-time/file count;
cancellation leaving labeled partial output (never deleting a known-good
artifact); output identity verified before alias publication; and exactly-once
convergence across a crash between conversion and registration.

This build ships NO pinned, verified converter executable or container. Per the
P6 exit gate, conversion therefore "remains visibly unavailable with an honest
reason" rather than being faked: the request contract and decode are defined so
the operation is a known versioned type, but no workflow is registered and the
command service refuses BEFORE any external effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .validation import OperationValidationError

REQUEST_VERSION = 1
RECOVERY_POLICY_VERSION = 1

MAX_ALIAS_CHARS = 128

# The single honest reason conversion is unavailable in this build.
CONVERTER_UNAVAILABLE_REASON = (
    "no pinned, verified converter executable or container is bundled with "
    "this build; model conversion is unavailable until a digest-pinned "
    "converter is provisioned and verified"
)


@dataclass(frozen=True)
class ModelConvertRequestV1:
    source_alias: str
    target_quantization: str
    requested_by: str = "cli"


def _closed(payload: dict[str, Any], fields: frozenset) -> None:
    unknown = set(payload) - fields
    if unknown:
        raise OperationValidationError(
            f"unknown request fields: {sorted(unknown)}"
        )


def decode_convert_request(payload: dict[str, Any]) -> ModelConvertRequestV1:
    _closed(
        payload,
        frozenset({"source_alias", "target_quantization", "requested_by"}),
    )
    source_alias = payload.get("source_alias")
    target_quantization = payload.get("target_quantization")
    for name, value in (
        ("source_alias", source_alias),
        ("target_quantization", target_quantization),
    ):
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > MAX_ALIAS_CHARS
        ):
            raise OperationValidationError(
                f"{name} must be a short non-empty string"
            )
    return ModelConvertRequestV1(
        source_alias=source_alias,  # type: ignore[arg-type]
        target_quantization=target_quantization,  # type: ignore[arg-type]
        requested_by=str(payload.get("requested_by", "cli")),
    )


def converter_available() -> bool:
    """Honest availability probe: no pinned, verified converter ships here."""
    return False


def availability() -> dict[str, Any]:
    """Visibly-unavailable report with the honest reason (P6 exit gate)."""
    return {
        "available": converter_available(),
        "operation_type": "MODEL_CONVERT",
        "request_version": REQUEST_VERSION,
        "reason": None if converter_available() else CONVERTER_UNAVAILABLE_REASON,
    }
