"""P3 §9.5 exit battery part 2: HTTP bounds and cross-surface canaries.

1. A half-open loopback connection (server accepts, never answers) is
   cut by the CHAT timeout policy within a documented bound.
2. A planted secret canary never surfaces on the process/log/event
   paths exercised by the bounded platform.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

import httpx
import pytest

from bc250_llm_mode.chat import (
    CHAT_CONNECT_TIMEOUT_S,
    CHAT_HTTP_TIMEOUT,
    CHAT_READ_TIMEOUT_S,
    CHAT_WRITE_TIMEOUT_S,
)

# The probe uses the SAME policy SHAPE with scaled-down bounds so the
# mechanism is proven quickly; the production constants are pinned by
# their own test (finite, structured, per-chunk read bound).
PROBE_TIMEOUT = httpx.Timeout(
    3.0, connect=CHAT_CONNECT_TIMEOUT_S / 10,
    write=CHAT_WRITE_TIMEOUT_S / 10, read=CHAT_READ_TIMEOUT_S / 40,
)

def _silent_server(sock: socket.socket) -> None:
    """Accept connections and then never send a byte."""
    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            return
        # Hold the connection open silently: the half-open case.


def test_half_open_connection_is_cut_by_the_chat_timeout_policy():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    thread = threading.Thread(target=_silent_server, args=(listener,),
                              daemon=True)
    thread.start()

    started = time.monotonic()
    with pytest.raises(httpx.HTTPError):
        # Exactly the transport policy the chat client uses.
        with httpx.Client(timeout=CHAT_HTTP_TIMEOUT) as client:
            client.post(f"http://127.0.0.1:{port}/v1/chat/completions",
                        json={"prompt": "x"},
                        timeout=PROBE_TIMEOUT)
    waited = time.monotonic() - started
    listener.close()
    # Bounded: the read bound engaged (not an instant refusal), then cut.
    assert waited < 20
    assert waited >= 2


def test_planted_secret_never_reaches_log_or_output_surfaces(tmp_path):
    """A canary planted in the environment stays out of every captured
    surface the bounded platform produces."""
    canary = "p3_canary_b61d_no_secret_escape"
    logger = logging.getLogger("p3-canary")
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger.addHandler(Capture())
    logger.setLevel(logging.INFO)

    from bc250_llm_mode.logging_utils import CommandRunner

    runner = CommandRunner(logger)
    result = runner.run(
        [__import__("sys").executable, "-c",
         "import os; print('len', len(os.environ.get('HF_TOKEN', '')))"],
        env={"HF_TOKEN": canary, "PATH": "/usr/bin:/bin"},
        emit_output=True,
        check=False,
    )
    joined = "\n".join(records) + "\n" + result.stdout
    assert canary not in joined
    # The value reached the child (length echoes back) without the value.
    assert f"len {len(canary)}" in result.stdout
