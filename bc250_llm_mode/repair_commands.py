"""Typed EXP-5 repair preview/execute/verify command boundary.

No action string is executable.  Composition injects one ``RepairBinding``
for every closed catalogue ID and this service rejects any incomplete or extra
mapping.  Every mutation is re-previewed against current truth immediately
before its owner is called.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .legacy_import import utcnow
from .repair_center import (
    REPAIR_ACTIONS,
    REPAIR_ACTION_IDS,
    REPAIR_CENTER_SCHEMA_VERSION,
    REPAIR_CONTRACT_VERSION,
    RepairAction,
    action_by_id,
)

PREVIEW_WINDOW_SECONDS = 15 * 60
MAX_EVIDENCE_ITEMS = 32
MAX_VALUE_CHARS = 160
_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}")
_SECRET_KEYS = ("secret", "token", "password", "credential", "authorization")


class RepairCommandError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _bounded_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Accept only small scalar evidence; reject secret-shaped keys."""
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_EVIDENCE_ITEMS:
        raise RepairCommandError("EVIDENCE_INVALID", "repair evidence is not bounded")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        lowered = key.lower()
        if (not 1 <= len(key) <= 64
                or any(fragment in lowered for fragment in _SECRET_KEYS)):
            raise RepairCommandError("EVIDENCE_INVALID", "repair evidence key refused")
        if raw_value is None or isinstance(raw_value, (bool, int)):
            result[key] = raw_value
        elif isinstance(raw_value, str) and len(raw_value) <= MAX_VALUE_CHARS:
            result[key] = raw_value
        else:
            raise RepairCommandError(
                "EVIDENCE_INVALID", "repair evidence value refused"
            )
    return result


@dataclass(frozen=True)
class RepairObservation:
    """Bounded facts observed by one action binding."""

    conditions: frozenset[str] = field(default_factory=frozenset)
    expected_revisions: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    reason_code: str | None = None

    def normalized(self) -> "RepairObservation":
        revisions: list[tuple[str, int]] = []
        for key, value in self.expected_revisions:
            if not _TARGET.fullmatch(str(key)) or int(value) < 0:
                raise RepairCommandError("EVIDENCE_INVALID", "revision evidence refused")
            revisions.append((str(key), int(value)))
        if len(revisions) > MAX_EVIDENCE_ITEMS:
            raise RepairCommandError("EVIDENCE_INVALID", "too many revision facts")
        return RepairObservation(
            conditions=frozenset(str(item) for item in self.conditions),
            expected_revisions=tuple(sorted(revisions)),
            evidence=_bounded_mapping(self.evidence),
            reason_code=str(self.reason_code) if self.reason_code else None,
        )


@dataclass(frozen=True)
class RepairOwnerResult:
    outcome: str
    code: str
    operation_id: str | None = None
    owner_revision: int | None = None
    prior_state_survives: bool = True
    # Ephemeral only: excluded from repr and every serialized result. The CLI
    # and GUI may reveal it once through their existing protected channel.
    one_time_secret: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class RepairProbe:
    passed: bool
    code: str
    prior_state_survives: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "code": self.code,
            "prior_state_survives": self.prior_state_survives,
        }


@dataclass(frozen=True)
class RepairBinding:
    action_id: str
    observe: Callable[[str | None], RepairObservation]
    execute: Callable[[str | None, RepairObservation], RepairOwnerResult]
    verify: Callable[[str | None, RepairObservation | None], RepairProbe]


@dataclass(frozen=True)
class RepairPreview:
    action: RepairAction
    target_id: str | None
    outcome: str
    reason_code: str | None
    missing_preconditions: tuple[str, ...]
    expected_revisions: tuple[tuple[str, int], ...]
    evidence_digest: str
    preview_digest: str
    confirmation_token: str
    expires_at: str

    @property
    def ready(self) -> bool:
        return self.outcome == "READY"

    def to_dict(self) -> dict[str, Any]:
        descriptor = self.action.to_dict(
            set(self.action.preconditions) - set(self.missing_preconditions)
        )
        descriptor.update({
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "target_id": self.target_id,
            "expected_revisions": dict(self.expected_revisions),
            "evidence_digest": self.evidence_digest,
            "preview_digest": self.preview_digest,
            "confirmation_token": self.confirmation_token,
            "expires_at": self.expires_at,
        })
        return descriptor


@dataclass(frozen=True)
class RepairResult:
    action_id: str
    target_id: str | None
    outcome: str
    result_code: str
    probe: RepairProbe
    operation_id: str | None = None
    owner_revision: int | None = None
    support_relevance: str = "REPAIR"
    one_time_secret: str | None = field(default=None, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.outcome == "SUCCEEDED" and self.probe.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REPAIR_CENTER_SCHEMA_VERSION,
            "contract_version": REPAIR_CONTRACT_VERSION,
            "action_id": self.action_id,
            "target_id": self.target_id,
            "outcome": self.outcome,
            "result_code": self.result_code,
            "operation_id": self.operation_id,
            "owner_revision": self.owner_revision,
            "prior_state_survives": self.probe.prior_state_survives,
            "support_relevance": self.support_relevance,
            "probe": self.probe.to_dict(),
            "has_one_time_secret": self.one_time_secret is not None,
        }


class RepairCommandService:
    """Revision-bound command service over an exhaustive closed mapping."""

    def __init__(
        self,
        bindings: Mapping[str, RepairBinding],
        *,
        clock: Callable[[], str] = utcnow,
    ) -> None:
        if set(bindings) != set(REPAIR_ACTION_IDS):
            missing = sorted(set(REPAIR_ACTION_IDS) - set(bindings))
            extra = sorted(set(bindings) - set(REPAIR_ACTION_IDS))
            raise ValueError(f"repair binding mismatch; missing={missing}, extra={extra}")
        for action_id, binding in bindings.items():
            if binding.action_id != action_id:
                raise ValueError("repair binding identity mismatch")
        self._bindings = dict(bindings)
        self._clock = clock

    @property
    def mapped_action_ids(self) -> tuple[str, ...]:
        return tuple(action.action_id for action in REPAIR_ACTIONS)

    def list_actions(self) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        for action in REPAIR_ACTIONS:
            if action.target_policy == "REQUIRED":
                preview = self._unavailable_preview(action, None, "TARGET_REQUIRED")
            else:
                preview = self.preview(action.action_id)
            rendered.append(preview.to_dict())
        return rendered

    def preview(self, action_id: str, target_id: str | None = None) -> RepairPreview:
        try:
            action = action_by_id(action_id)
        except KeyError as exc:
            raise RepairCommandError(
                "UNKNOWN_REPAIR_ACTION", "unknown repair action"
            ) from exc
        target = self._normalize_target(action, target_id)
        if action.target_policy == "REQUIRED" and target is None:
            return self._unavailable_preview(action, None, "TARGET_REQUIRED")
        try:
            observation = self._bindings[action.action_id].observe(target).normalized()
        except RepairCommandError:
            raise
        except Exception as exc:  # noqa: BLE001 - closed mapping, no message leak
            return self._unavailable_preview(
                action, target, f"OBSERVATION_{exc.__class__.__name__.upper()}"
            )
        return self._make_preview(action, target, observation)

    def run(
        self,
        action_id: str,
        target_id: str | None = None,
        *,
        preview_digest: str,
        confirmation_token: str,
    ) -> RepairResult:
        current = self.preview(action_id, target_id)
        if not current.ready:
            return self._refused(current, current.reason_code or "PRECONDITION_UNMET")
        if (preview_digest != current.preview_digest
                or confirmation_token != current.confirmation_token):
            return self._refused(current, "PREVIEW_STALE")
        observation = self._bindings[action_id].observe(current.target_id).normalized()
        repeated = self._make_preview(current.action, current.target_id, observation)
        if repeated.preview_digest != current.preview_digest:
            return self._refused(repeated, "PREVIEW_STALE")
        try:
            owner = self._bindings[action_id].execute(current.target_id, observation)
        except Exception as exc:  # noqa: BLE001 - never expose exception text
            probe = RepairProbe(False, "OWNER_FAILED", current.action.prior_state_survives)
            return RepairResult(
                action_id, current.target_id, "FAILED_SAFE",
                f"OWNER_{exc.__class__.__name__.upper()}", probe,
                support_relevance=current.action.support_relevance,
            )
        try:
            probe = self._bindings[action_id].verify(current.target_id, observation)
        except Exception as exc:  # noqa: BLE001
            probe = RepairProbe(
                False, f"PROBE_{exc.__class__.__name__.upper()}",
                owner.prior_state_survives,
            )
        outcome = "SUCCEEDED" if owner.outcome in {"SUCCEEDED", "ACCEPTED"} \
            and probe.passed else (
                "RECOVERY_REQUIRED" if owner.outcome == "RECOVERY_REQUIRED"
                else "FAILED_SAFE"
            )
        return RepairResult(
            action_id=action_id,
            target_id=current.target_id,
            outcome=outcome,
            result_code=owner.code if probe.passed else probe.code,
            probe=probe,
            operation_id=owner.operation_id,
            owner_revision=owner.owner_revision,
            support_relevance=current.action.support_relevance,
            one_time_secret=owner.one_time_secret,
        )

    def verify(self, action_id: str, target_id: str | None = None) -> RepairResult:
        action = action_by_id(action_id)
        target = self._normalize_target(action, target_id)
        if action.target_policy == "REQUIRED" and target is None:
            probe = RepairProbe(False, "TARGET_REQUIRED", action.prior_state_survives)
        else:
            probe = self._bindings[action_id].verify(target, None)
        return RepairResult(
            action_id, target, "SUCCEEDED" if probe.passed else "FAILED_SAFE",
            probe.code, probe, support_relevance=action.support_relevance,
        )

    def _make_preview(
        self, action: RepairAction, target: str | None,
        observation: RepairObservation,
    ) -> RepairPreview:
        missing = tuple(
            item for item in action.preconditions if item not in observation.conditions
        )
        outcome = "READY" if not missing and not observation.reason_code else "UNAVAILABLE"
        reason = observation.reason_code or (
            "PRECONDITION_UNMET" if missing else None
        )
        window, expires = self._preview_window()
        evidence = _canonical({
            "conditions": sorted(observation.conditions),
            "revisions": list(observation.expected_revisions),
            "evidence": dict(observation.evidence),
        })
        evidence_digest = hashlib.sha256(evidence).hexdigest()
        payload = _canonical({
            "contract_version": REPAIR_CONTRACT_VERSION,
            "action_id": action.action_id,
            "target_id": target,
            "mutation_steps": list(action.mutation_steps),
            "expected_revisions": list(observation.expected_revisions),
            "evidence_digest": evidence_digest,
            "window": window,
            "expires_at": expires,
            "outcome": outcome,
        })
        digest = hashlib.sha256(payload).hexdigest()
        return RepairPreview(
            action, target, outcome, reason, missing,
            observation.expected_revisions, evidence_digest, digest,
            f"REPAIR-{digest[:12].upper()}", expires,
        )

    def _unavailable_preview(
        self, action: RepairAction, target: str | None, reason: str,
    ) -> RepairPreview:
        return self._make_preview(
            action, target, RepairObservation(reason_code=reason)
        )

    @staticmethod
    def _refused(preview: RepairPreview, code: str) -> RepairResult:
        probe = RepairProbe(False, code, preview.action.prior_state_survives)
        return RepairResult(
            preview.action.action_id, preview.target_id, "REFUSED", code, probe,
            support_relevance=preview.action.support_relevance,
        )

    def _normalize_target(
        self, action: RepairAction, target_id: str | None
    ) -> str | None:
        if target_id is not None:
            target = str(target_id).strip()
            if not _TARGET.fullmatch(target):
                raise RepairCommandError("TARGET_INVALID", "repair target is invalid")
            return target
        if action.target_policy == "AUTO_BUNDLE":
            window, _expires = self._preview_window()
            return f"bundle-{window}"
        return None

    def _preview_window(self) -> tuple[int, str]:
        now = _parse_time(self._clock())
        epoch = int(now.timestamp())
        window = epoch // PREVIEW_WINDOW_SECONDS
        expires = _datetime.datetime.fromtimestamp(
            (window + 1) * PREVIEW_WINDOW_SECONDS,
            tz=_datetime.timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return window, expires


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _parse_time(value: str) -> _datetime.datetime:
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepairCommandError("CLOCK_INVALID", "repair clock is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone(_datetime.timezone.utc)


__all__ = [
    "RepairBinding", "RepairCommandError", "RepairCommandService",
    "RepairObservation", "RepairOwnerResult", "RepairPreview", "RepairProbe",
    "RepairResult",
]
