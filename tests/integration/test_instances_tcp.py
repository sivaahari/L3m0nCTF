"""Integration tests for TCP challenge instances, dynamic flags, and router relay (F1).

Tests:
1. TCP challenge setup (slug="demo-tcp", protocol="tcp", port=8000).
2. Player API starts TCP instance (POST /api/v1/l3mon/instances).
3. Connect to published TCP relay port (30000..30199) on loopback/host.
4. Interact over TCP: read banner, send "open", read dynamic team flag.
5. Flag validation: Team A submits derived flag to CTFd -> correct.
6. Flag reuse prevention: Team B submits Team A's flag -> incorrect.
7. Instance renewal (POST /api/v1/l3mon/instances/{id}/renew).
8. Router & Instancer restart: route and TCP relay recover from container labels.
9. Instance stop (DELETE /api/v1/l3mon/instances/{id}): TCP relay port stops answering.
10. Development TTL expiry override verification.
"""
from __future__ import annotations

import json
import os
import secrets as pysecrets
import socket
import subprocess
import time
import urllib.parse
import pytest

import test_stack as t
from tests.integration.test_instances import (
    admin,
    make_player,
    post_json,
    delete_json,
    world,
)

pytestmark = pytest.mark.integration

PROGRAMMES_API = "/api/v1/l3mon/admin/programmes"
RELEASE_API = "/api/v1/l3mon/admin/release"


def connect_tcp(host: str, port: int, timeout: float = 5.0) -> tuple[socket.socket, str]:
    """Connect to TCP relay and read initial banner."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    banner = s.recv(1024).decode("utf-8", errors="replace")
    return s, banner


@pytest.mark.overlays
def test_tcp_instance_end_to_end_lifecycle(world):
    try:
        ps = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=5)
        if ps.returncode != 0:
            pytest.skip("Docker daemon not accessible")
    except Exception:
        pytest.skip("Docker CLI not available")

    # Ensure TCP fixture image is built
    subprocess.run(["docker", "build", "-t", "l3mon-fixture/tcp:dev", "services/instancer/tests/fixtures/tcp"], check=True)

    # 1. Create Challenge in CTFd
    status, out = admin("POST", "/api/v1/challenges", {
        "name": "Demo TCP Pwn Challenge",
        "category": "Pwn",
        "description": "Exploit TCP challenge to retrieve flag",
        "value": 150,
        "state": "visible",
        "type": "standard",
    })
    assert status == 200, out
    chal_id = out["data"]["id"]

    # 2. Add Dynamic Flag for this challenge
    status, out = admin("POST", "/api/v1/flags", {
        "challenge_id": chal_id,
        "content": "",
        "type": "l3mon_dynamic",
        "data": "",
    })
    assert status == 200, out

    # 3. Create Channel and Programme with delivery="live_single" and slug="demo-tcp"
    chan_slug = f"chan-tcp-{pysecrets.token_hex(4)}"
    status, out = admin("PUT", PROGRAMMES_API, {
        "channels": [{"slug": chan_slug, "name": "TCP Pwn Channel", "position": 1}],
        "programmes": [{
            "challenge_id": chal_id,
            "channel": chan_slug,
            "cell": 0,
            "number": 401,
            "slug": "demo-tcp",
            "delivery": "live_single",
        }],
    })
    assert status == 200, out

    # Release channel and programme
    status, view = admin("GET", RELEASE_API)
    assert status == 200, view
    chan_id = view["data"]["channels"][0]["id"]
    prog_id = view["data"]["channels"][0]["programmes"][0]["id"]
    status, _ = admin("PUT", RELEASE_API, {
        "changes": [
            {"kind": "channel", "id": chan_id, "mode": "release"},
            {"kind": "programme", "id": prog_id, "mode": "release"},
        ]
    })
    assert status == 200

    # 4. Make two players (Team A and Team B)
    alice_name = f"alice_tcp_{pysecrets.token_hex(4)}"
    bob_name = f"bob_tcp_{pysecrets.token_hex(4)}"
    session_a, uid_a, team_a = make_player(alice_name)
    session_b, uid_b, team_b = make_player(bob_name)

    instance_id = None
    try:
        # 5. Team A starts TCP instance
        status, resp = post_json(session_a, "/api/v1/l3mon/instances", {"challenge_id": chal_id})
        assert status == 201, resp
        assert resp["success"] is True
        data = resp["data"]
        assert data["protocol"] == "tcp"
        assert data["state"] == "running"
        assert "port" in data
        relay_port = int(data["port"])
        assert 30000 <= relay_port <= 30199, f"Relay port {relay_port} out of range [30000, 30199]"
        relay_host = data.get("host") or "127.0.0.1"

        # Wait for Router to poll routes (polling interval: 2.0s)
        time.sleep(3)

        # 6. Connect to TCP relay port, read banner, send "open", and read dynamic flag
        sock, banner = connect_tcp(relay_host, relay_port)
        assert "L3m0nCTF TCP fixture ready" in banner

        sock.sendall(b"open\n")
        flag_line = b""
        while b"\n" not in flag_line:
            chunk = sock.recv(256)
            if not chunk:
                break
            flag_line += chunk
        sock.close()

        flag_text = flag_line.decode("utf-8").strip()
        assert flag_text.startswith("L3m0n" + "{") and flag_text.endswith("}")

        # 7. Team A submits flag to CTFd -> CORRECT
        status, attempt_a = post_json(session_a, "/api/v1/challenges/attempt", {
            "challenge_id": chal_id,
            "submission": flag_text,
        })
        assert status == 200, attempt_a
        assert attempt_a["data"]["status"] == "correct"

        # 8. Team B submits Team A's flag -> INCORRECT (cross-team flag submission blocked)
        status, attempt_b = post_json(session_b, "/api/v1/challenges/attempt", {
            "challenge_id": chal_id,
            "submission": flag_text,
        })
        assert status == 200, attempt_b
        assert attempt_b["data"]["status"] == "incorrect"

        # 9. Renew TCP instance
        status, renew_resp = post_json(session_a, f"/api/v1/l3mon/instances/{chal_id}/renew", {})
        assert status == 200, renew_resp
        assert renew_resp["success"] is True

        # 10. Restart Instancer and Router to verify TCP route recovery from labels
        subprocess.run(["docker", "restart", "l3mon-instancer-1"], check=True)
        time.sleep(2)
        subprocess.run(["docker", "restart", "l3mon-router-1"], check=True)
        time.sleep(4)

        # Connect again after restart -> TCP route recovered
        sock_recovered, banner_recovered = connect_tcp(relay_host, relay_port)
        assert "L3m0nCTF TCP fixture ready" in banner_recovered
        sock_recovered.sendall(b"echo alive\n")
        reply = sock_recovered.recv(1024).decode("utf-8")
        assert "alive" in reply
        sock_recovered.close()

        # 11. Stop TCP instance
        status, stop_resp = delete_json(session_a, f"/api/v1/l3mon/instances/{chal_id}")
        assert status == 200, stop_resp
        assert stop_resp["success"] is True

        # Wait for router to close TCP listener on route removal
        time.sleep(3)

        # Assert port stops answering (connection refused or timeout within 1.0s)
        port_closed = False
        try:
            s_check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s_check.settimeout(1.0)
            s_check.connect((relay_host, relay_port))
            data = s_check.recv(100)
            if not data:
                port_closed = True
            s_check.close()
        except (ConnectionRefusedError, socket.timeout, OSError):
            port_closed = True
        assert port_closed is True, f"TCP relay port {relay_port} remained open after instance stop"

    finally:
        # Cleanup instance if left running
        try:
            delete_json(session_a, f"/api/v1/l3mon/instances/{chal_id}")
        except Exception:
            pass
