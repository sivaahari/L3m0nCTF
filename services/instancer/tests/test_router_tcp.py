import asyncio
import ipaddress
import logging
import os
import socket
import time
import pytest

from services.instancer.manager import InstanceManager
from services.instancer.router.router import (
    InstanceRouter,
    TokenBucketRateLimiter,
    validate_route_target,
)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Break-Matrix Rows 1-3: Port range validation
# ---------------------------------------------------------------------------

def test_tcp_port_range_validation():
    # Min bound (< 1024)
    with pytest.raises(ValueError, match="INSTANCER_TCP_PORT_START"):
        InstanceManager(
            manifest=None,
            docker_client=None,
            tcp_port_start=80,
            tcp_port_count=100,
        )

    with pytest.raises(ValueError, match="INSTANCER_TCP_PORT_START"):
        InstanceRouter(
            instance_domain="example.com",
            instancer_url="http://127.0.0.1:8300",
            router_token="secret",
            tcp_port_start=80,
            tcp_port_count=100,
        )

    # Max bound (> 65535)
    with pytest.raises(ValueError, match="exceeds 65535"):
        InstanceManager(
            manifest=None,
            docker_client=None,
            tcp_port_start=65000,
            tcp_port_count=1000,
        )

    with pytest.raises(ValueError, match="exceeds 65535"):
        InstanceRouter(
            instance_domain="example.com",
            instancer_url="http://127.0.0.1:8300",
            router_token="secret",
            tcp_port_start=65000,
            tcp_port_count=1000,
        )

    # Count bound (< 1 or > 2000)
    with pytest.raises(ValueError, match="INSTANCER_TCP_PORT_COUNT"):
        InstanceManager(
            manifest=None,
            docker_client=None,
            tcp_port_start=30000,
            tcp_port_count=2500,
        )

    with pytest.raises(ValueError, match="INSTANCER_TCP_PORT_COUNT"):
        InstanceRouter(
            instance_domain="example.com",
            instancer_url="http://127.0.0.1:8300",
            router_token="secret",
            tcp_port_start=30000,
            tcp_port_count=0,
        )


# ---------------------------------------------------------------------------
# Break-Matrix Rows 14-20: Pool defense & route validation
# ---------------------------------------------------------------------------

def test_route_target_pool_validation():
    pool = ipaddress.ip_network("10.200.0.0/16")

    # Valid container targets inside pool
    assert validate_route_target("10.200.0.2", 8000, pool) is True
    assert validate_route_target("10.200.1.50", 8080, pool) is True
    assert validate_route_target("10.200.254.254", 30000, pool) is True

    # Row 16: Gateway (.1) rejected
    assert validate_route_target("10.200.0.1", 8080, pool) is False
    assert validate_route_target("10.200.1.1", 8080, pool) is False

    # Subnet boundaries (.0 and .255) rejected for /24
    assert validate_route_target("10.200.0.0", 8080, pool, pool_subnet_size=24) is False
    assert validate_route_target("10.200.0.255", 8080, pool, pool_subnet_size=24) is False
    assert validate_route_target("10.200.255.255", 8080, pool, pool_subnet_size=24) is False

    # Subnet boundary and gateway validation for /28 subnets (BLOCKING 5)
    # Subnet 1 (10.200.0.16/28): net .16, gw .17, usable .18..30, bcast .31
    assert validate_route_target("10.200.0.16", 8080, pool, pool_subnet_size=28) is False  # network
    assert validate_route_target("10.200.0.17", 8080, pool, pool_subnet_size=28) is False  # gateway
    assert validate_route_target("10.200.0.18", 8080, pool, pool_subnet_size=28) is True   # usable
    assert validate_route_target("10.200.0.31", 8080, pool, pool_subnet_size=28) is False  # broadcast
    # Subnet 2 (10.200.0.32/28): net .32, gw .33, usable .34..46, bcast .47
    assert validate_route_target("10.200.0.32", 8080, pool, pool_subnet_size=28) is False  # network
    assert validate_route_target("10.200.0.33", 8080, pool, pool_subnet_size=28) is False  # gateway
    assert validate_route_target("10.200.0.34", 8080, pool, pool_subnet_size=28) is True   # usable

    # Row 14: Cloud metadata 169.254.169.254 rejected
    assert validate_route_target("169.254.169.254", 80, pool) is False

    # Row 15: Loopback rejected
    assert validate_route_target("127.0.0.1", 8080, pool) is False
    assert validate_route_target("127.0.0.2", 8080, pool) is False

    # Row 17: Public IP rejected
    assert validate_route_target("8.8.8.8", 53, pool) is False
    assert validate_route_target("1.1.1.1", 53, pool) is False

    # Row 18: Private address outside the pool (Platform VM / VPC) rejected
    assert validate_route_target("10.0.0.5", 8080, pool) is False
    assert validate_route_target("10.10.0.1", 8080, pool) is False
    assert validate_route_target("192.168.1.1", 80, pool) is False
    assert validate_route_target("172.17.0.2", 80, pool) is False

    # Invalid port numbers
    assert validate_route_target("10.200.0.2", 0, pool) is False
    assert validate_route_target("10.200.0.2", -1, pool) is False
    assert validate_route_target("10.200.0.2", 65536, pool) is False
    assert validate_route_target("10.200.0.2", "8080", pool) is False  # type: ignore

    # Malformed IP strings
    assert validate_route_target("invalid_ip", 8080, pool) is False
    assert validate_route_target("", 8080, pool) is False
    assert validate_route_target("10.200.0.999", 8080, pool) is False


# ---------------------------------------------------------------------------
# Break-Matrix Row 11: Byte-rate limiter measurement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_rate_limiter_throttles_firehose():
    # 2 MB/s with 4 MB burst
    rate = 2 * 1024 * 1024
    burst = 4 * 1024 * 1024
    limiter = TokenBucketRateLimiter(rate_bps=rate, burst_bytes=burst)

    # First 4 MB should consume almost instantaneously from burst
    t0 = time.monotonic()
    await limiter.consume(4 * 1024 * 1024)
    elapsed_burst = time.monotonic() - t0
    assert elapsed_burst < 0.1

    # Remaining 6 MB (to total 10 MB) requires waiting 6MB / 2MB/s = 3.0s
    # In math: deficit / rate = 6291456 / 2097152 = 3.0s
    deficit = 6 * 1024 * 1024
    expected_wait = deficit / rate
    assert abs(expected_wait - 3.0) < 0.01

    # Test actual scaled rate limiter with smaller values for fast execution
    scaled_rate = 100 * 1024  # 100 KB/s
    scaled_burst = 50 * 1024   # 50 KB burst
    scaled_limiter = TokenBucketRateLimiter(rate_bps=scaled_rate, burst_bytes=scaled_burst)

    # Consume full burst
    await scaled_limiter.consume(50 * 1024)
    # Next 25 KB should take ~0.25 seconds
    t1 = time.monotonic()
    await scaled_limiter.consume(25 * 1024)
    elapsed = time.monotonic() - t1
    assert 0.20 <= elapsed <= 0.45


# ---------------------------------------------------------------------------
# Break-Matrix Row 4 & 5: Dynamic listener lifecycle & port reuse zero leakage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_listener_closes_on_route_removal():
    port = get_free_port()
    backend_port = get_free_port()

    # Start a mock backend
    backend_received = []

    async def mock_backend_cb(reader, writer):
        data = await reader.read(100)
        backend_received.append(data)
        writer.close()
        await writer.wait_closed()

    backend_server = await asyncio.start_server(mock_backend_cb, "127.0.0.1", backend_port)

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )

    try:
        # Route is added
        routes = [
            {
                "id": "inst1",
                "protocol": "tcp",
                "port": port,
                "target_ip": "127.0.0.1",
                "target_port": backend_port,
            }
        ]
        await router.sync_tcp_routes(routes)

        # Connect through router port
        client_r, client_w = await asyncio.open_connection("127.0.0.1", port)
        client_w.write(b"ping\n")
        await client_w.drain()
        await asyncio.sleep(0.1)
        client_w.close()
        await client_w.wait_closed()
        assert len(backend_received) == 1

        # Now remove the route
        await router.sync_tcp_routes([])

        # Connecting to port should now fail (listener is closed)
        with pytest.raises((ConnectionRefusedError, OSError)):
            await asyncio.open_connection("127.0.0.1", port)

    finally:
        await router.close_all_tcp_listeners()
        backend_server.close()
        await backend_server.wait_closed()


@pytest.mark.asyncio
async def test_tcp_port_reuse_no_byte_leakage():
    port = get_free_port()
    backend1_port = get_free_port()
    backend2_port = get_free_port()

    backend1_conns = []
    backend2_data = []

    async def backend1_cb(reader, writer):
        backend1_conns.append((reader, writer))
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def backend2_cb(reader, writer):
        data = await reader.read(1024)
        backend2_data.append(data)
        writer.write(b"welcome-backend-2\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    b1_srv = await asyncio.start_server(backend1_cb, "127.0.0.1", backend1_port)
    b2_srv = await asyncio.start_server(backend2_cb, "127.0.0.1", backend2_port)

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )

    try:
        # Route 1 on port
        await router.sync_tcp_routes([
            {"id": "inst_old", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend1_port}
        ])

        c1_r, c1_w = await asyncio.open_connection("127.0.0.1", port)
        c1_w.write(b"old-session-secret-bytes\n")
        await c1_w.drain()
        await asyncio.sleep(0.1)

        # Now re-route port to backend 2 (different instance)
        await router.sync_tcp_routes([
            {"id": "inst_new", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend2_port}
        ])

        # Client 1 connection must be terminated: see EOF or reset within 1.0s (no swallowing)
        saw_eof_or_reset = False
        try:
            read_rest = await asyncio.wait_for(c1_r.read(100), timeout=1.0)
            if read_rest == b"":
                saw_eof_or_reset = True
        except (ConnectionResetError, asyncio.IncompleteReadError):
            saw_eof_or_reset = True
        assert saw_eof_or_reset, "Old client did not see EOF or reset after route changed"
        c1_w.close()
        await c1_w.wait_closed()

        # Old backend must see its connection closed
        assert len(backend1_conns) == 1
        b1_r, b1_w = backend1_conns[0]
        b1_saw_eof = False
        try:
            b1_rest = await asyncio.wait_for(b1_r.read(100), timeout=1.0)
            if b1_rest == b"":
                b1_saw_eof = True
        except (ConnectionResetError, asyncio.IncompleteReadError):
            b1_saw_eof = True
        assert b1_saw_eof, "Old backend did not see EOF after route changed"

        # Connect as Client 2 to the reused port
        c2_r, c2_w = await asyncio.open_connection("127.0.0.1", port)
        c2_w.write(b"new-clean-session\n")
        await c2_w.drain()

        reply = await asyncio.wait_for(c2_r.readline(), timeout=2.0)
        assert reply == b"welcome-backend-2\n"

        c2_w.close()
        await c2_w.wait_closed()

        # Assert backend 2 never received any bytes from client 1
        assert len(backend2_data) == 1
        assert backend2_data[0] == b"new-clean-session\n"
        assert b"old-session-secret-bytes" not in backend2_data[0]

    finally:
        await router.close_all_tcp_listeners()
        b1_srv.close()
        b2_srv.close()
        await asyncio.gather(b1_srv.wait_closed(), b2_srv.wait_closed())


# ---------------------------------------------------------------------------
# Bidirectional relay & half-close propagation (Rows 24, 25)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_bidirectional_and_half_close():
    port = get_free_port()
    backend_port = get_free_port()

    async def echo_backend_cb(reader, writer):
        # Read until client EOF
        buffer = []
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            buffer.append(chunk)

        # After client half-closed, write response back
        writer.write(b"ECHO: " + b"".join(buffer))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    backend_srv = await asyncio.start_server(echo_backend_cb, "127.0.0.1", backend_port)

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )

    try:
        await router.sync_tcp_routes([
            {"id": "inst_echo", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}
        ])

        c_r, c_w = await asyncio.open_connection("127.0.0.1", port)
        c_w.write(b"payload_data")
        await c_w.drain()

        # Send half-close EOF
        c_w.write_eof()

        # Read echo response
        resp = await asyncio.wait_for(c_r.read(1024), timeout=2.0)
        assert resp == b"ECHO: payload_data"

        c_w.close()
        await c_w.wait_closed()

    finally:
        await router.close_all_tcp_listeners()
        backend_srv.close()
        try:
            await asyncio.wait_for(backend_srv.wait_closed(), timeout=2.0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Break-Matrix Row 6: Idle timeout disconnect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_idle_timeout_drops_connection():
    port = get_free_port()
    backend_port = get_free_port()

    async def quiet_backend_cb(reader, writer):
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    backend_srv = await asyncio.start_server(quiet_backend_cb, "127.0.0.1", backend_port)

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        tcp_idle_timeout=0.3,  # 300ms idle timeout for fast test
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )

    try:
        await router.sync_tcp_routes([
            {"id": "inst_idle", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}
        ])

        c_r, c_w = await asyncio.open_connection("127.0.0.1", port)
        c_w.write(b"initial_hello\n")
        await c_w.drain()

        # Wait longer than idle timeout without sending anything
        t0 = time.monotonic()
        data = await asyncio.wait_for(c_r.read(1024), timeout=1.5)
        elapsed = time.monotonic() - t0

        # Connection should have closed (EOF returned)
        assert data == b""
        assert elapsed >= 0.25

        c_w.close()
        try:
            await c_w.wait_closed()
        except Exception:
            pass

    finally:
        await router.close_all_tcp_listeners()
        backend_srv.close()
        try:
            await asyncio.wait_for(backend_srv.wait_closed(), timeout=2.0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Break-Matrix Rows 8-10: Concurrency Caps (instance, IP, global)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_connection_caps():
    port = get_free_port()
    backend_port = get_free_port()

    async def hold_backend_cb(reader, writer):
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    backend_srv = await asyncio.start_server(hold_backend_cb, "127.0.0.1", backend_port)

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        tcp_max_connections_per_instance=2,
        tcp_max_connections_per_ip=3,
        tcp_max_total_connections=4,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )

    try:
        await router.sync_tcp_routes([
            {"id": "inst_cap", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}
        ])

        # Open 2 connections (allowed)
        c1_r, c1_w = await asyncio.open_connection("127.0.0.1", port)
        c2_r, c2_w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.1)

        # 3rd connection should hit per-instance cap (2) and be closed immediately
        c3_r, c3_w = await asyncio.open_connection("127.0.0.1", port)
        data = await asyncio.wait_for(c3_r.read(100), timeout=1.0)
        assert data == b""  # Router closed connection
        c3_w.close()
        try:
            await c3_w.wait_closed()
        except Exception:
            pass

        metrics = router.get_tcp_metrics()
        assert metrics["refusals"].get("instance_cap", 0) >= 1

        # Clean up open conns
        c1_w.close()
        c2_w.close()
        await asyncio.gather(c1_w.wait_closed(), c2_w.wait_closed())

    finally:
        await router.close_all_tcp_listeners()
        backend_srv.close()
        try:
            await asyncio.wait_for(backend_srv.wait_closed(), timeout=2.0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Break-Matrix Row 13: Connect failure closes silently
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_connect_failure_closes_silently():
    port = get_free_port()
    dead_backend_port = get_free_port()  # Nothing listening here

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        tcp_connect_timeout=0.5,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )

    try:
        await router.sync_tcp_routes([
            {"id": "inst_dead", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": dead_backend_port}
        ])

        c_r, c_w = await asyncio.open_connection("127.0.0.1", port)
        # Router attempts to connect to dead port, fails, and closes client silently
        data = await asyncio.wait_for(c_r.read(1024), timeout=1.5)
        # Must be empty (silent close, zero banner, zero traceback)
        assert data == b""

        c_w.close()
        try:
            await c_w.wait_closed()
        except Exception:
            pass

        metrics = router.get_tcp_metrics()
        assert metrics["connect_failures"] >= 1

    finally:
        await router.close_all_tcp_listeners()


# ---------------------------------------------------------------------------
# Metrics & Prometheus Output
# ---------------------------------------------------------------------------

def test_tcp_metrics_reporting():
    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
    )
    metrics = router.get_tcp_metrics()
    assert "open_relays" in metrics
    assert "bytes_relayed" in metrics
    assert "refusals" in metrics
    assert "connect_failures" in metrics

    prom = router.format_tcp_metrics_prometheus()
    assert "instancer_router_tcp_open_relays" in prom
    assert "instancer_router_tcp_bytes_total" in prom
    assert "instancer_router_tcp_connect_failures_total" in prom


# ---------------------------------------------------------------------------
# Break-Matrix Row 37: Pwn fixture returns dynamic flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_fixture_returns_dynamic_flag():
    # Import and run fixture server in-process
    os.environ["FLAG"] = "DYNAMIC_TEST_FLAG_PWN_1337"
    from services.instancer.tests.fixtures.tcp import server as tcp_fixture

    fixture_port = get_free_port()
    tcp_fixture.PORT = fixture_port
    tcp_fixture.FLAG = os.environ["FLAG"]

    server = await asyncio.start_server(tcp_fixture.handle_client, "127.0.0.1", fixture_port)

    try:
        r, w = await asyncio.open_connection("127.0.0.1", fixture_port)
        banner = await r.readline()
        assert b"L3m0nCTF TCP fixture ready" in banner

        # Send command "open"
        w.write(b"open\n")
        await w.drain()
        flag_line = await r.readline()
        assert flag_line.decode().strip() == "DYNAMIC_TEST_FLAG_PWN_1337"

        # Send command "echo test"
        w.write(b"echo hello\n")
        await w.drain()
        echo_line = await r.readline()
        assert echo_line.decode().strip() == "hello"

        # Send quit
        w.write(b"quit\n")
        await w.drain()
        w.close()
        await w.wait_closed()

    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Lead Audit A2a: Double-Sync Probes & Poll Path
# ---------------------------------------------------------------------------

async def poll_like(router, routes):
    """Exactly what poll_routes_loop does with a fresh route list."""
    async with router._lock:
        router.update_routes(routes)
    await router.sync_tcp_routes(routes)


@pytest.mark.asyncio
async def test_poll_path_new_route_binds_once_without_errors(caplog):
    port = get_free_port()
    backend_port = get_free_port()

    async def backend_cb(reader, writer):
        data = await reader.read(100)
        writer.write(b"ACK:" + data)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    srv = await asyncio.start_server(backend_cb, "127.0.0.1", backend_port)
    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    try:
        route_a = {
            "id": "inst_a",
            "protocol": "tcp",
            "port": port,
            "target_ip": "127.0.0.1",
            "target_port": backend_port,
        }
        with caplog.at_level(logging.ERROR):
            await poll_like(router, [route_a])
            await asyncio.sleep(0.5)

        # Assert no log record at ERROR level (single bind, no EADDRINUSE race)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0, f"Found unexpected ERROR logs: {error_records}"

        # A client connecting to the relay port reaches backend A
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"probe-payload\n")
        await w.drain()
        reply = await asyncio.wait_for(r.readline(), timeout=2.0)
        assert reply == b"ACK:probe-payload\n"
        w.close()
        await w.wait_closed()
    finally:
        await router.close_all_tcp_listeners()
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_poll_path_port_reuse_has_the_new_route_after_one_poll():
    port = get_free_port()
    backend_a_port = get_free_port()
    backend_b_port = get_free_port()

    async def backend_a_cb(reader, writer):
        await reader.read(100)
        writer.write(b"hello-from-A\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def backend_b_cb(reader, writer):
        await reader.read(100)
        writer.write(b"hello-from-B\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    srv_a = await asyncio.start_server(backend_a_cb, "127.0.0.1", backend_a_port)
    srv_b = await asyncio.start_server(backend_b_cb, "127.0.0.1", backend_b_port)

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    try:
        route_a = {"id": "inst_a", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_a_port}
        await poll_like(router, [route_a])
        await asyncio.sleep(0.3)

        # Client talks to A (this connection is what exposes port reuse races)
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"hi\n")
        await w.drain()
        reply_a = await asyncio.wait_for(r.readline(), timeout=2.0)
        assert reply_a == b"hello-from-A\n"
        w.close()
        await w.wait_closed()

        # Re-route same port to B in a single poll
        route_b = {"id": "inst_b", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_b_port}
        await poll_like(router, [route_b])
        await asyncio.sleep(0.6)

        # Assert router._tcp_routes[port]["id"] is B's id AND a new client reaches B (never ConnectionResetError)
        assert port in router._tcp_routes
        assert router._tcp_routes[port]["id"] == "inst_b"

        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        w2.write(b"hi\n")
        await w2.drain()
        reply_b = await asyncio.wait_for(r2.readline(), timeout=2.0)
        assert reply_b == b"hello-from-B\n"
        w2.close()
        await w2.wait_closed()
    finally:
        await router.close_all_tcp_listeners()
        srv_a.close()
        await srv_a.wait_closed()
        srv_b.close()
        await srv_b.wait_closed()


# ---------------------------------------------------------------------------
# Lead Audit A2b: Three Settings Range Validation & main.py Validation
# ---------------------------------------------------------------------------

def test_router_tcp_settings_validation():
    # Valid explicit boundaries
    r = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_max_connections_per_instance=5,
        tcp_max_connections_per_ip=60,
        tcp_max_total_connections=500,
        tcp_rate_limit_bps=2 * 1024 * 1024,
        tcp_rate_limit_burst=4 * 1024 * 1024,
        tcp_idle_timeout=300.0,
        tcp_lifetime_cap=3600.0,
        instance_pool="10.200.0.0/16,172.16.0.0/12",
    )
    assert r.tcp_max_connections_per_instance == 5
    assert r.tcp_max_connections_per_ip == 60
    assert r.tcp_max_total_connections == 500
    assert r.tcp_rate_limit_bps == 2 * 1024 * 1024
    assert r.tcp_rate_limit_burst == 4 * 1024 * 1024
    assert len(r.pool_networks) == 2

    # Reject out of bounds INSTANCER_TCP_MAX_CONNS_PER_INSTANCE
    with pytest.raises(ValueError, match="INSTANCER_TCP_MAX_CONNS_PER_INSTANCE"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", tcp_max_connections_per_instance=0)
    with pytest.raises(ValueError, match="INSTANCER_TCP_MAX_CONNS_PER_INSTANCE"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", tcp_max_connections_per_instance=101)

    # Reject out of bounds INSTANCER_TCP_MAX_TOTAL_CONNS
    with pytest.raises(ValueError, match="INSTANCER_TCP_MAX_TOTAL_CONNS"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", tcp_max_total_connections=0)
    with pytest.raises(ValueError, match="INSTANCER_TCP_MAX_TOTAL_CONNS"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", tcp_max_total_connections=10001)

    # Reject out of bounds INSTANCER_TCP_RATE_LIMIT_BURST
    with pytest.raises(ValueError, match="INSTANCER_TCP_RATE_LIMIT_BURST"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", tcp_rate_limit_burst=512)
    with pytest.raises(ValueError, match="INSTANCER_TCP_RATE_LIMIT_BURST"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", tcp_rate_limit_burst=2 * 1024 * 1024 * 1024)

    # Reject empty or invalid pool
    with pytest.raises(ValueError, match="INSTANCER_INSTANCE_POOL"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", instance_pool="   ")
    with pytest.raises(ValueError, match="Invalid INSTANCER_INSTANCE_POOL entry"):
        InstanceRouter(instance_domain="example.com", instancer_url="http://127.0.0.1:8300", instance_pool="not-a-cidr")


def test_router_main_env_reading():
    from services.instancer.router.main import parse_router_config

    # 1. Defaults verification
    defaults = parse_router_config({})
    assert defaults["port"] == 8090
    assert defaults["tcp_port_start"] == 30000
    assert defaults["tcp_port_count"] == 200
    assert defaults["tcp_idle_timeout"] == 300.0
    assert defaults["tcp_lifetime_cap"] == 3600.0
    assert defaults["tcp_max_conns_per_instance"] == 10
    assert defaults["tcp_max_conns_per_ip"] == 60
    assert defaults["tcp_max_total_conns"] == 2000
    assert defaults["tcp_rate_limit_bps"] == 2097152
    assert defaults["tcp_rate_limit_burst"] == 4194304
    assert defaults["pool_subnet_size"] == 24
    assert defaults["instance_pool"] == "10.200.0.0/16"

    # 2. Custom env values
    custom = parse_router_config({
        "ROUTER_HTTP_PORT": "8088",
        "INSTANCER_TCP_MAX_CONNS_PER_INSTANCE": "25",
        "INSTANCER_TCP_MAX_TOTAL_CONNS": "5000",
        "INSTANCER_TCP_RATE_LIMIT_BURST": "8388608",
        "INSTANCER_POOL_SUBNET_SIZE": "28",
    })
    assert custom["port"] == 8088
    assert custom["tcp_max_conns_per_instance"] == 25
    assert custom["tcp_max_total_conns"] == 5000
    assert custom["tcp_rate_limit_burst"] == 8388608
    assert custom["pool_subnet_size"] == 28

    # 3. Invalid values for real environment variables raise ValueError naming the variable
    with pytest.raises(ValueError, match="INSTANCER_TCP_MAX_CONNS_PER_INSTANCE must be an integer"):
        parse_router_config({"INSTANCER_TCP_MAX_CONNS_PER_INSTANCE": "notanumber"})

    with pytest.raises(ValueError, match="INSTANCER_TCP_MAX_TOTAL_CONNS must be an integer"):
        parse_router_config({"INSTANCER_TCP_MAX_TOTAL_CONNS": "abc"})

    with pytest.raises(ValueError, match="INSTANCER_TCP_RATE_LIMIT_BURST must be an integer"):
        parse_router_config({"INSTANCER_TCP_RATE_LIMIT_BURST": "invalid"})

    with pytest.raises(ValueError, match="POLL_INTERVAL must be positive"):
        parse_router_config({"POLL_INTERVAL": "0"})

    with pytest.raises(ValueError, match="ROUTER_HTTP_PORT must be between 1 and 65535"):
        parse_router_config({"ROUTER_HTTP_PORT": "99999"})


# ---------------------------------------------------------------------------
# Lead Audit A3: Surviving Safety Rules Made Able to Fail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_per_ip_cap_boundary():
    port = get_free_port()
    backend_port = get_free_port()

    async def backend_cb(reader, writer):
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
        writer.close()
        await writer.wait_closed()

    backend_srv = await asyncio.start_server(backend_cb, "127.0.0.1", backend_port)
    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        tcp_max_connections_per_ip=3,
        tcp_max_connections_per_instance=10,
        tcp_max_total_connections=100,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    try:
        await router.sync_tcp_routes([
            {"id": "inst_ip_cap", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}
        ])
        # Open 3 connections from 127.0.0.1 -> all 3 succeed
        c1_r, c1_w = await asyncio.open_connection("127.0.0.1", port)
        c2_r, c2_w = await asyncio.open_connection("127.0.0.1", port)
        c3_r, c3_w = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0.1)

        # 4th connection from same IP must be closed immediately
        c4_r, c4_w = await asyncio.open_connection("127.0.0.1", port)
        data = await asyncio.wait_for(c4_r.read(100), timeout=1.0)
        assert data == b"", "4th connection was not refused by per-IP cap"
        c4_w.close()
        await c4_w.wait_closed()

        metrics = router.get_tcp_metrics()
        assert metrics["refusals"].get("ip_cap", 0) == 1

        c1_w.close()
        c2_w.close()
        c3_w.close()
        await asyncio.gather(c1_w.wait_closed(), c2_w.wait_closed(), c3_w.wait_closed())
    finally:
        await router.close_all_tcp_listeners()
        backend_srv.close()
        try:
            await asyncio.wait_for(backend_srv.wait_closed(), timeout=2.0)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_tcp_global_cap_boundary():
    port = get_free_port()
    backend_port = get_free_port()

    async def backend_cb(reader, writer):
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
        writer.close()
        await writer.wait_closed()

    backend_srv = await asyncio.start_server(backend_cb, "127.0.0.1", backend_port)
    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        tcp_max_total_connections=3,
        tcp_max_connections_per_ip=10,
        tcp_max_connections_per_instance=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    try:
        await router.sync_tcp_routes([
            {"id": "inst_glob_cap", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}
        ])
        # Open 3 connections from distinct IP addresses (127.0.0.1, 127.0.0.2, 127.0.0.3)
        c1_r, c1_w = await asyncio.open_connection("127.0.0.1", port, local_addr=("127.0.0.1", 0))
        c2_r, c2_w = await asyncio.open_connection("127.0.0.1", port, local_addr=("127.0.0.2", 0))
        c3_r, c3_w = await asyncio.open_connection("127.0.0.1", port, local_addr=("127.0.0.3", 0))
        await asyncio.sleep(0.1)

        # 4th connection from 127.0.0.4 must be refused
        c4_r, c4_w = await asyncio.open_connection("127.0.0.1", port, local_addr=("127.0.0.4", 0))
        data = await asyncio.wait_for(c4_r.read(100), timeout=1.0)
        assert data == b"", "4th connection exceeded global cap but was not refused"
        c4_w.close()
        await c4_w.wait_closed()

        metrics = router.get_tcp_metrics()
        assert metrics["refusals"].get("global_cap", 0) == 1

        c1_w.close()
        c2_w.close()
        c3_w.close()
        await asyncio.gather(c1_w.wait_closed(), c2_w.wait_closed(), c3_w.wait_closed())
    finally:
        await router.close_all_tcp_listeners()
        backend_srv.close()
        try:
            await asyncio.wait_for(backend_srv.wait_closed(), timeout=2.0)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_tcp_absolute_lifetime_cap():
    port = get_free_port()
    backend_port = get_free_port()

    async def echo_cb(reader, writer):
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    srv = await asyncio.start_server(echo_cb, "127.0.0.1", backend_port)
    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        tcp_idle_timeout=10.0,
        tcp_lifetime_cap=0.5,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    try:
        await router.sync_tcp_routes([
            {"id": "inst_lifetime", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}
        ])
        c_r, c_w = await asyncio.open_connection("127.0.0.1", port)
        start_time = time.perf_counter()
        closed = False
        while time.perf_counter() - start_time < 2.0:
            c_w.write(b"ping\n")
            try:
                await c_w.drain()
                resp = await asyncio.wait_for(c_r.readline(), timeout=0.2)
                if not resp:
                    closed = True
                    break
            except (ConnectionResetError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                closed = True
                break
            await asyncio.sleep(0.05)

        elapsed = time.perf_counter() - start_time
        assert closed is True, "Connection remained open past lifetime cap"
        assert 0.45 <= elapsed <= 1.5, f"Connection closed at unexpected elapsed time: {elapsed:.2f}s"
        c_w.close()
        await c_w.wait_closed()
    finally:
        await router.close_all_tcp_listeners()
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_tcp_real_byte_rate_transfer_timing():
    port = get_free_port()
    backend_port = get_free_port()

    async def echo_cb(reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    srv = await asyncio.start_server(echo_cb, "127.0.0.1", backend_port)
    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=port,
        tcp_port_count=10,
        tcp_rate_limit_bps=200_000,
        tcp_rate_limit_burst=100_000,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    try:
        await router.sync_tcp_routes([
            {"id": "inst_rate", "protocol": "tcp", "port": port, "target_ip": "127.0.0.1", "target_port": backend_port}
        ])

        payload = b"X" * 400_000
        c_r, c_w = await asyncio.open_connection("127.0.0.1", port)

        start = time.perf_counter()

        async def send():
            c_w.write(payload)
            await c_w.drain()

        async def recv():
            received = b""
            while len(received) < len(payload):
                chunk = await c_r.read(min(65536, len(payload) - len(received)))
                if not chunk:
                    break
                received += chunk
            return received

        _, received = await asyncio.gather(send(), recv())
        elapsed = time.perf_counter() - start

        assert len(received) == 400_000
        assert received == payload
        # Burst 100k + remaining 300k at 200k/s = 1.5s
        assert 1.2 <= elapsed < 3.0, f"Transfer took {elapsed:.2f}s, expected between 1.2s and 3.0s"

        c_w.close()
        await c_w.wait_closed()
    finally:
        await router.close_all_tcp_listeners()
        srv.close()
        await srv.wait_closed()


# ---------------------------------------------------------------------------
# Lead Audit A4: HTTP Metrics Endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_http_metrics_endpoint():
    from services.instancer.router.main import handle_client
    port = get_free_port()
    relay_port = get_free_port()

    router = InstanceRouter(
        instance_domain="example.com",
        instancer_url="http://127.0.0.1:8300",
        router_token="secret",
        tcp_port_start=relay_port,
        tcp_port_count=10,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )
    http_srv = await asyncio.start_server(
        lambda r, w: handle_client(r, w, router),
        "127.0.0.1",
        port,
    )
    try:
        # 1. Unauthenticated request should be refused with 401 Unauthorized
        r_unauth, w_unauth = await asyncio.open_connection("127.0.0.1", port)
        w_unauth.write(b"GET /metrics HTTP/1.1\r\nHost: router\r\nConnection: close\r\n\r\n")
        await w_unauth.drain()
        resp_unauth = await r_unauth.read()
        w_unauth.close()
        try:
            await asyncio.wait_for(w_unauth.wait_closed(), timeout=1.0)
        except Exception:
            pass
        assert "HTTP/1.1 401 Unauthorized" in resp_unauth.decode("utf-8", errors="replace")

        # 2. Authenticated request with Bearer secret returns 200 OK
        r, w = await asyncio.open_connection("127.0.0.1", port)
        req = b"GET /metrics HTTP/1.1\r\nHost: router\r\nAuthorization: Bearer secret\r\nConnection: close\r\n\r\n"
        w.write(req)
        await w.drain()
        resp = await r.read()
        w.close()
        try:
            await asyncio.wait_for(w.wait_closed(), timeout=1.0)
        except Exception:
            pass

        resp_text = resp.decode("utf-8", errors="replace")
        assert "HTTP/1.1 200 OK" in resp_text
        assert "instancer_router_tcp_open_relays" in resp_text
        assert "instancer_router_tcp_bytes_total" in resp_text
        assert "instancer_router_tcp_connect_failures_total" in resp_text
        assert "instancer_router_tcp_refusals_total" in resp_text
        assert "instancer_router_http_active_connections" in resp_text
    finally:
        http_srv.close()
        try:
            await asyncio.wait_for(http_srv.wait_closed(), timeout=2.0)
        except Exception:
            pass
        await router.close_all_tcp_listeners()

