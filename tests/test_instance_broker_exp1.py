from __future__ import annotations

import json
import socket

from bc250_llm_mode.instance_broker import ActivationRequest, GuiInstanceBroker, MAX_MESSAGE_BYTES


def test_one_owner_and_route_activation(tmp_path):
    owner = GuiInstanceBroker(tmp_path)
    contender = GuiInstanceBroker(tmp_path)
    assert owner.acquire() is True
    assert contender.acquire() is False
    try:
        # Client waits for the owner poll; send manually so this test remains
        # deterministic without adding a broker thread.
        nonce = contender.nonce_path.read_text().strip()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1)
        client.connect(str(contender.socket_path))
        client.sendall(json.dumps({
            "nonce": nonce, "verb": "ROUTE", "route": "models",
            "identifier": None,
        }).encode())
        client.shutdown(socket.SHUT_WR)
        requests = owner.poll()
        assert requests == [ActivationRequest("ROUTE", "models")]
        assert client.recv(16) == b"OK"
        client.close()
    finally:
        owner.close()


def test_wrong_nonce_oversize_and_unknown_route_are_refused(tmp_path):
    owner = GuiInstanceBroker(tmp_path)
    assert owner.acquire()
    try:
        for payload in (
            {"nonce": "wrong", "verb": "ACTIVATE"},
            {"nonce": owner._nonce, "verb": "ROUTE", "route": "shell"},
        ):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(owner.socket_path))
            client.sendall(json.dumps(payload).encode())
            client.shutdown(socket.SHUT_WR)
            assert owner.poll() == []
            client.close()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(owner.socket_path))
        client.sendall(b"x" * (MAX_MESSAGE_BYTES + 1))
        client.shutdown(socket.SHUT_WR)
        assert owner.poll() == []
        client.close()
    finally:
        owner.close()


def test_abrupt_owner_cleanup_requires_lock_ownership(tmp_path):
    owner = GuiInstanceBroker(tmp_path)
    assert owner.acquire()
    socket_path = owner.socket_path
    owner.close()
    socket_path.write_text("stale")
    replacement = GuiInstanceBroker(tmp_path)
    assert replacement.acquire()
    try:
        assert replacement.socket_path.is_socket()
    finally:
        replacement.close()
