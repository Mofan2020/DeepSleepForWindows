"""Tests for the daemon/agent conversation.

The daemon listens on a loopback socket and the agent reports the idle
time over it.  These tests start a real server and talk to it with a
real socket, so the wire format is actually exercised.
"""

from __future__ import annotations

import json
import socket
import time

import pytest

from winsleep.core.daemon import StateServer


@pytest.fixture
def server():
    instance = StateServer()
    port = instance.start()
    yield instance, port
    instance.stop()


def send(port: int, message: dict) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as conn:
        conn.sendall(json.dumps(message).encode("utf-8") + b"\n")
        conn.settimeout(5.0)
        reply = conn.recv(65536)
    return json.loads(reply.decode("utf-8").splitlines()[0])


class TestStateServer:
    def test_ping_is_answered(self, server):
        _, port = server
        assert send(port, {"kind": "ping"})["kind"] == "pong"

    def test_a_report_is_accepted(self, server):
        instance, port = server
        assert send(port, {"kind": "report", "idle_seconds": 42.5})["ok"] is True

    def test_a_report_updates_the_idle_time(self, server):
        instance, port = server
        send(port, {"kind": "report", "idle_seconds": 42.5})
        assert instance.agent.idle_seconds == 42.5

    def test_a_report_marks_the_state_as_fresh(self, server):
        instance, port = server
        send(port, {"kind": "report", "idle_seconds": 1.0})
        assert instance.agent.is_fresh() is True

    def test_the_state_starts_out_stale(self, server):
        instance, _ = server
        assert instance.agent.is_fresh() is False

    def test_an_unknown_message_is_rejected(self, server):
        _, port = server
        reply = send(port, {"kind": "nonsense"})
        assert reply["ok"] is False

    def test_bad_json_is_rejected_without_killing_the_server(self, server):
        _, port = server
        with socket.create_connection(("127.0.0.1", port), timeout=5.0) as conn:
            conn.sendall(b"{not json\n")
            conn.settimeout(5.0)
            reply = conn.recv(4096)
        assert b"false" in reply
        # The server is still alive and answering.
        assert send(port, {"kind": "ping"})["kind"] == "pong"

    def test_a_report_without_a_number_is_tolerated(self, server):
        instance, port = server
        reply = send(port, {"kind": "report", "idle_seconds": "abc"})
        assert reply["ok"] is True
        assert instance.agent.idle_seconds == -1.0

    def test_the_callback_is_called(self, server):
        instance, port = server
        seen = []
        instance.on_agent_update = seen.append
        send(port, {"kind": "report", "idle_seconds": 7.0})
        # The callback runs on the server thread; give it a moment.
        for _ in range(50):
            if seen:
                break
            time.sleep(0.02)
        assert seen and seen[0].idle_seconds == 7.0

    def test_the_port_is_a_real_port(self, server):
        _, port = server
        assert port > 0
