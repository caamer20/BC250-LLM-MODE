"""Query-first enforcement for the closed EXP-3 workload idle policies.

There is deliberately no daemon, timer, or thread here.  A caller may invoke
``evaluate`` or ``enforce_once`` from an existing bounded refresh/maintenance
cycle.  The only permitted effect is stopping the one server owner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class IdleDecision:
    policy: str
    action: str
    reason_code: str
    idle_seconds: int | None
    threshold_seconds: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc)


class IdlePolicyService:
    """Evaluate current applied-profile policy and optionally stop once."""

    def __init__(
        self,
        units,
        *,
        server_active: Callable[[], bool],
        stop_server: Callable[[], Any],
        now: Callable[[], str],
    ) -> None:
        self._units = units
        self._server_active = server_active
        self._stop_server = stop_server
        self._now = now

    def evaluate(
        self,
        *,
        last_request_at: str | None,
        desktop_mode: bool = False,
    ) -> IdleDecision:
        from .operations.repositories import OperationRepository
        from .repositories import RuntimeConfigRepository
        from .workload_profiles import WorkloadProfileRepository

        with self._units.read() as conn:
            runtime = RuntimeConfigRepository(conn).get()
            profile_id = runtime.get("profile_id")
            profile = (
                WorkloadProfileRepository(conn).get(
                    str(profile_id), include_deleted=True
                )
                if profile_id
                else None
            )
            active_operations = bool(OperationRepository(conn).list_active())
        policy = profile.idle_policy if profile is not None else "KEEP_LOADED"
        if not self._server_active():
            return IdleDecision(policy, "NONE", "SERVER_ALREADY_STOPPED", None, None)
        if active_operations:
            return IdleDecision(policy, "NONE", "ACTIVE_OPERATION", None, None)
        if desktop_mode:
            return IdleDecision(policy, "STOP", "DESKTOP_MODE", None, None)
        if policy == "STOP_ON_DESKTOP":
            return IdleDecision(policy, "NONE", "WAITING_FOR_DESKTOP", None, None)
        if policy == "KEEP_LOADED":
            return IdleDecision(policy, "NONE", "KEEP_LOADED_CURRENT_BOOT", None, None)
        threshold = int(profile.stop_after_minutes or 0) * 60
        observed = _parse_utc(last_request_at)
        now = _parse_utc(self._now())
        if observed is None or now is None:
            return IdleDecision(policy, "NONE", "REQUEST_ACTIVITY_UNKNOWN", None, threshold)
        idle = max(0, int((now - observed).total_seconds()))
        if idle < threshold:
            return IdleDecision(policy, "NONE", "IDLE_INTERVAL_NOT_REACHED", idle, threshold)
        return IdleDecision(policy, "STOP", "STOP_AFTER_ELAPSED", idle, threshold)

    def enforce_once(
        self,
        *,
        last_request_at: str | None,
        desktop_mode: bool = False,
    ) -> dict[str, Any]:
        decision = self.evaluate(
            last_request_at=last_request_at, desktop_mode=desktop_mode
        )
        stopped = False
        if decision.action == "STOP":
            self._stop_server()
            stopped = True
        return {**decision.to_dict(), "stopped": stopped, "started": False}


__all__ = ["IdleDecision", "IdlePolicyService"]
