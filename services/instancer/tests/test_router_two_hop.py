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
    """Client -> Central Router (Hop 1) -> Host Router (Hop 2 using real handle_client) -> Instance Backend."""
    from services.instancer.router.main import handle_client

    # 1. Start Mock Instance Backend (returns HTTP 200)
    async def backend_cb(reader, writer):
        try:
            req = await reader.read(4096)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 18\r\nConnection: close\r\n\r\nHello from backend")
            await writer.drain()
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except Exception:
                pass

    backend_server = await asyncio.start_server(backend_cb, "127.0.0.1", 0)
    backend_port = backend_server.sockets[0].getsockname()[1]

    inst_id = "c" * 32

    # 2. Start Hop 2 Router (Host Router) using REAL handle_client server code
    hop2_router = InstanceRouter(
        instance_domain="inst.l3m0nctf.xyz",
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
        trusted_forwarders=["127.0.0.1"],
    )
    hop2_router.update_routes([
        {
            "id": inst_id,
            "protocol": "http",
            "target_ip": "127.0.0.1",
            "target_port": backend_port,
        }
    ])

    hop2_server = await asyncio.start_server(lambda r, w: handle_client(r, w, hop2_router), "127.0.0.1", 0)
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
        try:
            await asyncio.wait_for(hop2_server.wait_closed(), timeout=2.0)
        except Exception:
            pass
        try:
            await asyncio.wait_for(backend_server.wait_closed(), timeout=2.0)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_hop2_trusted_forwarder_preserves_per_player_limits(monkeypatch):
    """BLOCKING 8 & Point 5: Two real routers end-to-end.

    Verifies:
    1. 60 distinct players routed through Hop 1 are NOT refused by Hop 2's per-IP limit (50).
    2. Player-supplied spoofed X-L3mon-Client-IP is replaced by Hop 1 with real client IP.
    3. Hop 2 strips X-L3mon-Client-IP so the backend instance NEVER receives it (N-3 proof).
    """
    from services.instancer.router.main import handle_client
    import services.instancer.router.router as router_mod

    # Make 127.0.0.1 trusted for Cloudflare CF-Connecting-IP simulation on Hop 1
    monkeypatch.setattr(router_mod, "is_cloudflare_ip", lambda ip: True)

    backend_received_raw = []
    barrier = asyncio.Barrier(60)

    async def backend_cb(reader, writer):
        try:
            req = await reader.read(4096)
            backend_received_raw.append(req)
            # Hold connections until all 60 are concurrently active
            try:
                await asyncio.wait_for(barrier.wait(), timeout=5.0)
            except Exception:
                pass
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            await writer.drain()
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except Exception:
                pass

    backend_server = await asyncio.start_server(backend_cb, "127.0.0.1", 0)
    backend_port = backend_server.sockets[0].getsockname()[1]
    inst_id = "d" * 32

    # Hop 2 with max_connections_per_ip = 50 and 127.0.0.1 configured as trusted forwarder
    hop2_router = InstanceRouter(
        instance_domain="inst.l3m0nctf.xyz",
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
        max_connections_per_instance=100,
        max_connections_per_ip=50,
        trusted_forwarders=["127.0.0.1"],
    )
    hop2_router.update_routes([
        {"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": backend_port}
    ])

    hop2_server = await asyncio.start_server(lambda r, w: handle_client(r, w, hop2_router), "127.0.0.1", 0)
    hop2_port = hop2_server.sockets[0].getsockname()[1]

    # Hop 1 Central Router
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
        max_connections_per_instance=100,
    )
    hop1_router.update_host_routes(
        "h1",
        f"http://127.0.0.1:{hop2_port}",
        [{"id": inst_id, "protocol": "http"}],
        now_ts=1000.0,
    )
    hop1_server = await asyncio.start_server(lambda r, w: handle_client(r, w, hop1_router), "127.0.0.1", 0)
    hop1_port = hop1_server.sockets[0].getsockname()[1]

    async def send_player_request(idx: int) -> tuple[int, bytes]:
        player_ip = f"198.51.100.{idx + 1}"
        r, w = await asyncio.open_connection("127.0.0.1", hop1_port)
        req_lines = [
            f"GET /player-{idx} HTTP/1.1",
            f"Host: {inst_id}.inst.l3m0nctf.xyz",
            f"CF-Connecting-IP: {player_ip}",
            "X-L3mon-Client-IP: 1.2.3.4",  # Spoofed header from player!
            "Connection: close",
            "",
            "",
        ]
        w.write("\r\n".join(req_lines).encode("utf-8"))
        await w.drain()
        resp = await r.read(4096)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        status = 0
        if resp.startswith(b"HTTP/1.1 "):
            status = int(resp.split(b" ")[1])
        return status, resp

    try:
        # Launch 60 concurrent player requests across 2 real routers
        tasks = [asyncio.create_task(send_player_request(i)) for i in range(60)]
        results = await asyncio.gather(*tasks)

        # 1. Assert all 60 distinct players were accepted (200 OK), not refused by Hop 2 limit (50)
        assert len(results) == 60
        for i, (status, resp) in enumerate(results):
            assert status == 200, f"Player {i+1} got unexpected status {status}: {resp!r}"

        # 2. Assert Hop 2 stripped X-L3mon-Client-IP before delivering to backend (N-3 proof)
        assert len(backend_received_raw) == 60
        for raw_req in backend_received_raw:
            req_lower = raw_req.lower()
            assert b"x-l3mon-client-ip" not in req_lower, "Hop 2 must strip X-L3mon-Client-IP before backend delivery"
            assert b"1.2.3.4" not in raw_req, "Player spoofed IP must never reach backend"
    finally:
        hop1_server.close()
        hop2_server.close()
        backend_server.close()
        try:
            await asyncio.wait_for(hop1_server.wait_closed(), timeout=2.0)
        except Exception:
            pass
        try:
            await asyncio.wait_for(hop2_server.wait_closed(), timeout=2.0)
        except Exception:
            pass
        try:
            await asyncio.wait_for(backend_server.wait_closed(), timeout=2.0)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_hop2_refuses_when_hop1_omits_client_ip():
    """Point 5 (N-2 breakage test): If Hop 1 omits X-L3mon-Client-IP, Hop 2 attributes all

    connections to Hop 1 (127.0.0.1) and refuses connections exceeding max_connections_per_ip (50).
    """
    from services.instancer.router.main import handle_client

    backend_received = 0
    barrier = asyncio.Barrier(60)

    async def backend_cb(reader, writer):
        nonlocal backend_received
        try:
            req = await reader.read(4096)
            backend_received += 1
            try:
                await asyncio.wait_for(barrier.wait(), timeout=3.0)
            except Exception:
                pass
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            await writer.drain()
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except Exception:
                pass

    backend_server = await asyncio.start_server(backend_cb, "127.0.0.1", 0)
    backend_port = backend_server.sockets[0].getsockname()[1]
    inst_id = "f" * 32

    # Hop 2 with max_connections_per_ip = 50 and 127.0.0.1 configured as trusted forwarder
    hop2_router = InstanceRouter(
        instance_domain="inst.l3m0nctf.xyz",
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
        max_connections_per_instance=100,
        max_connections_per_ip=50,
        trusted_forwarders=["127.0.0.1"],
    )
    hop2_router.update_routes([
        {"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": backend_port}
    ])

    hop2_server = await asyncio.start_server(lambda r, w: handle_client(r, w, hop2_router), "127.0.0.1", 0)
    hop2_port = hop2_server.sockets[0].getsockname()[1]

    async def send_omitted_header_request(idx: int) -> int:
        r, w = await asyncio.open_connection("127.0.0.1", hop2_port)
        # Hop 1 sends NO X-L3mon-Client-IP header (simulating N-2 breakage)
        req_lines = [
            f"GET /broken-{idx} HTTP/1.1",
            f"Host: {inst_id}.inst.l3m0nctf.xyz",
            "Connection: close",
            "",
            "",
        ]
        w.write("\r\n".join(req_lines).encode("utf-8"))
        await w.drain()
        resp = await r.read(4096)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        status = 0
        if resp.startswith(b"HTTP/1.1 "):
            status = int(resp.split(b" ")[1])
        return status

    try:
        tasks = [asyncio.create_task(send_omitted_header_request(i)) for i in range(60)]
        results = await asyncio.gather(*tasks)

        # Because Hop 1 omitted X-L3mon-Client-IP, all 60 are counted against 127.0.0.1.
        # Connections exceeding 50 MUST be refused with 503!
        refused_503 = [s for s in results if s == 503]
        success_200 = [s for s in results if s == 200]
        assert len(refused_503) > 0, f"Expected refused requests, got all {results}"
        assert len(success_200) <= 50, f"Expected at most 50 successes, got {len(success_200)}"
    finally:
        hop2_server.close()
        backend_server.close()
        try:
            await asyncio.wait_for(hop2_server.wait_closed(), timeout=2.0)
        except Exception:
            pass
        try:
            await asyncio.wait_for(backend_server.wait_closed(), timeout=2.0)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_hop2_untrusted_peer_cannot_spoof_client_ip():
    """BLOCKING 8: Untrusted peer sending X-L3mon-Client-IP is ignored and tracked by peer IP."""
    from services.instancer.router.main import handle_client

    async def backend_cb(reader, writer):
        try:
            req = await reader.read(4096)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            await writer.drain()
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except Exception:
                pass

    backend_server = await asyncio.start_server(backend_cb, "127.0.0.1", 0)
    backend_port = backend_server.sockets[0].getsockname()[1]
    inst_id = "e" * 32

    # Hop 2 with 10.0.0.1 as trusted forwarder (so 127.0.0.1 is UNTRUSTED)
    hop2_router = InstanceRouter(
        instance_domain="inst.l3m0nctf.xyz",
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
        max_connections_per_ip=5,
        trusted_forwarders=["10.0.0.1"],
    )
    hop2_router.update_routes([
        {"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": backend_port}
    ])

    hop2_server = await asyncio.start_server(lambda r, w: handle_client(r, w, hop2_router), "127.0.0.1", 0)
    hop2_port = hop2_server.sockets[0].getsockname()[1]

    try:
        # Untrusted client from 127.0.0.1 tries to spoof distinct IPs via X-L3mon-Client-IP
        # Because 127.0.0.1 is not trusted, Hop 2 ignores the header and tracks by 127.0.0.1
        resolved_ips = []
        for i in range(3):
            resolved = hop2_router.resolve_client_ip("127.0.0.1", [("X-L3mon-Client-IP", f"1.1.1.{i+1}")])
            resolved_ips.append(resolved)

        assert all(ip == "127.0.0.1" for ip in resolved_ips), "Untrusted peer header must be ignored"
    finally:
        hop2_server.close()
        backend_server.close()
        try:
            await asyncio.wait_for(hop2_server.wait_closed(), timeout=2.0)
        except Exception:
            pass
        try:
            await asyncio.wait_for(backend_server.wait_closed(), timeout=2.0)
        except Exception:
            pass
