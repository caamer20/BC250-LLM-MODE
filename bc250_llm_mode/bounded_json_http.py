"""Small JSON HTTP requests with a whole-process deadline, including DNS/headers.

The fixed child receives private request data through stdin and emits a bounded
reply through a private temporary descriptor. A provider cannot extend the
total deadline by trickling response headers before a stream is available.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile


class BoundedHTTPError(OSError):
    pass


class BufferedResponse:
    def __init__(self, status, content):
        self.status_code, self.content = status, content
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise BoundedHTTPError("HTTP request did not succeed")
    def iter_raw(self):
        for offset in range(0, len(self.content), 65536):
            yield self.content[offset:offset + 65536]
    def close(self):
        pass


class BoundedJSONHTTP:
    def __init__(self, *, maximum_bytes, total_seconds, cancellation=None):
        if not 1 <= maximum_bytes <= 8 * 1024 * 1024 or not 0 < total_seconds <= 15:
            raise ValueError("Unsupported JSON HTTP bounds")
        self.maximum_bytes, self.total_seconds, self.cancellation = maximum_bytes, total_seconds, cancellation

    def stream(self, method, url, *, json=None, data=None, timeout=None, headers=None,
               follow_redirects=False, trust_env=False):
        if method != "POST" or follow_redirects or trust_env:
            raise ValueError("Only bounded direct POST requests are supported")
        import json as json_module
        if self.cancellation:
            self.cancellation.raise_if_cancelled()
        seconds = min(self.total_seconds, float(timeout)) if type(timeout) in {int, float} else self.total_seconds
        request = json_module.dumps({"url": url, "json": json, "data": data,
            "headers": headers or {}, "maximum_bytes": self.maximum_bytes,
            "seconds": seconds}, ensure_ascii=False).encode()
        if len(request) > 16 * 1024 * 1024:
            raise BoundedHTTPError("HTTP request exceeds its input bound")
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("json_http_worker.py"))],
                stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            def stop():
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            if self.cancellation:
                self.cancellation.bind_interrupt(stop)
            try:
                try:
                    process.communicate(request, timeout=seconds)
                except subprocess.TimeoutExpired:
                    stop()
                    process.communicate()
                    raise BoundedHTTPError("HTTP request exceeded its total deadline") from None
            finally:
                if self.cancellation:
                    self.cancellation.bind_interrupt(None)
            if self.cancellation:
                self.cancellation.raise_if_cancelled()
            if process.returncode != 0:
                raise BoundedHTTPError("HTTP request could not complete within its bounds")
            output.seek(0)
            raw = output.read(self.maximum_bytes * 2 + 4097)
        if len(raw) > self.maximum_bytes * 2 + 4096:
            raise BoundedHTTPError("HTTP reply exceeds its bound")
        try:
            result = json_module.loads(raw)
            if (not isinstance(result, dict) or set(result) != {"status", "body"}
                    or type(result["status"]) is not int or not 100 <= result["status"] <= 599
                    or not isinstance(result["body"], str)):
                raise ValueError()
            content = base64.b64decode(result["body"], validate=True)
            if len(content) > self.maximum_bytes:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise BoundedHTTPError("HTTP worker returned an invalid reply") from None
        return BufferedResponse(result["status"], content)
