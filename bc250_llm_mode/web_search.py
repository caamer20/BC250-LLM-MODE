"""User-directed SearXNG search; no automatic queries or page fetching.

Only the edited query is submitted, in a POST body. Search result excerpts are
inert sources reviewed before being attached to a local model request.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from .document_service import DocumentSource
from .fsops import atomic_write_text, ensure_private_dir
from .http_deadline import interrupt_response, response_deadline
from .profile_access import profile_access, require_writable_profile
from .message_catalog import SourceInputError

MAX_QUERY_BYTES = 2048
MAX_SEARCH_RESPONSE_BYTES = 1024 * 1024
MAX_RESULTS = 4
SEARCH_TIMEOUT_SECONDS = 15


def search_endpoint(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise SourceInputError("SEARCH_ENDPOINT_INVALID")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise SourceInputError("SEARCH_ENDPOINT_INVALID") from exc
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or "\\" in value
            or port is not None and not 1 <= port <= 65535):
        raise SourceInputError("SEARCH_ENDPOINT_INVALID")
    if parts.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            loopback = parts.hostname == "localhost"
        if not loopback:
            raise SourceInputError("SEARCH_ENDPOINT_TLS")
    path = parts.path.rstrip("/")
    if not path.endswith("/search"):
        path += "/search"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def source_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise SourceInputError("DOCUMENT_INVALID")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise SourceInputError("DOCUMENT_INVALID")
        _ = parts.port
    except ValueError as exc:
        raise SourceInputError("DOCUMENT_INVALID") from exc
    return value


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value, limit):
    if not isinstance(value, str):
        return ""
    parser = _PlainText()
    parser.feed(value[:16384])
    return " ".join("".join(parser.parts).split())[:limit]


class WebSearchService:
    def __init__(self, profile: Path, *, http_client=None, clock=time.monotonic):
        self.profile = profile
        self.path = profile / "chat-preferences" / "web-search.json"
        self.http, self.clock = http_client, clock

    def current(self):
        with profile_access(self.profile):
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return ""
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise SourceInputError("SEARCH_CONFIG_INVALID")
                raw = source.read(4097)
            if len(raw) > 4096:
                raise SourceInputError("SEARCH_CONFIG_INVALID")
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"endpoint"}:
                raise SourceInputError("SEARCH_CONFIG_INVALID")
            return search_endpoint(value["endpoint"]) if value["endpoint"] else ""

    def configure(self, endpoint):
        checked = search_endpoint(endpoint) if endpoint else ""
        with profile_access(self.profile):
            require_writable_profile(self.profile)
            ensure_private_dir(self.path.parent)
            atomic_write_text(self.path, json.dumps({"endpoint": checked}), mode=0o600)
        return checked

    def search(self, query, *, endpoint=None, cancellation=None):
        if not isinstance(query, str) or not query.strip() or len(query.encode()) > MAX_QUERY_BYTES or any(ord(c) < 32 for c in query):
            raise SourceInputError("SEARCH_QUERY_INVALID")
        endpoint = search_endpoint(endpoint or self.current())
        end = self.clock() + SEARCH_TIMEOUT_SECONDS
        from .bounded_json_http import BoundedJSONHTTP
        http = self.http or BoundedJSONHTTP(maximum_bytes=MAX_SEARCH_RESPONSE_BYTES,
            total_seconds=SEARCH_TIMEOUT_SECONDS, cancellation=cancellation)
        if cancellation:
            cancellation.raise_if_cancelled()
        try:
            with http.stream("POST", endpoint, data={"q": query.strip(), "format": "json", "categories": "general", "safesearch": "1"},
                    timeout=httpx.Timeout(10, connect=5), follow_redirects=False, trust_env=False,
                    headers={"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "BC250-LLM-MODE-Web-Search"}) as response:
                with response_deadline(response, max(0.001, end - self.clock())):
                    if cancellation:
                        cancellation.bind_interrupt(lambda: interrupt_response(response))
                    try:
                        if response.status_code == 403:
                            raise SourceInputError("SEARCH_JSON_DISABLED")
                        response.raise_for_status()
                        raw = bytearray()
                        for chunk in (response.iter_raw() if hasattr(response, "iter_raw") else response.iter_bytes()):
                            if cancellation:
                                cancellation.raise_if_cancelled()
                            raw.extend(chunk)
                            if len(raw) > MAX_SEARCH_RESPONSE_BYTES or self.clock() >= end:
                                raise SourceInputError("SEARCH_RESPONSE_LIMIT")
                    finally:
                        if cancellation:
                            cancellation.bind_interrupt(None)
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise SourceInputError("SEARCH_FAILED")
            sources, seen = [], set()
            for row in data["results"][:100]:
                if not isinstance(row, dict):
                    continue
                try:
                    url = source_url(row.get("url"))
                except ValueError:
                    continue
                if url in seen:
                    continue
                title = _plain(row.get("title"), 120)
                title = "".join(c for c in title if c.isprintable() and c not in "/\\") or urlsplit(url).hostname
                snippet = _plain(row.get("content"), 4000)
                if not snippet:
                    continue
                text = "Web search excerpt (not the full page):\n" + snippet
                identity = hashlib.sha256((url + "\n" + text).encode()).hexdigest()
                sources.append(DocumentSource(title, "web", text, identity, len(text.encode()), url=url))
                seen.add(url)
                if len(sources) >= MAX_RESULTS:
                    break
            return tuple(sources)
        except (httpx.HTTPError, OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            raise SourceInputError("SEARCH_FAILED") from None
