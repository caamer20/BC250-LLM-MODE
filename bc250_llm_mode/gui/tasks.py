"""Bounded task lanes for the Tk shell.

The lanes keep blocking work off Tk without becoming another operation
authority.  Action and chat submissions reject while occupied; observation
requests coalesce to the newest pending request.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_RESULT_EVENTS = 512


@dataclass(frozen=True)
class TaskResult:
    lane: str
    generation: int
    value: Any = None
    error: BaseException | None = None


class BoundedTaskLane:
    def __init__(self, name: str, results: "queue.Queue[TaskResult]", *, coalesce: bool = False) -> None:
        self.name = name
        self._results = results
        self._coalesce = coalesce
        self._condition = threading.Condition()
        self._pending: tuple[int, Callable[[], Any]] | None = None
        self._running = False
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name=f"bc250-gui-{name}", daemon=True)
        self._thread.start()

    def submit(self, generation: int, fn: Callable[[], Any]) -> bool:
        with self._condition:
            if self._closed:
                return False
            if (self._running or self._pending is not None) and not self._coalesce:
                return False
            self._pending = (generation, fn)
            self._condition.notify()
            return True

    def _publish(self, result: TaskResult) -> None:
        try:
            self._results.put_nowait(result)
        except queue.Full:
            try:
                self._results.get_nowait()
            except queue.Empty:
                pass
            try:
                self._results.put_nowait(result)
            except queue.Full:
                pass

    def _loop(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed and self._pending is None:
                    return
                generation, fn = self._pending
                self._pending = None
                self._running = True
            try:
                self._publish(TaskResult(self.name, generation, value=fn()))
            except BaseException as exc:  # the Tk boundary receives typed failure
                self._publish(TaskResult(self.name, generation, error=exc))
            finally:
                with self._condition:
                    self._running = False

    def close(self, timeout: float = 1.0) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout=timeout)


class TaskLanes:
    """Exactly three worker lanes and one bounded result queue."""

    def __init__(self) -> None:
        self.results: "queue.Queue[TaskResult]" = queue.Queue(MAX_RESULT_EVENTS)
        self.action = BoundedTaskLane("action", self.results)
        self.observation = BoundedTaskLane("observation", self.results, coalesce=True)
        self.chat = BoundedTaskLane("chat", self.results)
        self._lanes = (self.action, self.observation, self.chat)

    def close(self) -> None:
        for lane in self._lanes:
            lane.close()
