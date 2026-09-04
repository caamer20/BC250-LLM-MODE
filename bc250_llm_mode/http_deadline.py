"""One bounded deadline interrupt per admitted HTTP response.

Chat has one admitted request; the gateway admits at most 16 handlers. Timers
are cancelled and joined at scope exit; they never form a persistent watcher.
"""
from contextlib import contextmanager
import socket
import threading


def interrupt_response(response):
    try:
        stream = getattr(response, "extensions", {}).get("network_stream")
        sock = stream.get_extra_info("socket") if stream else None
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except (OSError, AttributeError):
        pass
    try:
        response.close()
    except (OSError, AttributeError):
        pass


@contextmanager
def response_deadline(response, remaining):
    if remaining <= 0:
        raise TimeoutError("HTTP total deadline exceeded")
    expired = threading.Event()
    def interrupt():
        expired.set()
        interrupt_response(response)
    timer = threading.Timer(remaining, interrupt)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join(timeout=0.1)
        if expired.is_set():
            raise TimeoutError("HTTP total deadline exceeded")
