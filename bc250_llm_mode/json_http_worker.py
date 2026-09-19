"""Fixed, short-lived HTTP worker; private input/output, no diagnostic output."""
import base64
import json
import logging
import resource
import sys
from urllib.parse import urlsplit


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (12, 13))
    resource.setrlimit(resource.RLIMIT_FSIZE, (17 * 1024 * 1024, 17 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if sys.platform.startswith("linux"):
        # Fedora Python 3.14 can reserve ~238 MiB before importing httpx.
        # Keep a finite ceiling with room for its libraries and bounded reply.
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    logging.disable(logging.CRITICAL)
    try:
        import httpx
        raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            return {}
        request = json.loads(raw)
        required = {"url", "json", "data", "headers", "maximum_bytes", "seconds"}
        optional = {"method", "first_sse_event", "headers_only"}
        if not isinstance(request, dict) or not required <= set(request) or set(request) - required - optional:
            return {}
        method = request.get("method", "POST")
        first_sse = request.get("first_sse_event", False)
        headers_only = request.get("headers_only", False)
        if (method not in {"GET", "POST"} or type(first_sse) is not bool
                or type(headers_only) is not bool or first_sse and headers_only
                or method == "GET" and (request["json"] is not None or request["data"] is not None)):
            return {}
        parts = urlsplit(request["url"])
        maximum, seconds = request["maximum_bytes"], request["seconds"]
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or type(maximum) is not int or not 0 < maximum <= 8 * 1024 * 1024
                or type(seconds) not in {int, float} or not 0 < seconds <= 30):
            return {}
        with httpx.stream(method, request["url"], json=request["json"], data=request["data"],
                headers={**request["headers"], "Accept-Encoding": "identity"},
                timeout=httpx.Timeout(min(seconds, 10), connect=min(seconds, 5)),
                follow_redirects=False, trust_env=False) as response:
            if headers_only:
                return {"status": response.status_code, "body": ""}
            body = bytearray()
            pending = b""
            for chunk in response.iter_raw():
                body.extend(chunk)
                if len(body) > maximum:
                    return {}
                if first_sse:
                    lines = (pending + chunk).splitlines(keepends=True)
                    pending = b""
                    for line in lines:
                        if not line.endswith((b"\n", b"\r")):
                            pending = line
                            continue
                        if len(line) > 4096 or not line.startswith(b"data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except (ValueError, UnicodeError, RecursionError):
                            continue
                        if isinstance(event, dict):
                            return {"status": response.status_code, "body": base64.b64encode(body).decode("ascii")}
            return {"status": response.status_code, "body": base64.b64encode(body).decode("ascii")}
    except Exception:
        return {}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=True))
