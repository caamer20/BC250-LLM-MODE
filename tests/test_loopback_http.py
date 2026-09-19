"""Real HTTP framing, size, privacy and deadline boundaries for local probes."""
import json
import threading
import time

import pytest

from bc250_llm_mode.loopback_http import request_bytes, LoopbackHTTPError
from bc250_llm_mode import runtime_policy, server, gateway_runtime, gateway_service
from test_runtime_policy_metrics import metrics_server, METRICS


def reply(body, headers=None, status=b'200 OK'):
    if headers is None:
        headers = b'Content-Length: ' + str(len(body)).encode() + b'\r\n'
    packet = b'HTTP/1.1 ' + status + b'\r\n' + headers + b'\r\n' + body
    return lambda output, _finished: (output.write(packet), output.flush())


@pytest.mark.parametrize('framing', ['length', 'chunked', 'eof'])
def test_valid_metrics_and_framing_without_threads_or_proxy(monkeypatch, framing):
    body, headers = METRICS, None
    if framing == 'chunked':
        body = f'{len(METRICS):x}\r\n'.encode() + METRICS + b'\r\n0\r\n\r\n'
        headers = b'Transfer-Encoding: chunked\r\n'
    elif framing == 'eof':
        headers = b'Connection: close\r\n'
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    with metrics_server(reply(body, headers)) as port:
        threads = {t.ident for t in threading.enumerate()}
        assert runtime_policy.request_activity(port) == {'active': 0, 'counter': [12., 3.]}
    assert not [t for t in threading.enumerate() if t.ident not in threads and not t.daemon]


@pytest.mark.parametrize('headers', [
    b'Content-Length: 9999999\r\n', b'Content-Length: -1\r\n',
    b'Content-Length: 1\r\nContent-Length: 2\r\n',
    b'Transfer-Encoding: chunked\r\nContent-Length: 1\r\n',
    b'Transfer-Encoding: gzip\r\n', b'Content-Encoding: gzip\r\n',
    b'Content-Length: 999\r\n',
])
def test_ambiguous_oversized_or_truncated_response_is_unknown(headers):
    with metrics_server(reply(METRICS, headers)) as port:
        assert runtime_policy.request_activity(port) is None


def test_redirects_are_not_followed():
    hits = []
    with metrics_server(lambda *_: hits.append(True)) as destination:
        headers = f'Location: http://127.0.0.1:{destination}/metrics\r\nContent-Length: 0\r\n'.encode()
        with metrics_server(reply(b'', headers, b'302 Found')) as port:
            assert runtime_policy.request_activity(port) is None
    assert not hits


@pytest.mark.parametrize('body', [
    METRICS.replace(b'processing 0', b'processing 0.5'),
    METRICS + b'llamacpp:requests_processing 0\n',
    METRICS.replace(b'deferred 0', b'deferred NaN'),
    METRICS.replace(b'deferred 0', b'deferred -1'),
])
def test_invalid_metrics_cannot_prove_idle(body):
    with metrics_server(reply(body)) as port:
        assert runtime_policy.request_activity(port) is None


@pytest.mark.parametrize('url', ['http://localhost/metrics', 'https://127.0.0.1/metrics',
    'http://192.0.2.1/metrics', 'http://user:secret@127.0.0.1/metrics',
    'http://127.0.0.1:0/metrics', 'http://127.0.0.1:65536/metrics',
    'http://127.0.0.1/metrics\r\nX: secret'])
def test_non_literal_local_targets_and_injection_are_refused(url):
    with pytest.raises(ValueError):
        request_bytes(url, timeout=.2, maximum_bytes=1024)


def test_inference_uses_bounded_json_post_and_checks_response_shape():
    posted = []
    body = json.dumps({'choices': [{'message': {'content': 'ok'}}]}).encode()
    with metrics_server(reply(body), path='/v1/chat/completions', posted=posted) as port:
        result = server.minimal_inference_probe({'server_port': port}, timeout=1)
    assert result == {'ok': True, 'sample': 'ok'}
    assert json.loads(posted[0])['max_tokens'] == 1
    with metrics_server(reply(b'[]'), path='/v1/chat/completions') as port:
        with pytest.raises(RuntimeError, match='no completion'):
            server.minimal_inference_probe({'server_port': port}, timeout=1)


@pytest.mark.parametrize('probe', ['health', 'inference', 'gateway'])
def test_actual_probe_paths_enforce_body_size(monkeypatch, probe):
    monkeypatch.setattr(server, 'MAX_PROBE_RESPONSE_BYTES', 128)
    monkeypatch.setattr(gateway_runtime, 'BACKEND_PROBE_MAX_BYTES', 128)
    path = '/v1/chat/completions' if probe == 'inference' else '/health'
    with metrics_server(reply(b'x' * 129), path=path) as port:
        with pytest.raises(LoopbackHTTPError, match='byte limit'):
            if probe == 'health':
                server._json_get(f'http://127.0.0.1:{port}/health', timeout=1)
            elif probe == 'gateway':
                gateway_runtime._bounded_json(f'http://127.0.0.1:{port}/health', timeout=1)
            else:
                server.minimal_inference_probe({'server_port': port}, timeout=1)


def test_no_length_body_is_still_capped():
    with metrics_server(reply(b'x' * 129, b'Connection: close\r\n')) as port:
        with pytest.raises(LoopbackHTTPError, match='byte limit'):
            request_bytes(f'http://127.0.0.1:{port}/metrics', timeout=1, maximum_bytes=128)
