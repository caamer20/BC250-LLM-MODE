"""Model-convert command (P6 §12.4) — honestly unavailable in this build.

``ModelConvertCommandService`` is the ONE entry every frontend reaches for
model conversion. Because this build ships no pinned, verified converter
executable/container, every conversion request is refused BEFORE any external
effect (no enqueue, no process, no file mutation) with the single honest
reason from :mod:`operations.model_convert`. This satisfies the P6 exit gate:
conversion "remains visibly unavailable with an honest reason."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .operations.model_convert import (
    availability,
    converter_available,
    decode_convert_request,
    CONVERTER_UNAVAILABLE_REASON,
)


@dataclass(frozen=True)
class ConvertOutcome:
    status: str  # UNAVAILABLE | (future: CONVERTED / QUARANTINED / ...)
    operation_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "CONVERTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation_id": self.operation_id,
            **self.detail,
        }


class ModelConvertCommandService:
    """Refuses-before-effect while no pinned, verified converter exists."""

    def __init__(self, *, units: Any = None) -> None:
        self._units = units

    def availability(self) -> dict[str, Any]:
        return availability()

    def convert(
        self,
        source_alias: str,
        target_quantization: str,
        requested_by: str = "cli",
    ) -> ConvertOutcome:
        # Validate the request shape first (bounded, no external effect).
        request = decode_convert_request(
            {
                "source_alias": source_alias,
                "target_quantization": target_quantization,
                "requested_by": requested_by,
            }
        )
        if not converter_available():
            # Refuse BEFORE any external effect (P6 §12.4 / exit gate).
            return ConvertOutcome(
                "UNAVAILABLE",
                None,
                {
                    "reason": CONVERTER_UNAVAILABLE_REASON,
                    "source_alias": request.source_alias,
                    "target_quantization": request.target_quantization,
                },
            )
        # Unreachable in this build: a real conversion workflow would be
        # enqueued here once a pinned, verified converter is provisioned.
        return ConvertOutcome(
            "UNAVAILABLE", None, {"reason": CONVERTER_UNAVAILABLE_REASON}
        )
