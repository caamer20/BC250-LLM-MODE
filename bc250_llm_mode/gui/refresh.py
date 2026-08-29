"""One adaptive Tk ``after`` owner for the application window."""

from __future__ import annotations

from collections.abc import Callable


class RefreshCoordinator:
    def __init__(self, root, callback: Callable[[], None]) -> None:
        self._root = root
        self._callback = callback
        self._token = None
        self._closed = False
        self.active = False
        self.mapped = True

    @property
    def token(self):
        return self._token

    def interval_ms(self) -> int:
        if not self.mapped:
            return 30_000
        return 1_000 if self.active else 5_000

    def start(self) -> None:
        if self._closed or self._token is not None:
            return
        self._token = self._root.after(self.interval_ms(), self._tick)

    def _tick(self) -> None:
        self._token = None
        if self._closed:
            return
        self._callback()
        self.start()

    def request_now(self) -> None:
        if self._closed:
            return
        if self._token is not None:
            try:
                self._root.after_cancel(self._token)
            except Exception:
                pass
            self._token = None
        self._token = self._root.after(1, self._tick)

    def close(self) -> None:
        self._closed = True
        if self._token is not None:
            try:
                self._root.after_cancel(self._token)
            except Exception:
                pass
            self._token = None
