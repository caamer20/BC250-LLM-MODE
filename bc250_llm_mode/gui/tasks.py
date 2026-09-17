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


@dataclass
class _RetainedTask:
    """Keep Tk-owning closures until the UI can release them itself."""
    fn: Callable[[], Any]
    result: TaskResult | None = None


class BoundedTaskLane:
    def __init__(self, name: str, results: "queue.Queue[TaskResult]", *, coalesce: bool = False) -> None:
        self.name = name
        self._results = results
        self._coalesce = coalesce
        self._condition = threading.Condition()
        self._pending: tuple[int, int] | None = None
        self._owners: dict[int, _RetainedTask] = {}
        self._retired: list[int] = []
        self._next_ticket = 0
        self._owner_thread = threading.get_ident()
        self._running = False
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name=f"bc250-gui-{name}", daemon=True)
        self._thread.start()

    def submit(self, generation: int, fn: Callable[[], Any]) -> bool:
        self.reap_completed()
        with self._condition:
            if self._closed:
                return False
            if (self._running or self._pending is not None) and not self._coalesce:
                return False
            if self._pending is not None:
                # Coalesced work has never left this thread.
                self._owners.pop(self._pending[1], None)
            self._next_ticket += 1
            ticket = self._next_ticket
            self._owners[ticket] = _RetainedTask(fn)
            self._pending = (generation, ticket)
            self._condition.notify()
            return True

    def _publish(self, result: TaskResult) -> None:
        # Never evict a result on the worker: its callback may own Tk Variables.
        # Backpressure retains one result per lane and ends promptly on close.
        while True:
            with self._condition:
                if self._closed:
                    return
            try:
                self._results.put(result, timeout=0.05)
                return
            except queue.Full:
                continue

    def reap_completed(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("Task closures must be released on their submitting UI thread")
        with self._condition:
            retired, self._retired = self._retired, []
            for ticket in retired:
                self._owners.pop(ticket, None)

    def _loop(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed and self._pending is None:
                    return
                generation, ticket = self._pending
                owner = self._owners[ticket]
                self._pending = None
                self._running = True
            try:
                owner.result = TaskResult(self.name, generation, value=owner.fn())
            except BaseException as exc:  # the Tk boundary receives typed failure
                # Tracebacks retain worker frames and their later closures.
                # GUI errors use stable codes, never raw traceback rendering.
                exc.__traceback__ = None
                exc.__context__ = None
                exc.__cause__ = None
                owner.result = TaskResult(self.name, generation, error=exc)
            finally:
                # A completion may immediately submit its next phase. Advertise
                # that work has ended before the UI can receive the result.
                with self._condition:
                    self._running = False
                self._publish(owner.result)
                # Clear every worker reference BEFORE making the owner eligible
                # for UI-thread release. The queue remains UI-owned as well.
                owner = None
                with self._condition:
                    self._retired.append(ticket)

    def close(self, timeout: float = 0.25) -> None:
        with self._condition:
            self._closed = True
            if self._pending is not None:
                self._owners.pop(self._pending[1], None)
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout=timeout)
        self.reap_completed()


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
        # Discard queued completions on the UI thread, after producers close.
        # Keeping them in a root/callback cycle defers Tk finalizers to a later
        # garbage collection that might run on a worker.
        while True:
            try:
                self.results.get_nowait()
            except queue.Empty:
                break

    def reap_completed(self) -> None:
        for lane in self._lanes:
            lane.reap_completed()
