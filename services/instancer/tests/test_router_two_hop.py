"""Unit tests for Central Router two-hop HTTP forwarding and destination integrity (SP4 / F3).

Verifies:
1. Hop 1 destination integrity: Central router forwards exclusively to configured router_url,
   ignoring any foreign, private VPC, or metadata address supplied by the host agent.
2. Route ID validation: IDs not matching ^[0-9a-f]{32}$ are rejected.
3. Duplicate ID conflict resolution: The instance recorded for the earlier host wins.
4. Dead host 30-second expiration: Routes belonging to a host silent for >= 30s are purged.
5. Per-host read token isolation (ROUTER_TOKEN_<NAME> vs ROUTER_TOKEN).
6. Full two-hop HTTP forwarding flow end-to-end.
"""
from __future__ import annotations

import asyncio
import pytest

from services.instancer.router.router import (
    InstanceRouter,
    get_router_token_for_host,
)


def test_hop1_destination_integrity_ignores_agent_target():
    """Hop 1 takes destination strictly from router_url, never from agent's target_ip."""
    hosts = [
        {
            "name": "h1",
            "url": "http://10.200.1.10:8300",
            "router_url": "http://10.200.1.10:8090",
            "host": "h1.inst.l3m0nctf.xyz",
        }
    ]
    router = InstanceRouter(hosts=hosts, instance_domain="inst.l3m0nctf.xyz")
    assert router.is_central is True

    valid_id = "a" * 32
    poisoned_routes = [
        {
            "id": valid_id,
            "protocol": "http",
            "target_ip": "169.254.169.254",  # Malicious metadata address from agent
            "target_port": 80,
        }
    ]

    router.update_host_routes("h1", "http://10.200.1.10:8090", poisoned_routes, now_ts=1000.0)

    # Route must be recorded with hop 1 destination, NOT the metadata address!
    assert valid_id in router._routes
    route = router._routes[valid_id]
    assert route["target_ip"] == "10.200.1.10"
    assert route["target_port"] == 8090
    assert route["host_name"] == "h1"


def test_router_rejects_malformed_instance_id():
    """Central router strictly rejects any route ID that is not 32 lowercase hex characters."""
    router = InstanceRouter(
        hosts=[{"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090"}],
        instance_domain="inst.l3m0nctf.xyz",
    )

    malformed_routes = [
        {"id": "A" * 32},             # Uppercase hex
        {"id": "1234567890abcdef"},    # 16 chars (too short)
        {"id": "a" * 31},             # 31 chars
        {"id": "a" * 33},             # 33 chars
        {"id": "../../../../etc/passwd"},  # Path traversal attempt
        {"id": "1' OR '1'='1"},       # Injection attempt
        {"id": ""},                   # Empty string
        {"id": None},                 # None
    ]

    router.update_host_routes("h1", "http://10.0.1.10:8090", malformed_routes, now_ts=1000.0)
    assert len(router._routes) == 0, f"Expected 0 routes, got {router._routes}"


def test_router_duplicate_id_first_registered_wins():
    """When two hosts report the same instance ID, the first registered host wins."""
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090"},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090"},
    ]
    router = InstanceRouter(hosts=hosts, instance_domain="inst.l3m0nctf.xyz")

    common_id = "f" * 32

    # Host 1 registers common_id
    router.update_host_routes("h1", "http://10.0.1.10:8090", [{"id": common_id, "protocol": "http"}], now_ts=1000.0)
    assert router._routes[common_id]["host_name"] == "h1"
    assert router._routes[common_id]["target_ip"] == "10.0.1.10"

    # Host 2 attempts to register common_id
    router.update_host_routes("h2", "http://10.0.1.11:8090", [{"id": common_id, "protocol": "http"}], now_ts=1005.0)

    # First registered host (h1) must retain ownership
    assert router._routes[common_id]["host_name"] == "h1"
    assert router._routes[common_id]["target_ip"] == "10.0.1.10"


def test_dead_host_routes_expire_within_30s():
    """Routes belonging to a host silent for >= 30 seconds are automatically purged."""
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090"},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090"},
    ]
    router = InstanceRouter(hosts=hosts, instance_domain="inst.l3m0nctf.xyz")

    id1 = "1" * 32
    id2 = "2" * 32

    # Both hosts report active instances at t=1000.0
    router.update_host_routes("h1", "http://10.0.1.10:8090", [{"id": id1}], now_ts=1000.0)
    router.update_host_routes("h2", "http://10.0.1.11:8090", [{"id": id2}], now_ts=1000.0)
    assert len(router._routes) == 2

    # At t=1010.0 (10s later), both are still active
    router.purge_expired_host_routes(now_ts=1010.0)
    assert len(router._routes) == 2

    # At t=1015.0, only h2 polls successfully
    router.update_host_routes("h2", "http://10.0.1.11:8090", [{"id": id2}], now_ts=1015.0)

    # At t=1031.0 (31s since h1's last poll), h1's routes must expire
    router.purge_expired_host_routes(now_ts=1031.0)
    assert id1 not in router._routes
    assert id2 in router._routes
    assert len(router._routes) == 1


def test_per_host_router_token_isolation(monkeypatch):
    monkeypatch.setenv("ROUTER_TOKEN", "global_router_token")
    monkeypatch.setenv("ROUTER_TOKEN_H1", "secret_h1_router_token")

    # Host h1 gets dedicated router token
    assert get_router_token_for_host("h1") == "secret_h1_router_token"

    # Host h2 falls back to global router token
    assert get_router_token_for_host("h2") == "global_router_token"


@pytest.mark.asyncio
async def test_two_hop_end_to_end_forwarding():
    """Client -> Central Router (Hop 1) -> Host Router (Hop 2) -> Instance Backend."""
    # 1. Start Mock Instance Backend (returns HTTP 200)
    async def backend_cb(reader, writer):
        req = await reader.read(4096)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 18\r\nConnection: close\r\n\r\nHello from backend")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    backend_server = await asyncio.start_server(backend_cb, "127.0.0.1", 0)
    backend_port = backend_server.sockets[0].getsockname()[1]

    inst_id = "c" * 32

    # 2. Start Hop 2 Router (Host Router)
    hop2_router = InstanceRouter(
        instance_domain="inst.l3m0nctf.xyz",
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    hop2_router.update_routes([
        {
            "id": inst_id,
            "protocol": "http",
            "target_ip": "127.0.0.1",
            "target_port": backend_port,
        }
    ])

    async def hop2_cb(reader, writer):
        line = await reader.readline()
        headers = []
        while True:
            hline = await reader.readline()
            if not hline or hline == b"\r\n":
                break
            parts = hline.decode("utf-8").split(":", 1)
            if len(parts) == 2:
                headers.append((parts[0].strip(), parts[1].strip()))
        method, path, _ = line.decode("utf-8").split()
        await hop2_router.forward_stream(method, path, headers, b"", writer)

    hop2_server = await asyncio.start_server(hop2_cb, "127.0.0.1", 0)
    hop2_port = hop2_server.sockets[0].getsockname()[1]

    # 3. Start Hop 1 Router (Central Router)
    hop1_router = InstanceRouter(
        hosts=[
            {
                "name": "h1",
                "url": "http://127.0.0.1:8300",
                "router_url": f"http://127.0.0.1:{hop2_port}",
                "host": "h1.inst.l3m0nctf.xyz",
            }
        ],
        instance_domain="inst.l3m0nctf.xyz",
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    hop1_router.update_host_routes(
        "h1",
        f"http://127.0.0.1:{hop2_port}",
        [{"id": inst_id, "protocol": "http", "target_ip": "ignored.private.vpc"}],
        now_ts=1000.0,
    )

    try:
        # Client makes request through Hop 1
        status, resp_headers, body = await hop1_router.handle_http_request(
            method="GET",
            path="/test",
            headers={"Host": f"{inst_id}.inst.l3m0nctf.xyz"},
            body=b"",
        )

        assert status == 200
        assert body == b"Hello from backend"
    finally:
        hop2_server.close()
        backend_server.close()
        await hop2_server.wait_closed()
        await backend_server.wait_closed()
