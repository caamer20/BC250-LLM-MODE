"""Fixed guest supervisor for build commands launched through Podman.

Killing a Podman client does not necessarily stop its guest process. This
supervisor requires bounded stdin heartbeats from RuntimeProcessRunner and
owns a separate child process group, including compiler descendants.
"""
import hashlib


PROCESS_HELPER_SOURCE = '''#!/usr/bin/env python3
import os, select, signal, subprocess, sys, time


def stop_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        proc.poll()
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def main():
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        return 125
    timeout = float(sys.argv[1])
    if not 0 < timeout <= 5400:
        return 125
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.Popen(sys.argv[3:], stdin=subprocess.DEVNULL,
                            start_new_session=True, env=environment)
    started = last_pulse = time.monotonic()
    try:
        while proc.poll() is None:
            now = time.monotonic()
            if now - started >= timeout or now - last_pulse >= 5.0:
                return 124
            readable, _, _ = select.select([sys.stdin.buffer], [], [], 0.1)
            if readable:
                pulse = os.read(sys.stdin.fileno(), 64)
                if not pulse:
                    return 124
                last_pulse = time.monotonic()
        return proc.returncode
    finally:
        stop_group(proc)


if __name__ == "__main__":
    sys.exit(main())
'''

PROCESS_HELPER_DIGEST = hashlib.sha256(PROCESS_HELPER_SOURCE.encode()).hexdigest()
