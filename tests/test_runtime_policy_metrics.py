"""Real loopback responses must not stall thermal/idle policy sampling."""
from contextlib import contextmanager
import http.server
import threading
import time

import pytest

from bc250_llm_mode import runtime_policy

METRICS = (b'llamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n'
           b'llamacpp:prompt_tokens_total 12\nllamacpp:tokens_predicted_total 3\n')


@contextmanager
def metrics_server(write_response, *, path="/metrics", posted=None, observe=None):
    finished = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            assert self.path == path
            if observe is not None:
                observe(self)
            try:
                write_response(self.wfile, finished)
            except (OSError, ValueError):
                pass
            finally:
                self.close_connection = True

        def do_POST(self):
            if posted is not None:
                posted.append(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            self.do_GET()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        finished.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize('phase', ['headers', 'body'])
def test_trickled_metrics_have_a_total_deadline(monkeypatch, phase):
    monkeypatch.setattr(runtime_policy, 'METRICS_TIMEOUT_SECONDS', .2, raising=False)

    def trickle(output, finished):
        if phase == 'headers':
            output.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
            chunks = [b'a'] * 25
            suffix = b'\r\nContent-Length: 0\r\n\r\n'
        else:
            output.write(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(METRICS)).encode() + b'\r\n\r\n')
            chunks = [METRICS[i:i + 4] for i in range(0, len(METRICS), 4)]
            suffix = b''
        output.flush()
        for chunk in chunks:
            if finished.wait(.04):
                return
            output.write(chunk)
            output.flush()
        output.write(suffix)
        output.flush()

    with metrics_server(trickle) as port:
        started = time.monotonic()
        observed = runtime_policy.request_activity(port)
        elapsed = time.monotonic() - started
        assert observed is None, 'Unfinished metrics must remain unknown, not idle'
        assert elapsed < .8, f'Metrics delayed the next safety poll for {elapsed:.3f}s'
