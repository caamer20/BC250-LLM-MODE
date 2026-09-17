"""Fedora Python's large virtual baseline must leave room for HTTP imports."""
import base64
import json
import subprocess
import sys

import pytest

from bc250_llm_mode import json_http_worker
from test_bounded_json_http import server  # noqa: F401 - real loopback fixture


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux address-space accounting")
def test_http_worker_accepts_large_interpreter_virtual_baseline(server):
    # Reserve virtual address space without committing physical RAM. Fedora's
    # Python 3.14 starts near this footprint before importing the HTTP client.
    # The worker must still enforce its finite ceiling and complete a request.
    program = """
import mmap, runpy, sys
from pathlib import Path
size = next(int(line.split()[1]) * 1024 for line in
            Path('/proc/self/status').read_text().splitlines()
            if line.startswith('VmSize:'))
reserve = mmap.mmap(-1, max(4096, 250 * 1024 * 1024 - size), prot=0)
runpy.run_path(sys.argv[1], run_name='__main__')
"""
    request = {"url": f"http://127.0.0.1:{server.server_address[1]}/tokenize",
               "json": {"content": "fixture"}, "data": None, "headers": {},
               "maximum_bytes": 1024, "seconds": 2}
    result = subprocess.run([sys.executable, "-I", "-c", program, json_http_worker.__file__],
                            input=json.dumps(request).encode(), capture_output=True, timeout=5)
    assert result.returncode == 0
    reply = json.loads(result.stdout)
    assert reply.get("status") == 200
    assert json.loads(base64.b64decode(reply["body"])) == {"tokens": [1, 2, 3, 4, 5, 6]}
