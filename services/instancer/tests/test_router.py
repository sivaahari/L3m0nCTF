import asyncio
import re
import pytest

from services.instancer.router.router import InstanceRouter, sanitize_headers, extract_instance_id

INSTANCE_DOMAIN = "inst.example.com"
ROUTE_ID = "0123456789abcdef0123456789abcdef"


def test_extract_instance_id_valid():
    host = f"{ROUTE_ID}.{INSTANCE_DOMAIN}"
    assert extract_instance_id(host, INSTANCE_DOMAIN) == ROUTE_ID

    # Host with port
    host_with_port = f"{ROUTE_ID}.{INSTANCE_DOMAIN}:8090"
    assert extract_instance_id(host_with_port, INSTANCE_DOMAIN) == ROUTE_ID


def test_extract_instance_id_open_proxy_attempts():
    # Localhost, IPs, metadata service, wrong domain, bad format
    bad_hosts = [
        "localhost",
        "localhost:8090",
        "127.0.0.1",
        "169.254.169.254",
        "metadata.google.internal",
        "attacker.com",
        f"other.{INSTANCE_DOMAIN}",
        f"{ROUTE_ID}.attacker.com",
        f"-{ROUTE_ID}.{INSTANCE_DOMAIN}",
        f"{ROUTE_ID}a.{INSTANCE_DOMAIN}",
        "",
    ]
    for h in bad_hosts:
        assert extract_instance_id(h, INSTANCE_DOMAIN) is None


def test_header_sanitization():
    headers = {
        "Host": f"{ROUTE_ID}.{INSTANCE_DOMAIN}",
        "User-Agent": "TestClient/1.0",
        "Connection": "keep-alive, upgrade",
        "Keep-Alive": "timeout=5",
        "Proxy-Authorization": "Basic dGVzdDp0ZXN0",
        "X-Forwarded-For": "1.2.3.4",
        "Custom-Header": "AllowedValue",
    }
    sanitized = sanitize_headers(headers, is_websocket=False)

    assert "Host" in sanitized
    assert "User-Agent" in sanitized
    assert "Custom-Header" in sanitized
    # Hop-by-hop headers must be stripped
    assert "Connection" not in sanitized
    assert "Keep-Alive" not in sanitized
    assert "Proxy-Authorization" not in sanitized


@pytest.mark.asyncio
async def test_router_closed_proxy_refusal():
    router = InstanceRouter(instance_domain=INSTANCE_DOMAIN, instancer_url="http://127.0.0.1:8300")
    # Set routes table directly
    router.update_routes([
        {"id": ROUTE_ID, "protocol": "http", "target_ip": "10.0.1.2", "target_port": 8080}
    ])

    # 1. Unknown instance ID -> returns 404
    status, _, _ = await router.handle_http_request(
        method="GET",
        path="/",
        headers={"Host": f"fedcba9876543210fedcba9876543210.{INSTANCE_DOMAIN}"},
        body=b"",
    )
    assert status == 404

    # 2. Host metadata / localhost -> returns 404
    status, _, _ = await router.handle_http_request(
        method="GET",
        path="/",
        headers={"Host": "169.254.169.254"},
        body=b"",
    )
    assert status == 404

    # 3. Payload too large (over 10 MB limit)
    large_body = b"A" * (10 * 1024 * 1024 + 1)
    status, _, _ = await router.handle_http_request(
        method="POST",
        path="/upload",
        headers={"Host": f"{ROUTE_ID}.{INSTANCE_DOMAIN}"},
        body=large_body,
    )
    assert status == 413


# ---- added by the integration audit (2026-10-18): the router is reachable by anybody, so what it reads from a stranger must be bounded ----------

import asyncio

from services.instancer.router.main import handle_client


class _Writer:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, chunk):
        self.data += chunk

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def _serve(raw: bytes, *, eof=True, **kwargs):
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        if eof:
            reader.feed_eof()
        writer = _Writer()
        await asyncio.wait_for(handle_client(reader, writer, InstanceRouter(instance_domain="inst.test"), **kwargs), timeout=5)
        return writer.data

    return asyncio.run(run())


def test_a_huge_declared_body_is_refused_with_413_before_any_of_it_is_read():
    """Content-Length was believed and read in full before the size check: one request could use all of the router's memory. No valid host was needed."""
    answer = _serve(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 4000000000\r\n\r\nabc")
    assert answer.startswith(b"HTTP/1.1 413"), answer[:80]


def test_too_many_header_lines_are_refused_with_431():
    many = b"".join(b"X-%d: y\r\n" % i for i in range(500))
    answer = _serve(b"GET / HTTP/1.1\r\nHost: x\r\n" + many + b"\r\n")
    assert answer.startswith(b"HTTP/1.1 431"), answer[:80]


def test_a_client_that_stops_sending_is_dropped_at_the_timeout_not_kept_for_ever():
    """A slow client (the request line arrives, the rest never does) must not hold a connection and its memory indefinitely."""
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"GET / HTTP/1.1\r\nHost: x\r\n")  # no blank line, no end of stream
        writer = _Writer()
        await asyncio.wait_for(handle_client(reader, writer, InstanceRouter(instance_domain="inst.test"), header_timeout=0.2), timeout=5)
        return writer

    writer = asyncio.run(run())
    assert writer.closed and writer.data.startswith(b"HTTP/1.1 408")


def test_an_ordinary_unknown_host_request_still_gets_the_static_404():
    answer = _serve(b"GET / HTTP/1.1\r\nHost: evil.example\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 404")


def test_rejects_conflicting_content_length_and_transfer_encoding():
    answer = _serve(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\nhello")
    assert answer.startswith(b"HTTP/1.1 400"), answer[:80]


def test_refuses_chunked_request_body():
    answer = _serve(b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 400"), answer[:80]


def test_preserves_multiple_set_cookie_headers_and_single_content_length():
    """Found by audit: Set-Cookie collapsed in plain dict, duplicate Content-Length was emitted."""
    async def run():
        # Mock backend server that returns two Set-Cookie headers
        backend_received = b""

        async def mock_backend(reader, writer):
            nonlocal backend_received
            backend_received = await reader.read(4096)
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Set-Cookie: session=abc; Path=/\r\n"
                b"Set-Cookie: theme=dark; Path=/\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 4\r\n"
                b"\r\n"
                b"pong"
            )
            writer.write(resp)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": port}])

        reader = asyncio.StreamReader()
        reader.feed_data(f"GET /ping HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()

        await handle_client(reader, writer, router)
        server.close()
        await server.wait_closed()
        return writer.data, backend_received

    data, backend_req = asyncio.run(run())
    text = data.decode("utf-8", errors="replace")
    # Verify multiple Set-Cookie headers are preserved
    assert text.count("Set-Cookie: session=abc") == 1
    assert text.count("Set-Cookie: theme=dark") == 1
    # Verify Content-Length appears exactly once in the response
    assert text.lower().count("content-length:") == 1
    assert "pong" in text
    # Verify backend received exactly one Content-Length: 0
    assert backend_req.lower().count(b"content-length:") <= 1


def test_caps_streaming_backend_response_at_20mb():
    """Backend streaming more than 20MB must be cut off to protect router memory."""
    async def run():
        async def mock_streaming_backend(reader, writer):
            await reader.read(4096)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n\r\n")
            await writer.drain()
            chunk = b"X" * (64 * 1024)
            # Send 25 MB
            try:
                for _ in range(400):
                    writer.write(chunk)
                    await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_streaming_backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": port}])

        reader = asyncio.StreamReader()
        reader.feed_data(f"GET /stream HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()

        await handle_client(reader, writer, router)
        server.close()
        await server.wait_closed()
        return len(writer.data)

    total_received = asyncio.run(run())
    # Must be cut off at or before 20MB (+ header bytes)
    assert total_received <= 21 * 1024 * 1024


def test_enforces_max_connections_per_instance():
    """More than 50 concurrent connections to the same instance ID must return 503."""
    async def run():
        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": 8080}])
        # Manually saturate instance connections
        router._active_connections_per_inst[inst_id] = 50

        reader = asyncio.StreamReader()
        reader.feed_data(f"GET / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()

        await handle_client(reader, writer, router)
        return writer.data

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 503"), data[:80]


def test_enforces_max_total_connections():
    """More than 1000 total connections on router must return 503."""
    async def run():
        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": 8080}])
        # Manually saturate total connections
        router._total_active_connections = 1000

        reader = asyncio.StreamReader()
        reader.feed_data(f"GET / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()

        await handle_client(reader, writer, router)
        return writer.data

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 503"), data[:80]


def test_backend_that_never_answers_triggers_504():
    """Backend connection succeeds but sends no bytes -> times out with 504."""
    async def run():
        async def mock_silent_backend(reader, writer):
            # Read request, but never respond; exit as soon as router closes or 2s timeout
            try:
                await reader.read(4096)
                await asyncio.wait_for(reader.read(1), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        server = await asyncio.start_server(mock_silent_backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": port}])

        # Monkeypatch IDLE_TIMEOUT_SECONDS for fast test
        import services.instancer.router.router as router_mod
        orig_timeout = router_mod.IDLE_TIMEOUT_SECONDS
        router_mod.IDLE_TIMEOUT_SECONDS = 0.2
        try:
            reader = asyncio.StreamReader()
            reader.feed_data(f"GET / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
            reader.feed_eof()
            writer = _Writer()

            await handle_client(reader, writer, router)
            server.close()
            await asyncio.wait_for(server.wait_closed(), timeout=2.0)
            return writer.data
        finally:
            router_mod.IDLE_TIMEOUT_SECONDS = orig_timeout


    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 504"), data[:80]


def test_backend_sending_garbage_returns_502():
    """Backend immediately closes without valid HTTP response -> 502."""
    async def run():
        async def mock_garbage_backend(reader, writer):
            await reader.read(4096)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_garbage_backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": port}])

        reader = asyncio.StreamReader()
        reader.feed_data(f"GET / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()

        await handle_client(reader, writer, router)
        server.close()
        await server.wait_closed()
        return writer.data

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 502"), data[:80]


def test_slow_body_client_times_out():
    """Client declared Content-Length on valid instance but never sends body bytes -> times out with 408."""
    async def run():
        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": 8080}])
        reader = asyncio.StreamReader()
        reader.feed_data(f"POST / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\nContent-Length: 50\r\n\r\npart".encode())
        # Do not feed EOF
        writer = _Writer()
        await handle_client(reader, writer, router, header_timeout=1.0, body_timeout=0.2)
        return writer.data

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 408"), data[:80]



def test_forwards_backend_chunked_response():
    """Backend sends Transfer-Encoding: chunked without Content-Length -> router forwards Transfer-Encoding: chunked."""
    async def run():
        async def mock_chunked_backend(reader, writer):
            await reader.read(4096)
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"5\r\nhello\r\n0\r\n\r\n"
            )
            writer.write(resp)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(mock_chunked_backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": port}])

        reader = asyncio.StreamReader()
        reader.feed_data(f"GET /chunked HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()

        await handle_client(reader, writer, router)
        server.close()
        await server.wait_closed()
        return writer.data

    data = asyncio.run(run())
    assert b"Transfer-Encoding: chunked" in data
    assert b"Content-Length" not in data
    assert b"hello" in data


def test_the_connection_to_the_instance_is_closed_by_the_router_when_it_gives_up():
    """When router forward fails or times out, the backend socket must be closed in finally."""
    async def run():
        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": 8080}])

        class SpyBackendWriter:
            def __init__(self):
                self.closed = False
                self.wait_closed_called = False

            def write(self, data):
                pass

            async def drain(self):
                pass

            def close(self):
                self.closed = True

            async def wait_closed(self):
                self.wait_closed_called = True

        spy_writer = SpyBackendWriter()
        mock_reader = asyncio.StreamReader()

        async def fake_open_connection(ip, port):
            return mock_reader, spy_writer

        import unittest.mock as mock
        with mock.patch("asyncio.open_connection", side_effect=fake_open_connection):
            import services.instancer.router.router as router_mod
            orig_timeout = router_mod.IDLE_TIMEOUT_SECONDS
            router_mod.IDLE_TIMEOUT_SECONDS = 0.05
            try:
                reader = asyncio.StreamReader()
                reader.feed_data(f"GET / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
                reader.feed_eof()
                client_writer = _Writer()

                await handle_client(reader, client_writer, router)
            finally:
                router_mod.IDLE_TIMEOUT_SECONDS = orig_timeout

        return spy_writer.closed

    closed = asyncio.run(run())
    assert closed is True


# ==============================================================================
# Round 2 Audit Findings Tests (N1, N2, N3)
# ==============================================================================

def test_unknown_host_large_body_refused_immediately_without_reading_body():
    """N1: Request for no valid instance is refused at once, without waiting to read body."""
    async def run():
        reader = asyncio.StreamReader()
        # Send headers with declared Content-Length: 5000000 without sending body and without EOF
        reader.feed_data(b"POST / HTTP/1.1\r\nHost: unknown.inst.test\r\nContent-Length: 5000000\r\n\r\n")
        writer = _Writer()
        await asyncio.wait_for(
            handle_client(reader, writer, InstanceRouter(instance_domain="inst.test"), header_timeout=1.0, body_timeout=5.0),
            timeout=0.5,
        )
        return writer.data

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 404"), data[:80]


def test_body_exceeding_challenge_cap_refused_immediately_without_reading():
    """N1: Challenge request-body cap in manifest (e.g. 500 KB) refuses before reading."""
    async def run():
        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{
            "id": inst_id,
            "protocol": "http",
            "target_ip": "127.0.0.1",
            "target_port": 8080,
            "max_body_bytes": 500000,
        }])
        reader = asyncio.StreamReader()
        # Declare 600 KB without sending body or EOF
        reader.feed_data(f"POST / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\nContent-Length: 600000\r\n\r\n".encode())
        writer = _Writer()
        await asyncio.wait_for(
            handle_client(reader, writer, router, header_timeout=1.0, body_timeout=5.0),
            timeout=0.5,
        )
        return writer.data

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 413"), data[:80]


def test_reading_budget_exhaustion_returns_503():
    """N1: Concurrent body reads exceeding global budget (64MB) return 503."""
    async def run():
        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{
            "id": inst_id,
            "protocol": "http",
            "target_ip": "127.0.0.1",
            "target_port": 8080,
            "max_body_bytes": 10 * 1024 * 1024,
        }])
        # Manually saturate reading budget
        router._current_reading_bytes = 60 * 1024 * 1024
        reader = asyncio.StreamReader()
        # Request needing 8 MB exceeds 64 MB budget
        reader.feed_data(f"POST / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\nContent-Length: 8000000\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()
        await handle_client(reader, writer, router, header_timeout=1.0, body_timeout=0.2)
        return writer.data

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 503"), data[:80]


def test_request_line_too_large_refused_with_431():
    """N2: Request line longer than 8 KB is refused with 431."""
    long_path = "A" * 9000
    answer = _serve(f"GET /{long_path} HTTP/1.1\r\nHost: inst.test\r\n\r\n".encode())
    assert answer.startswith(b"HTTP/1.1 431"), answer[:80]


def test_header_block_too_large_refused_with_431():
    """N2: Header block exceeding 16 KB total is refused with 431."""
    # 25 headers of 700 bytes = 17.5 KB > 16 KB cap
    headers = b"".join(b"X-Header-%02d: %s\r\n" % (i, b"V" * 700) for i in range(25))
    answer = _serve(b"GET / HTTP/1.1\r\nHost: inst.test\r\n" + headers + b"\r\n")
    assert answer.startswith(b"HTTP/1.1 431"), answer[:80]


def test_pre_routing_connection_limits_and_ip_limiting():
    """N2: Pre-routing global connection limit (1000) and per-IP limit (50)."""
    async def run_global_limit():
        router = InstanceRouter(instance_domain="inst.test")
        router._total_active_connections = 1000
        reader = asyncio.StreamReader()
        reader.feed_data(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        reader.feed_eof()
        writer = _Writer()
        await handle_client(reader, writer, router)
        return writer.data

    data = asyncio.run(run_global_limit())
    assert data.startswith(b"HTTP/1.1 503"), data[:80]

    async def run_ip_limit():
        router = InstanceRouter(instance_domain="inst.test")
        router._active_connections_per_ip["198.51.100.5"] = 50
        reader = asyncio.StreamReader()
        reader.feed_data(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        reader.feed_eof()
        writer = _Writer()
        writer.get_extra_info = lambda key, default=None: ("198.51.100.5", 12345) if key == "peername" else default
        await handle_client(reader, writer, router)
        return writer.data

    data_ip = asyncio.run(run_ip_limit())
    assert data_ip.startswith(b"HTTP/1.1 503"), data_ip[:80]


def test_cloudflare_real_ip_trust_and_spoof_refusal():
    """N2: CF-Connecting-IP is trusted ONLY from Cloudflare ranges (e.g. 173.245.48.5)."""
    router = InstanceRouter(instance_domain="inst.test")
    # 1. From Cloudflare IP -> trusted
    assert router.resolve_client_ip("173.245.48.5", [("CF-Connecting-IP", "203.0.113.19")]) == "203.0.113.19"
    # 2. From non-Cloudflare IP (stranger trying to spoof) -> NOT trusted, uses peer IP
    assert router.resolve_client_ip("198.51.100.1", [("CF-Connecting-IP", "203.0.113.19")]) == "198.51.100.1"


def test_backend_dripping_bytes_triggers_total_deadline_timeout_504():
    """N3: Backend that drips bytes slowly exceeds total deadline (e.g. 0.2s) -> 504."""
    async def run():
        async def mock_dripping_backend(reader, writer):
            await reader.read(4096)
            # Drip 1 byte every 50ms
            try:
                for _ in range(20):
                    writer.write(b"X")
                    await writer.drain()
                    await asyncio.sleep(0.05)
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        server = await asyncio.start_server(mock_dripping_backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{
            "id": inst_id,
            "protocol": "http",
            "target_ip": "127.0.0.1",
            "target_port": port,
            "response_timeout_seconds": 0.15,
        }])

        reader = asyncio.StreamReader()
        reader.feed_data(f"GET / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
        reader.feed_eof()
        writer = _Writer()

        await handle_client(reader, writer, router)
        server.close()
        await asyncio.wait_for(server.wait_closed(), timeout=2.0)
        return writer.data

    data = asyncio.run(run())
    assert b"504 Gateway Timeout" in data or data.startswith(b"HTTP/1.1 504"), data[:80]


def test_hostile_many_slow_header_connections():
    """N2 Hostile Client: 80 concurrent slow header connections must time out cleanly and free slots."""
    async def run():
        router = InstanceRouter(instance_domain="inst.test")
        tasks = []

        async def slow_header_client(idx: int):
            reader = asyncio.StreamReader()
            reader.feed_data(b"GET / HTTP/1.1\r\nHost: inst.test\r\nX-Slow: ")
            # Never send trailing newline / EOF
            writer = _Writer()
            writer.get_extra_info = lambda k, d=None: (f"10.0.{idx // 40}.{idx % 40}", 10000 + idx) if k == "peername" else d
            await handle_client(reader, writer, router, header_timeout=0.05, body_timeout=0.1)
            return writer

        for i in range(80):
            tasks.append(asyncio.create_task(slow_header_client(i)))

        writers = await asyncio.gather(*tasks)
        # All 80 should time out with 408 and be marked closed
        assert len(writers) == 80
        assert all(w.closed for w in writers)
        assert all(w.data.startswith(b"HTTP/1.1 408") for w in writers)
        # Verify router connection slots fully recovered
        assert router._total_active_connections == 0
        assert len(router._active_connections_per_ip) == 0

    asyncio.run(run())


def test_hostile_many_slow_bodies():
    """N2 Hostile Client: 40 concurrent slow body connections must time out and restore reading budget."""
    async def run():
        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{
            "id": inst_id,
            "protocol": "http",
            "target_ip": "127.0.0.1",
            "target_port": 8080,
            "max_body_bytes": 1024 * 1024,
        }])

        tasks = []

        async def slow_body_client(idx: int):
            reader = asyncio.StreamReader()
            # Send complete headers declaring 200 KB body, but feed only 10 bytes and stall
            head = f"POST / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\nContent-Length: 200000\r\n\r\n".encode()
            reader.feed_data(head + b"1234567890")
            writer = _Writer()
            writer.get_extra_info = lambda k, d=None: (f"10.1.{idx // 20}.{idx % 20}", 20000 + idx) if k == "peername" else d
            await handle_client(reader, writer, router, header_timeout=0.2, body_timeout=0.05)
            return writer

        for i in range(40):
            tasks.append(asyncio.create_task(slow_body_client(i)))

        writers = await asyncio.gather(*tasks)
        assert len(writers) == 40
        assert all(w.closed for w in writers)
        assert all(w.data.startswith(b"HTTP/1.1 408") for w in writers)
        # Verify reading budget and connection count fully returned to 0
        assert router._current_reading_bytes == 0
        assert router._total_active_connections == 0

    asyncio.run(run())


def test_hostile_many_connections_burst():
    """N2 Hostile Client: Burst of 1200 connections enforces 1000 max_total_connections."""
    async def run():
        router = InstanceRouter(instance_domain="inst.test", max_total_connections=1000)
        # Directly populate 1000 active connections
        router._total_active_connections = 1000

        # Now launch 50 new connections across different source IPs
        tasks = []
        async def burst_client(idx: int):
            reader = asyncio.StreamReader()
            reader.feed_data(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            reader.feed_eof()
            writer = _Writer()
            writer.get_extra_info = lambda k, d=None: (f"10.2.{idx // 10}.{idx % 10}", 30000 + idx) if k == "peername" else d
            await handle_client(reader, writer, router)
            return writer

        for i in range(50):
            tasks.append(asyncio.create_task(burst_client(i)))

        writers = await asyncio.gather(*tasks)
        # All 50 connections above limit must receive 503
        assert len(writers) == 50
        assert all(w.data.startswith(b"HTTP/1.1 503") for w in writers)

        # Release the 1000 connections
        router._total_active_connections = 0
        # Subsequent connection must be accepted
        reader = asyncio.StreamReader()
        reader.feed_data(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        reader.feed_eof()
        writer = _Writer()
        await handle_client(reader, writer, router)
        assert writer.data.startswith(b"HTTP/1.1 404")

    asyncio.run(run())






