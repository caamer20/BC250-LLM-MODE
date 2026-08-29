"""Same-user, thread-free GUI activation broker (EXP-1)."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import socket
import struct
import tempfile
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fsops import atomic_write_text
from .gui.routes import Route, parse_route

MAX_MESSAGE_BYTES = 2048
MAX_IDENTIFIER_CHARS = 256
ALLOWED_VERBS = frozenset({"ACTIVATE", "ROUTE", "OPEN_OPERATION", "OPEN_MODEL"})


@dataclass(frozen=True)
class ActivationRequest:
    verb: str
    route: str | None = None
    identifier: str | None = None

    def __post_init__(self) -> None:
        if self.verb not in ALLOWED_VERBS:
            raise ValueError("unsupported activation verb")
        if self.route is not None:
            parse_route(self.route)
        if self.identifier is not None and (
            not self.identifier or len(self.identifier) > MAX_IDENTIFIER_CHARS
            or any(ord(char) < 32 for char in self.identifier)
        ):
            raise ValueError("activation identifier is invalid")


class GuiInstanceBroker:
    def __init__(self, profile_dir: str | Path) -> None:
        self.profile_dir = Path(profile_dir)
        self.lock_path = self.profile_dir / "gui.lock"
        candidate = self.profile_dir / "gui.sock"
        # Darwin and several BSDs cap AF_UNIX names near 104 bytes. Keep the
        # lock/nonce in the private profile, but use a UID+profile-hashed name
        # in the protected runtime/tmp directory when the real path is too
        # long. The socket itself is still mode 0600 and nonce authenticated.
        if len(os.fsencode(candidate)) >= 96:
            runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
            digest = hashlib.sha256(os.fsencode(self.profile_dir.resolve())).hexdigest()[:20]
            candidate = runtime / f"bc250-gui-{os.getuid()}-{digest}.sock"
        self.socket_path = candidate
        self.nonce_path = self.profile_dir / "gui.nonce"
        self._lock = None
        self._server: socket.socket | None = None
        self._nonce: str | None = None

    def acquire(self) -> bool:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.profile_dir, 0o700)
        lock = open(self.lock_path, "a+b")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return False
        self._lock = lock
        # Exclusive lock ownership proves any previous socket/nonce is stale.
        self.socket_path.unlink(missing_ok=True)
        self.nonce_path.unlink(missing_ok=True)
        nonce = secrets.token_urlsafe(32)
        atomic_write_text(self.nonce_path, nonce, mode=0o600)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            server.listen(4)
            server.setblocking(False)
        except BaseException:
            server.close()
            self.nonce_path.unlink(missing_ok=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
            self._lock = None
            raise
        self._server = server
        self._nonce = nonce
        return True

    def activate_existing(self, request: ActivationRequest, timeout: float = 0.5) -> bool:
        try:
            nonce = self.nonce_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        payload = json.dumps({
            "nonce": nonce, "verb": request.verb,
            "route": request.route, "identifier": request.identifier,
        }, separators=(",", ":")).encode()
        if len(payload) > MAX_MESSAGE_BYTES:
            return False
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(str(self.socket_path))
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            return client.recv(16) == b"OK"
        except OSError:
            return False
        finally:
            client.close()

    def poll(self) -> list[ActivationRequest]:
        if self._server is None:
            return []
        requests = []
        while len(requests) < 8:
            try:
                conn, _ = self._server.accept()
            except BlockingIOError:
                break
            with conn:
                conn.settimeout(0.1)
                try:
                    if not self._same_uid(conn):
                        continue
                    data = conn.recv(MAX_MESSAGE_BYTES + 1)
                    if len(data) > MAX_MESSAGE_BYTES:
                        continue
                    value = json.loads(data.decode("utf-8"))
                    if not isinstance(value, dict) or value.get("nonce") != self._nonce:
                        continue
                    request = ActivationRequest(
                        str(value.get("verb")),
                        value.get("route"), value.get("identifier"),
                    )
                    requests.append(request)
                    conn.sendall(b"OK")
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
        return requests

    @staticmethod
    def _same_uid(conn: socket.socket) -> bool:
        if not hasattr(socket, "SO_PEERCRED"):
            return True
        try:
            raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", raw)
            return uid == os.getuid()
        except OSError:
            return False

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            self.socket_path.unlink(missing_ok=True)
            self.nonce_path.unlink(missing_ok=True)
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None
