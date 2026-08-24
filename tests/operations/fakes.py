"""Durable fake world and deterministic providers (Session 5B §6).

The fake world is REAL durable state on disk under the injected temporary
profile: separately constructed executors observe the same files, so
recovery tests prove postconditions instead of trusting Python objects.
No secrets; no production host integration; no sleeps.
"""

from __future__ import annotations

import json
import threading
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bc250_llm_mode.fsops import atomic_write_text


class SimulatedProcessDeath(BaseException):
    """Test-only abrupt process-loss sentinel.

    Deliberately a ``BaseException``: ordinary exception compensation and
    ``finally`` lease release must NOT run for it.
    """

    def __init__(self, point: str) -> None:
        super().__init__(point)
        self.point = point


class FakeClock:
    """Manually advanced UTC clock producing ADR-formatted timestamps."""

    def __init__(self, start: str = "2026-08-23T12:00:00Z") -> None:
        import datetime

        self._datetime = datetime
        self._moment = datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")

    def advance(self, seconds: int) -> str:
        self._moment += self._datetime.timedelta(seconds=int(seconds))
        return self.now()

    def now(self) -> str:
        return self._moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Repository clock provider alias.
    __call__ = now


class SequenceIds:
    """Deterministic id factory: prefix-0001, prefix-0002, ..."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.lock = threading.Lock()
        self.counter = 0

    def __call__(self) -> str:
        with self.lock:
            self.counter += 1
            return f"{self.prefix}-{self.counter:04d}"


def deterministic_uuid() -> str:
    return str(_uuid.uuid4())


class CrashInjector:
    """Named crash points; each armed point fires exactly once.

    Raises :class:`SimulatedProcessDeath` (a BaseException) so the engine's
    ordinary failure compensation cannot accidentally run.
    """

    def __init__(self) -> None:
        self._armed: dict[tuple[str, str], bool] = {}
        self.fired: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def arm(self, step_key: str, point: str) -> None:
        with self.lock:
            self._armed[(step_key, point)] = True

    def check(self, step_key: str, point: str) -> None:
        with self.lock:
            if self._armed.pop((step_key, point), False):
                self.fired.append((step_key, point))
                raise SimulatedProcessDeath(f"{step_key}:{point}")


class EffectRecorder:
    """Bounded structured evidence of adapter call order."""

    MAX_ENTRIES = 256

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def record(self, kind: str, **detail: Any) -> None:
        with self.lock:
            if len(self.entries) < self.MAX_ENTRIES:
                entry = {"kind": kind, **detail}
                self.entries.append(entry)

    def acquisition_order(self) -> list[str]:
        return [e["resource"] for e in self.entries if e["kind"] == "acquire"]

    def effect_order(self) -> list[str]:
        return [e["step"] for e in self.entries if e["kind"] == "effect"]

    def compensation_order(self) -> list[str]:
        return [e["step"] for e in self.entries if e["kind"] == "compensate"]


@dataclass
class FakeWorld:
    """Real on-disk effect state shared by every executor instance."""

    root: Path

    @property
    def desired_path(self) -> Path:
        return self.root / "desired.json"

    @property
    def active_path(self) -> Path:
        return self.root / "active.json"

    @property
    def prior_path(self) -> Path:
        return self.root / "prior.json"

    @property
    def publication_path(self) -> Path:
        return self.root / "publication.json"

    def set_desired(self, value: str) -> None:
        atomic_write_text(
            self.desired_path,
            json.dumps({"value": value}, indent=2, sort_keys=True),
        )

    def read_desired(self) -> dict[str, Any]:
        return json.loads(self.desired_path.read_text(encoding="utf-8"))

    def capture_prior(self, value: str) -> None:
        atomic_write_text(
            self.prior_path,
            json.dumps({"value": value}, indent=2, sort_keys=True),
        )

    def read_prior(self) -> dict[str, Any] | None:
        if not self.prior_path.exists():
            return None
        return json.loads(self.prior_path.read_text(encoding="utf-8"))

    def apply_effect(self, value: str, effect_id: str) -> None:
        current = self.read_active()
        effects = list(current.get("effects", []))
        if effect_id in effects:
            return  # idempotent by effect identity
        atomic_write_text(
            self.active_path,
            json.dumps(
                {
                    "value": value,
                    "application_count": int(current.get("application_count", 0)) + 1,
                    "effects": effects + [effect_id],
                    "compensated": False,
                },
                indent=2,
                sort_keys=True,
            ),
        )

    def read_active(self) -> dict[str, Any]:
        if not self.active_path.exists():
            return {
                "value": None,
                "application_count": 0,
                "effects": [],
                "compensated": False,
            }
        return json.loads(self.active_path.read_text(encoding="utf-8"))

    def restore_prior(self) -> None:
        prior = self.read_prior()
        current = self.read_active()
        atomic_write_text(
            self.active_path,
            json.dumps(
                {
                    "value": prior["value"] if prior else None,
                    "application_count": int(current.get("application_count", 0)),
                    "effects": list(current.get("effects", [])),
                    "compensated": True,
                },
                indent=2,
                sort_keys=True,
            ),
        )

    def publish(self, revision_marker: str) -> None:
        atomic_write_text(
            self.publication_path,
            json.dumps({"marker": revision_marker}, sort_keys=True),
        )

    def publication_exists(self) -> bool:
        return self.publication_path.exists()


def build_fake_world(root: Path) -> FakeWorld:
    root.mkdir(parents=True, exist_ok=True)
    return FakeWorld(root=root)