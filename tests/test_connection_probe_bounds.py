"""Connection verification must neither leak credentials nor hold a lane forever."""
import time

import pytest

from bc250_llm_mode.connection_setup import BoundedHTTPProbeTransport
from test_runtime_policy_metrics import metrics_server
from test_loopback_http import reply


def test_connection_probe_does_not_forward_a_key_across_redirects():
    hits = []
    with metrics_server(reply(b'{}'), observe=lambda r: hits.append(r.headers.get('Authorization'))) as target:
        location = f'Location: http://127.0.0.1:{target}/metrics\r\nContent-Length: 0\r\n'.encode()
        with metrics_server(reply(b'', location, b'302 Found')) as port:
            result = BoundedHTTPProbeTransport().request(method='GET',
                url=f'http://127.0.0.1:{port}/metrics', token='synthetic-redirect-canary', timeout=2)
    assert not hits, 'A connection verification key must never follow a redirect'
    assert result.status == 302


def test_connection_probe_has_a_total_header_deadline():
    def slow(output, stop):
        output.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
        output.flush()
        for _ in range(30):
            if stop.wait(.08):
                return
            output.write(b'x'); output.flush()
        output.write(b'\r\nContent-Length: 2\r\n\r\n{}'); output.flush()

    with metrics_server(slow) as port:
        start = time.monotonic()
        with pytest.raises(OSError):
            BoundedHTTPProbeTransport().request(method='GET',
                url=f'http://127.0.0.1:{port}/metrics', token=None, timeout=1)
        assert time.monotonic() - start < 1.8
