"""Small local-model probes with one deadline across connect, headers and body.

Only literal IPv4 loopback is accepted: no DNS, environment proxy, redirect,
background timer, worker process, or TLS trust decision is involved. SocketIO
uses recv_into(), so every buffered HTTP read shares the original deadline.
"""
from __future__ import annotations

import http.client
import math
import socket
import time
from urllib.parse import urlsplit


class LoopbackHTTPError(OSError):
    """Closed, content-free local probe failure."""


class _DeadlineSocket(socket.socket):
    def __init__(self, deadline: float):
        super().__init__(socket.AF_INET, socket.SOCK_STREAM)
        self.deadline = deadline

    def budget(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Local probe deadline')
        self.settimeout(remaining)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self.budget()
        return super().recv_into(buffer, nbytes, flags)


def request_bytes(url: str, *, timeout: float, maximum_bytes: int,
                  body: bytes | None = None, expected_status: int | None = None) -> bytes:
    """GET or JSON POST a bounded body from the configured local model only."""
    if (not isinstance(url, str) or len(url) > 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in url)):
        raise ValueError('Invalid local probe URL')
    parts = urlsplit(url)
    if (parts.scheme != 'http' or parts.hostname != '127.0.0.1'
            or parts.username is not None or parts.password is not None
            or parts.fragment or '\\' in url):
        raise ValueError('Local probes require literal IPv4 loopback HTTP')
    port = parts.port if parts.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError('Invalid local probe port')
    if (type(timeout) not in {int, float} or not math.isfinite(timeout)
            or not 0 < timeout <= 300 or type(maximum_bytes) is not int
            or not 0 < maximum_bytes <= 8 * 1024 * 1024
            or body is not None and (not isinstance(body, bytes) or len(body) > 1024 * 1024)):
        raise ValueError('Invalid local probe bounds')
    if expected_status is not None and (type(expected_status) is not int or not 100 <= expected_status <= 599):
        raise ValueError('Invalid expected local probe status')
    target = parts.path or '/'
    if parts.query:
        target += '?' + parts.query
    method = 'POST' if body is not None else 'GET'
    headers = (f'{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
               'Accept-Encoding: identity\r\nConnection: close\r\n')
    if body is not None:
        headers += f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n'
    wire = (headers + '\r\n').encode('ascii') + (body or b'')
    deadline = time.monotonic() + timeout
    try:
        with _DeadlineSocket(deadline) as peer:
            peer.budget()
            peer.connect(('127.0.0.1', port))
            peer.budget()
            peer.sendall(wire)
            with http.client.HTTPResponse(peer) as response:
                response.begin()
                if (not 200 <= response.status < 300
                        or expected_status is not None and response.status != expected_status):
                    raise LoopbackHTTPError('Local model HTTP probe did not succeed')
                lengths = [v.strip(' \t') for v in response.headers.get_all('Content-Length', [])]
                transfers = [v.strip(' \t') for v in response.headers.get_all('Transfer-Encoding', [])]
                if (len(lengths) > 1 or len(transfers) > 1 or lengths and transfers
                        or lengths and (not lengths[0].isascii() or not lengths[0].isdecimal())
                        or transfers and transfers[0].lower() != 'chunked'
                        or response.headers.get('Content-Encoding', 'identity').lower() != 'identity'):
                    raise LoopbackHTTPError('Invalid local model HTTP framing')
                if response.length is not None and response.length > maximum_bytes:
                    raise LoopbackHTTPError('Local model HTTP reply exceeds its byte limit')
                raw = response.read(maximum_bytes + 1)
                peer.budget()
                if len(raw) > maximum_bytes:
                    raise LoopbackHTTPError('Local model HTTP reply exceeds its byte limit')
                if response.length not in (None, 0):
                    raise LoopbackHTTPError('Local model HTTP reply is incomplete')
                return raw
    except TimeoutError:
        raise LoopbackHTTPError('Local model HTTP probe exceeded its deadline') from None
    except (http.client.HTTPException, OSError) as exc:
        if isinstance(exc, LoopbackHTTPError):
            raise
        raise LoopbackHTTPError('Local model HTTP probe failed') from None
