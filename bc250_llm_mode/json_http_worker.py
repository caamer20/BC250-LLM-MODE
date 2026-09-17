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
        if not isinstance(request, dict) or set(request) != {"url", "json", "data", "headers", "maximum_bytes", "seconds"}:
            return {}
        parts = urlsplit(request["url"])
        maximum, seconds = request["maximum_bytes"], request["seconds"]
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or type(maximum) is not int or not 0 < maximum <= 8 * 1024 * 1024
                or type(seconds) not in {int, float} or not 0 < seconds <= 15):
            return {}
        with httpx.stream("POST", request["url"], json=request["json"], data=request["data"],
                headers={**request["headers"], "Accept-Encoding": "identity"},
                timeout=httpx.Timeout(min(seconds, 10), connect=min(seconds, 5)),
                follow_redirects=False, trust_env=False) as response:
            body = bytearray()
            for chunk in response.iter_raw():
                body.extend(chunk)
                if len(body) > maximum:
                    return {}
            return {"status": response.status_code, "body": base64.b64encode(body).decode("ascii")}
    except Exception:
        return {}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=True))
