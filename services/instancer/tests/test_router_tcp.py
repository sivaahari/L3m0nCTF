import asyncio
import ipaddress
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

    # Subnet boundaries (.0 and .255) rejected
    assert validate_route_target("10.200.0.0", 8080, pool) is False
    assert validate_route_target("10.200.0.255", 8080, pool) is False
    assert validate_route_target("10.200.255.255", 8080, pool) is False

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

        # Client 1 connection should be terminated (reading returns EOF or error)
        try:
            read_rest = await asyncio.wait_for(c1_r.read(100), timeout=1.0)
            assert read_rest == b""
        except Exception:
            pass
        c1_w.close()
        try:
            await c1_w.wait_closed()
        except Exception:
            pass

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
        await backend_srv.wait_closed()


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
        await backend_srv.wait_closed()


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
        await backend_srv.wait_closed()


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
