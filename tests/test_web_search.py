"""Opt-in web search transmits only the explicit query and returns inert sources."""
import json

import pytest

from bc250_llm_mode.chat_lifecycle import ChatCancellation, ChatCancelled
from bc250_llm_mode.document_service import message_with_sources
from bc250_llm_mode.web_search import WebSearchService, search_endpoint


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def raise_for_status(self):
        if self.status_code != 200:
            raise ValueError("HTTP error")
    def iter_bytes(self):
        yield self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode()


class Http:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.calls = payload, status, []
    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(self.payload, self.status)


def test_only_explicit_query_is_posted_no_history_files_or_automatic_fetch(tmp_path):
    http = Http({"results": [{"title": "A <b>useful</b> result", "url": "https://example.org/report",
        "content": "Public facts <script>run_bad_code()</script> with context."}]})
    search = WebSearchService(tmp_path, http_client=http)
    assert search.current() == "" and not http.calls
    search.configure("http://127.0.0.1:8888")
    assert not http.calls and search.path.stat().st_mode & 0o777 == 0o600
    result = search.search("explicit public query")
    assert len(http.calls) == 1
    method, url, kwargs = http.calls[0]
    assert method == "POST" and url == "http://127.0.0.1:8888/search"
    assert kwargs["data"] == {"q": "explicit public query", "format": "json", "categories": "general", "safesearch": "1"}
    assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert result[0].name == "A useful result" and "run_bad_code" not in result[0].text
    assert "explicit public query" not in search.path.read_text()
    request = message_with_sources({"role": "user", "content": "Use this source", "sources": [result[0].to_dict()]})
    assert request["role"] == "user" and "https://example.org/report" in request["content"]
    assert "quoted source material" in request["content"]


@pytest.mark.parametrize("endpoint", ["http://remote.example", "https://user:secret@example.org", "file:///etc/passwd",
    "https://example.org/?key=secret", "http://127.0.0.1:70000", "https://example.org/#x", "https://example.org\n"])
def test_invalid_provider_addresses_are_rejected(endpoint):
    with pytest.raises(ValueError):
        search_endpoint(endpoint)


def test_result_limit_deduplication_and_dangerous_link_filter(tmp_path):
    rows = [{"title": "Bad", "url": "javascript:run()", "content": "do this"}]
    rows += [{"title": str(i), "url": f"https://example.org/{i}", "content": "Excerpt"} for i in range(20)]
    rows.insert(2, dict(rows[1]))
    sources = WebSearchService(tmp_path, http_client=Http({"results": rows})).search("q", endpoint="https://search.example.org")
    assert len(sources) == 4 and len({s.url for s in sources}) == 4
    assert all(s.url.startswith("https://example.org") for s in sources)


def test_oversized_or_disabled_json_provider_fails_with_actionable_error(tmp_path):
    for http, match in ((Http(b"x" * (1024 * 1024 + 1)), "size or time"),
                        (Http({}, 403), "Enable search.formats")):
        with pytest.raises(ValueError, match=match):
            WebSearchService(tmp_path, http_client=http).search("q", endpoint="http://localhost:8888")


def test_cancelled_search_sends_nothing(tmp_path):
    cancellation = ChatCancellation()
    cancellation.cancel()
    http = Http({"results": []})
    with pytest.raises(ChatCancelled):
        WebSearchService(tmp_path, http_client=http).search("q", endpoint="https://search.example.org", cancellation=cancellation)
    assert http.calls == []
