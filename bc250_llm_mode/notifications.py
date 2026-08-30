"""Privacy-safe desktop notification preferences, receipts, and delivery.

No API in this module accepts rendered copy.  Callers provide one closed,
already-hashed event identity; bundled category copy is the only content that
can reach the fixed-argv desktop adapter.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .legacy_import import utcnow


NOTIFICATION_SCHEMA_VERSION = 1
MASTER = "MASTER"
CATEGORIES = (
    "OPERATION_SUCCESS",
    "OPERATION_FAILURE",
    "THERMAL_WARNING",
    "THERMAL_STOP",
    "STORAGE_CRITICAL",
    "BACKUP_FAILURE",
    "BACKUP_STALE",
    "REMOTE_SAFETY_DISABLE",
    "APPLICATION_UPDATE",
)
PREFERENCE_KEYS = (MASTER, *CATEGORIES)
SOURCE_CLASSES = (
    "OPERATION",
    "THERMAL",
    "STORAGE",
    "BACKUP",
    "REMOTE_ACCESS",
    "APPLICATION_UPDATE",
)
DELIVERY_STATES = ("DELIVERED", "SUPPRESSED", "FAILED")
REASON_CODES = (
    "DELIVERED",
    "PREFERENCE_DISABLED",
    "CAPABILITY_UNAVAILABLE",
    "DUPLICATE",
    "RATE_GLOBAL",
    "RATE_CATEGORY",
    "DELIVERY_RESERVED",
    "ADAPTER_FAILED",
    "ADAPTER_TIMEOUT",
)
ADAPTER_CLASSES = ("notify-send", "unavailable")
MAX_RECEIPTS = 256
MAX_HOURLY_DELIVERIES = 3
CATEGORY_COOLDOWN_SECONDS = 10 * 60
GLOBAL_WINDOW_SECONDS = 60 * 60
MAX_DELIVERY_ATTEMPTS = 2
DELIVERY_TIMEOUT_SECONDS = 3.0

_CRITICAL_CATEGORIES = frozenset({
    "THERMAL_STOP", "REMOTE_SAFETY_DISABLE",
})

_COPY = {
    "OPERATION_SUCCESS": ("normal", "A model operation finished."),
    "OPERATION_FAILURE": ("normal", "A model operation needs attention."),
    "THERMAL_WARNING": ("normal", "Temperature is high; performance was reduced."),
    "THERMAL_STOP": ("critical", "The LLM server stopped for temperature safety."),
    "STORAGE_CRITICAL": ("critical", "Model storage is critically low."),
    "BACKUP_FAILURE": ("normal", "A backup or restore needs attention."),
    "BACKUP_STALE": ("normal", "A current verified backup is unavailable."),
    "REMOTE_SAFETY_DISABLE": ("critical", "Remote access was disabled for safety."),
    "APPLICATION_UPDATE": ("normal", "A verified application update is available."),
}
_TEST_COPY = ("normal", "Local notifications are working.")


class NotificationError(ValueError):
    """Closed notification contract or compare-and-swap refusal."""


def _require_category(category: str, *, master: bool = False) -> str:
    value = str(category).strip().upper()
    allowed = PREFERENCE_KEYS if master else CATEGORIES
    if value not in allowed:
        raise NotificationError("unknown notification category")
    return value


def _require_source(source_class: str) -> str:
    value = str(source_class).strip().upper()
    if value not in SOURCE_CLASSES:
        raise NotificationError("unknown notification source class")
    return value


def _parse_time(value: str) -> _datetime.datetime:
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NotificationError("notification clock returned an invalid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return parsed.astimezone(_datetime.timezone.utc)


def _minus_seconds(value: str, seconds: int) -> str:
    parsed = _parse_time(value) - _datetime.timedelta(seconds=int(seconds))
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_identity_part(value: Any) -> str:
    """Reject free-form/path-like identity before hashing.

    Event builders accept only bounded stable identifiers and numeric
    revisions/timestamps. This prevents an accidental secret/path/body from
    entering even transient canonical identity serialization.
    """
    if isinstance(value, bool) or value is None:
        raise NotificationError("notification identity part is invalid")
    rendered = str(value)
    if not (1 <= len(rendered) <= 160):
        raise NotificationError("notification identity part is out of bounds")
    lowered = rendered.lower()
    forbidden = ("/", "\\", "bearer ", "authorization", "hf_", "ghp_", "-----begin")
    if any(marker in lowered for marker in forbidden):
        raise NotificationError("notification identity part is not a stable identifier")
    return rendered


def _receipt_key(category: str, source_class: str, parts: Iterable[Any]) -> str:
    document = {
        "category": _require_category(category),
        "identity_version": 1,
        "parts": [_safe_identity_part(part) for part in parts],
        "source_class": _require_source(source_class),
    }
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class NotificationEvent:
    category: str
    source_class: str
    receipt_key: str
    critical: bool = False

    def __post_init__(self) -> None:
        _require_category(self.category)
        _require_source(self.source_class)
        if len(self.receipt_key) != 64 or any(
            char not in "0123456789abcdef" for char in self.receipt_key
        ):
            raise NotificationError("receipt key must be lowercase sha256 hex")

    @classmethod
    def operation(
        cls, *, operation_id: str, terminal_state: str, revision: int,
        category: str,
    ) -> "NotificationEvent":
        category = _require_category(category)
        if category not in {"OPERATION_SUCCESS", "OPERATION_FAILURE", "BACKUP_FAILURE"}:
            raise NotificationError("category is not valid for an operation event")
        terminal = str(terminal_state).upper()
        allowed = {
            "SUCCEEDED", "FAILED_SAFE", "FAILED_ROLLED_BACK", "RECOVERY_REQUIRED"
        }
        if terminal not in allowed or int(revision) < 1:
            raise NotificationError("operation event is not a committed terminal")
        return cls(
            category,
            "BACKUP" if category == "BACKUP_FAILURE" else "OPERATION",
            _receipt_key(category,
                         "BACKUP" if category == "BACKUP_FAILURE" else "OPERATION",
                         (operation_id, terminal, int(revision))),
            critical=terminal == "RECOVERY_REQUIRED",
        )

    @classmethod
    def thermal(
        cls, *, category: str, transitioned_at: str, latch_state: str
    ) -> "NotificationEvent":
        category = _require_category(category)
        expected = {
            "THERMAL_WARNING": "throttled",
            "THERMAL_STOP": "stopped",
        }
        if expected.get(category) != str(latch_state).lower():
            raise NotificationError("thermal category does not match latch transition")
        _parse_time(transitioned_at)
        return cls(
            category, "THERMAL",
            _receipt_key(category, "THERMAL", (transitioned_at, latch_state)),
            critical=category == "THERMAL_STOP",
        )

    @classmethod
    def _evidence(
        cls,
        *,
        category: str,
        source_class: str,
        evidence_fingerprint: str,
        window: str,
    ) -> "NotificationEvent":
        category = _require_category(category)
        source_class = _require_source(source_class)
        allowed = {
            "STORAGE_CRITICAL": "STORAGE",
            "BACKUP_STALE": "BACKUP",
            "REMOTE_SAFETY_DISABLE": "REMOTE_ACCESS",
            "APPLICATION_UPDATE": "APPLICATION_UPDATE",
        }
        if allowed.get(category) != source_class:
            raise NotificationError("category does not match evidence source")
        if len(evidence_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in evidence_fingerprint
        ):
            raise NotificationError("evidence fingerprint must be sha256 hex")
        return cls(
            category, source_class,
            _receipt_key(category, source_class, (evidence_fingerprint, window)),
            critical=category in _CRITICAL_CATEGORIES,
        )

    @staticmethod
    def _window_start(window_start: str) -> str:
        parsed = _parse_time(window_start)
        if parsed.minute or parsed.second or parsed.microsecond or parsed.hour % 6:
            raise NotificationError("evidence window must be a UTC six-hour boundary")
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def storage(
        cls, *, evidence_fingerprint: str, window_start: str
    ) -> "NotificationEvent":
        return cls._evidence(
            category="STORAGE_CRITICAL", source_class="STORAGE",
            evidence_fingerprint=evidence_fingerprint,
            window=cls._window_start(window_start),
        )

    @classmethod
    def backup_stale(
        cls, *, evidence_fingerprint: str, window_start: str
    ) -> "NotificationEvent":
        return cls._evidence(
            category="BACKUP_STALE", source_class="BACKUP",
            evidence_fingerprint=evidence_fingerprint,
            window=cls._window_start(window_start),
        )

    @classmethod
    def remote_disable(cls, *, access_revision: int) -> "NotificationEvent":
        if int(access_revision) < 1:
            raise NotificationError("access revision must be positive")
        category = "REMOTE_SAFETY_DISABLE"
        return cls(
            category, "REMOTE_ACCESS",
            _receipt_key(category, "REMOTE_ACCESS", ("access-state", int(access_revision))),
            critical=True,
        )

    @classmethod
    def application_update(
        cls, *, release_digest: str, inventory_digest: str
    ) -> "NotificationEvent":
        for value in (release_digest, inventory_digest):
            if len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise NotificationError("update identity must use sha256 hex")
        category = "APPLICATION_UPDATE"
        return cls(
            category, "APPLICATION_UPDATE",
            _receipt_key(category, "APPLICATION_UPDATE", (release_digest, inventory_digest)),
        )


@dataclass(frozen=True)
class NotificationPreference:
    category: str
    enabled: bool
    created_at: str
    updated_at: str
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NotificationReceipt:
    receipt_key: str
    category: str
    source_class: str
    delivery_state: str
    reason_code: str
    adapter_class: str
    first_attempt_at: str
    last_attempt_at: str
    delivered_at: str | None
    occurrence_count: int
    attempt_count: int
    revision: int

    def to_dict(self) -> dict[str, Any]:
        # The key is itself redacted identity, but normal status needs only a
        # short correlation prefix rather than exporting persistent identity.
        value = asdict(self)
        value.pop("receipt_key")
        value["receipt_prefix"] = self.receipt_key[:8]
        return value


def _preference(row) -> NotificationPreference:
    return NotificationPreference(
        category=row["category"], enabled=bool(row["enabled"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        revision=int(row["revision"]),
    )


def _receipt(row) -> NotificationReceipt:
    return NotificationReceipt(
        receipt_key=row["receipt_key"], category=row["category"],
        source_class=row["source_class"], delivery_state=row["delivery_state"],
        reason_code=row["reason_code"], adapter_class=row["adapter_class"],
        first_attempt_at=row["first_attempt_at"],
        last_attempt_at=row["last_attempt_at"], delivered_at=row["delivered_at"],
        occurrence_count=int(row["occurrence_count"]),
        attempt_count=int(row["attempt_count"]), revision=int(row["revision"]),
    )


class NotificationPreferenceRepository:
    def __init__(self, conn, *, clock: Callable[[], str] = utcnow) -> None:
        self._conn = conn
        self._clock = clock

    def list(self) -> tuple[NotificationPreference, ...]:
        rows = self._conn.execute(
            "SELECT category, enabled, created_at, updated_at, revision "
            "FROM notification_preferences ORDER BY category"
        ).fetchall()
        return tuple(_preference(row) for row in rows)

    def get(self, category: str) -> NotificationPreference:
        category = _require_category(category, master=True)
        row = self._conn.execute(
            "SELECT category, enabled, created_at, updated_at, revision "
            "FROM notification_preferences WHERE category = ?", (category,),
        ).fetchone()
        if row is None:
            raise NotificationError("notification preference is missing")
        return _preference(row)

    def set(
        self, category: str, enabled: bool, *, expected_revision: int
    ) -> NotificationPreference:
        category = _require_category(category, master=True)
        if not isinstance(enabled, bool) or int(expected_revision) < 1:
            raise NotificationError("notification preference update is invalid")
        cursor = self._conn.execute(
            "UPDATE notification_preferences SET enabled = ?, updated_at = ?, "
            "revision = revision + 1 WHERE category = ? AND revision = ?",
            (int(enabled), self._clock(), category, int(expected_revision)),
        )
        if cursor.rowcount != 1:
            raise NotificationError("notification preference revision conflict")
        return self.get(category)


class NotificationReceiptRepository:
    _COLUMNS = (
        "receipt_key, category, source_class, delivery_state, reason_code, "
        "adapter_class, first_attempt_at, last_attempt_at, delivered_at, "
        "occurrence_count, attempt_count, revision"
    )

    def __init__(self, conn, *, clock: Callable[[], str] = utcnow) -> None:
        self._conn = conn
        self._clock = clock

    def get(self, receipt_key: str) -> NotificationReceipt | None:
        row = self._conn.execute(
            f"SELECT {self._COLUMNS} FROM notification_receipts "
            "WHERE receipt_key = ?", (receipt_key,),
        ).fetchone()
        return _receipt(row) if row else None

    def list_recent(self, *, limit: int = 20) -> tuple[NotificationReceipt, ...]:
        if not (1 <= int(limit) <= 100):
            raise NotificationError("receipt limit must be within 1..100")
        rows = self._conn.execute(
            f"SELECT {self._COLUMNS} FROM notification_receipts "
            "ORDER BY last_attempt_at DESC, receipt_key DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return tuple(_receipt(row) for row in rows)

    def delivered_since(self, since: str, *, category: str | None = None) -> int:
        if category is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM notification_receipts "
                "WHERE delivery_state = 'DELIVERED' AND delivered_at >= ?",
                (since,),
            ).fetchone()
        else:
            category = _require_category(category)
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM notification_receipts "
                "WHERE delivery_state = 'DELIVERED' AND delivered_at >= ? "
                "AND category = ?", (since, category),
            ).fetchone()
        return int(row["n"])

    def reserve(
        self,
        event: NotificationEvent,
        *,
        reason_code: str = "DELIVERY_RESERVED",
        adapter_class: str = "notify-send",
    ) -> NotificationReceipt:
        if reason_code not in REASON_CODES or adapter_class not in ADAPTER_CLASSES:
            raise NotificationError("invalid notification receipt classification")
        now = self._clock()
        existing = self.get(event.receipt_key)
        if existing is not None:
            if existing.reason_code == "DELIVERY_RESERVED" or (
                existing.delivery_state in {"DELIVERED", "SUPPRESSED"}
            ):
                self._conn.execute(
                    "UPDATE notification_receipts SET occurrence_count = "
                    "MIN(1000000, occurrence_count + 1), last_attempt_at = ?, "
                    "revision = revision + 1 WHERE receipt_key = ?",
                    (now, event.receipt_key),
                )
                return self.get(event.receipt_key)  # type: ignore[return-value]
            if existing.attempt_count >= MAX_DELIVERY_ATTEMPTS:
                return existing
            cursor = self._conn.execute(
                "UPDATE notification_receipts SET delivery_state = 'FAILED', "
                "reason_code = 'DELIVERY_RESERVED', adapter_class = ?, "
                "last_attempt_at = ?, attempt_count = attempt_count + 1, "
                "occurrence_count = MIN(1000000, occurrence_count + 1), "
                "revision = revision + 1 WHERE receipt_key = ? AND revision = ?",
                (adapter_class, now, event.receipt_key, existing.revision),
            )
            if cursor.rowcount != 1:
                raise NotificationError("notification receipt revision conflict")
            return self.get(event.receipt_key)  # type: ignore[return-value]
        self._conn.execute(
            "INSERT INTO notification_receipts (receipt_key, category, "
            "source_class, delivery_state, reason_code, adapter_class, "
            "first_attempt_at, last_attempt_at, occurrence_count, attempt_count, "
            "revision) VALUES (?, ?, ?, 'FAILED', ?, ?, ?, ?, 1, 1, 1)",
            (event.receipt_key, event.category, event.source_class, reason_code,
             adapter_class, now, now),
        )
        self.prune()
        return self.get(event.receipt_key)  # type: ignore[return-value]

    def suppress(
        self, event: NotificationEvent, *, reason_code: str,
        adapter_class: str = "unavailable",
    ) -> NotificationReceipt:
        if reason_code not in {
            "PREFERENCE_DISABLED", "CAPABILITY_UNAVAILABLE", "DUPLICATE",
            "RATE_GLOBAL", "RATE_CATEGORY",
        }:
            raise NotificationError("invalid suppression reason")
        existing = self.get(event.receipt_key)
        if existing is not None:
            self._conn.execute(
                "UPDATE notification_receipts SET occurrence_count = "
                "MIN(1000000, occurrence_count + 1), last_attempt_at = ?, "
                "revision = revision + 1 WHERE receipt_key = ?",
                (self._clock(), event.receipt_key),
            )
            return self.get(event.receipt_key)  # type: ignore[return-value]
        now = self._clock()
        self._conn.execute(
            "INSERT INTO notification_receipts (receipt_key, category, "
            "source_class, delivery_state, reason_code, adapter_class, "
            "first_attempt_at, last_attempt_at, occurrence_count, attempt_count, "
            "revision) VALUES (?, ?, ?, 'SUPPRESSED', ?, ?, ?, ?, 1, 0, 1)",
            (event.receipt_key, event.category, event.source_class, reason_code,
             adapter_class, now, now),
        )
        self.prune()
        return self.get(event.receipt_key)  # type: ignore[return-value]

    def finalize(
        self,
        receipt_key: str,
        *,
        expected_revision: int,
        delivered: bool,
        reason_code: str,
        adapter_class: str,
    ) -> NotificationReceipt:
        if reason_code not in REASON_CODES or adapter_class not in ADAPTER_CLASSES:
            raise NotificationError("invalid delivery result")
        if delivered and reason_code != "DELIVERED":
            raise NotificationError("delivered receipt requires DELIVERED reason")
        now = self._clock()
        cursor = self._conn.execute(
            "UPDATE notification_receipts SET delivery_state = ?, reason_code = ?, "
            "adapter_class = ?, last_attempt_at = ?, delivered_at = ?, "
            "revision = revision + 1 WHERE receipt_key = ? AND revision = ? "
            "AND reason_code = 'DELIVERY_RESERVED'",
            ("DELIVERED" if delivered else "FAILED", reason_code, adapter_class,
             now, now if delivered else None, receipt_key, int(expected_revision)),
        )
        if cursor.rowcount != 1:
            raise NotificationError("notification finalize fence lost")
        return self.get(receipt_key)  # type: ignore[return-value]

    def prune(self) -> None:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM notification_receipts"
        ).fetchone()
        excess = max(0, int(row["n"]) - MAX_RECEIPTS)
        if excess:
            self._conn.execute(
                "DELETE FROM notification_receipts WHERE receipt_key IN "
                "(SELECT receipt_key FROM notification_receipts "
                "ORDER BY last_attempt_at, receipt_key LIMIT ?)",
                (excess,),
            )


@dataclass(frozen=True)
class NotificationCapability:
    available: bool
    adapter_class: str
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    adapter_class: str
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DesktopNotificationAdapter:
    """One fixed-argv notify-send call; never logs or returns child output."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        env: dict[str, str] | None = None,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        self._which = which
        self._env = dict(os.environ if env is None else env)
        self._run = run

    def capability(self) -> NotificationCapability:
        executable = self._which("notify-send")
        session_ready = bool(
            self._env.get("DBUS_SESSION_BUS_ADDRESS")
            or self._env.get("WAYLAND_DISPLAY")
            or self._env.get("DISPLAY")
        )
        if not executable or not Path(executable).is_absolute() or not session_ready:
            return NotificationCapability(False, "unavailable", "CAPABILITY_UNAVAILABLE")
        return NotificationCapability(True, "notify-send", "DELIVERED")

    @staticmethod
    def _argv(executable: str, category: str, *, test: bool = False) -> list[str]:
        if test:
            urgency, body = _TEST_COPY
        else:
            category = _require_category(category)
            urgency, body = _COPY[category]
        return [
            executable,
            "--app-name=BC250 LLM MODE",
            "--icon=bc250-llm-mode",
            f"--urgency={urgency}",
            "--expire-time=10000",
            "BC250 LLM MODE",
            body,
        ]

    def deliver(self, category: str, *, test: bool = False) -> DeliveryResult:
        capability = self.capability()
        if not capability.available:
            return DeliveryResult(False, "unavailable", "CAPABILITY_UNAVAILABLE")
        executable = self._which("notify-send")
        assert executable is not None
        argv = self._argv(executable, category, test=test)
        try:
            result = self._run(
                argv,
                check=False,
                timeout=DELIVERY_TIMEOUT_SECONDS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return DeliveryResult(False, "notify-send", "ADAPTER_TIMEOUT")
        except OSError:
            return DeliveryResult(False, "notify-send", "ADAPTER_FAILED")
        if int(getattr(result, "returncode", 1)) != 0:
            return DeliveryResult(False, "notify-send", "ADAPTER_FAILED")
        return DeliveryResult(True, "notify-send", "DELIVERED")


class NotificationPreferenceService:
    def __init__(self, units: Any, *, clock: Callable[[], str] = utcnow) -> None:
        self._units = units
        self._clock = clock

    def status(self) -> dict[str, Any]:
        with self._units.read() as conn:
            rows = NotificationPreferenceRepository(conn, clock=self._clock).list()
        by_category = {row.category: row for row in rows}
        master = by_category[MASTER]
        return {
            "schema_version": NOTIFICATION_SCHEMA_VERSION,
            "master_enabled": master.enabled,
            "master_revision": master.revision,
            "categories": {
                category: by_category[category].to_dict() for category in CATEGORIES
            },
        }

    def set(
        self, category: str, enabled: bool, *, expected_revision: int
    ) -> dict[str, Any]:
        with self._units.begin() as conn:
            row = NotificationPreferenceRepository(
                conn, clock=self._clock
            ).set(category, enabled, expected_revision=expected_revision)
        return row.to_dict()


@dataclass(frozen=True)
class NotificationOutcome:
    category: str
    delivery_state: str
    reason_code: str
    adapter_class: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NotificationCoordinator:
    """Preference, dedupe, rate-limit, delivery, and receipt coordinator."""

    def __init__(
        self,
        units: Any,
        *,
        adapter: DesktopNotificationAdapter,
        clock: Callable[[], str] = utcnow,
    ) -> None:
        self._units = units
        self._adapter = adapter
        self._clock = clock

    @staticmethod
    def _outcome(receipt: NotificationReceipt, *, duplicate: bool = False):
        return NotificationOutcome(
            receipt.category,
            "SUPPRESSED" if duplicate else receipt.delivery_state,
            "DUPLICATE" if duplicate else receipt.reason_code,
            receipt.adapter_class,
        )

    def notify(self, event: NotificationEvent) -> NotificationOutcome:
        now = self._clock()
        capability = self._adapter.capability()
        with self._units.begin() as conn:
            preferences = NotificationPreferenceRepository(
                conn, clock=self._clock
            )
            receipts = NotificationReceiptRepository(conn, clock=self._clock)
            existing = receipts.get(event.receipt_key)
            if existing is not None and (
                existing.delivery_state in {"DELIVERED", "SUPPRESSED"}
                or existing.reason_code == "DELIVERY_RESERVED"
                or existing.attempt_count >= MAX_DELIVERY_ATTEMPTS
                or existing.first_attempt_at
                < _minus_seconds(now, GLOBAL_WINDOW_SECONDS)
            ):
                repeated = receipts.suppress(event, reason_code="DUPLICATE")
                return self._outcome(repeated, duplicate=True)

            if not preferences.get(MASTER).enabled or not preferences.get(
                event.category
            ).enabled:
                receipt = receipts.suppress(
                    event, reason_code="PREFERENCE_DISABLED",
                    adapter_class=capability.adapter_class,
                )
                return self._outcome(receipt)
            if not capability.available:
                receipt = receipts.suppress(
                    event, reason_code="CAPABILITY_UNAVAILABLE",
                    adapter_class="unavailable",
                )
                return self._outcome(receipt)
            if receipts.delivered_since(
                _minus_seconds(now, GLOBAL_WINDOW_SECONDS)
            ) >= MAX_HOURLY_DELIVERIES:
                receipt = receipts.suppress(
                    event, reason_code="RATE_GLOBAL",
                    adapter_class=capability.adapter_class,
                )
                return self._outcome(receipt)
            category_limited = receipts.delivered_since(
                _minus_seconds(now, CATEGORY_COOLDOWN_SECONDS),
                category=event.category,
            )
            critical_bypass = (
                (event.critical or event.category in _CRITICAL_CATEGORIES)
                and category_limited == 1
            )
            if category_limited and not critical_bypass:
                receipt = receipts.suppress(
                    event, reason_code="RATE_CATEGORY",
                    adapter_class=capability.adapter_class,
                )
                return self._outcome(receipt)
            reserved = receipts.reserve(event, adapter_class=capability.adapter_class)

        result = self._adapter.deliver(event.category)
        with self._units.begin() as conn:
            finalized = NotificationReceiptRepository(
                conn, clock=self._clock
            ).finalize(
                event.receipt_key,
                expected_revision=reserved.revision,
                delivered=result.delivered,
                reason_code=result.reason_code,
                adapter_class=result.adapter_class,
            )
        return self._outcome(finalized)

    def status(self) -> dict[str, Any]:
        capability = self._adapter.capability()
        with self._units.read() as conn:
            preferences = NotificationPreferenceRepository(
                conn, clock=self._clock
            ).list()
            receipts = NotificationReceiptRepository(
                conn, clock=self._clock
            ).list_recent(limit=20)
        counts = {state: 0 for state in DELIVERY_STATES}
        for receipt in receipts:
            counts[receipt.delivery_state] += 1
        return {
            "schema_version": NOTIFICATION_SCHEMA_VERSION,
            "capability": capability.to_dict(),
            "master_enabled": next(
                row.enabled for row in preferences if row.category == MASTER
            ),
            "categories": {
                row.category: row.enabled for row in preferences
                if row.category != MASTER
            },
            "recent_counts": counts,
            "recent": [receipt.to_dict() for receipt in receipts],
        }

    def test(self) -> DeliveryResult:
        with self._units.read() as conn:
            master = NotificationPreferenceRepository(conn).get(MASTER)
        if not master.enabled:
            return DeliveryResult(False, "unavailable", "PREFERENCE_DISABLED")
        return self._adapter.deliver("OPERATION_SUCCESS", test=True)


__all__ = [
    "ADAPTER_CLASSES",
    "CATEGORIES",
    "DELIVERY_STATES",
    "DeliveryResult",
    "DesktopNotificationAdapter",
    "MASTER",
    "MAX_RECEIPTS",
    "NOTIFICATION_SCHEMA_VERSION",
    "NotificationCapability",
    "NotificationCoordinator",
    "NotificationError",
    "NotificationEvent",
    "NotificationOutcome",
    "NotificationPreference",
    "NotificationPreferenceRepository",
    "NotificationPreferenceService",
    "NotificationReceipt",
    "NotificationReceiptRepository",
    "PREFERENCE_KEYS",
    "REASON_CODES",
    "SOURCE_CLASSES",
]
