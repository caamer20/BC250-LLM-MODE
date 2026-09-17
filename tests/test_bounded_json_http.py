"""Real local HTTP verifies whole-request bounds before headers and after body."""
import http.server
import json
import threading
import time

import pytest

from bc250_llm_mode.bounded_json_http import BoundedJSONHTTP, BoundedHTTPError
from bc250_llm_mode.chat_context import ChatContextService
from bc250_llm_mode.chat_lifecycle import ChatCancellation, ChatCancelled
from bc250_llm_mode.web_search import WebSearchService


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def do_POST(self):
        body = self.rfile.read(min(1024 * 1024, int(self.headers.get("Content-Length", "0"))))
        if self.path == "/slow":
            self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            self.wfile.flush()
            for _ in range(40):
                try:
                    self.wfile.write(b"a")
                    self.wfile.flush()
                except OSError:
                    break
                time.sleep(0.1)
            return
        if self.path == "/apply-template":
            assert json.loads(body)["messages"][0]["role"] == "user"
            result = {"prompt": "a template with special tokens"}
        elif self.path == "/tokenize":
            result = {"tokens": [1, 2, 3, 4, 5, 6]}
        else:
            assert b"q=local+fixture+query" in body
            result = {"results": [{"title": "Fixture", "url": "https://example.org/source", "content": "Fixture search excerpt"}]}
        raw = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_token_preflight_and_search_use_real_bounded_child_requests(server, tmp_path):
    port = server.server_address[1]
    plan = ChatContextService().prepare({"server_port": port, "current_ctx": 8192}, [{"role": "user", "content": "fixture"}])
    assert not plan.estimated and plan.prompt_tokens == 6
    sources = WebSearchService(tmp_path).search("local fixture query", endpoint=f"http://127.0.0.1:{port}")
    assert len(sources) == 1 and sources[0].url == "https://example.org/source"


def test_trickled_headers_cannot_extend_total_deadline(server):
    http = BoundedJSONHTTP(maximum_bytes=1024, total_seconds=0.8)
    started = time.monotonic()
    with pytest.raises(BoundedHTTPError, match="deadline"):
        http.stream("POST", f"http://127.0.0.1:{server.server_address[1]}/slow", json={})
    assert time.monotonic() - started < 2.0


def test_cancellation_interrupts_before_response_headers(server):
    token = ChatCancellation()
    http = BoundedJSONHTTP(maximum_bytes=1024, total_seconds=5, cancellation=token)
    timer = threading.Timer(0.5, token.cancel)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ChatCancelled):
            http.stream("POST", f"http://127.0.0.1:{server.server_address[1]}/slow", json={})
        assert time.monotonic() - started < 2.0
    finally:
        timer.cancel()
        timer.join(timeout=1)
