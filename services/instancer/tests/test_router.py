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
            # Read request, but never respond; wait for the router to hang up, then close our side too
            # (integration audit: the old mock slept and never closed, so on Python 3.12, where wait_closed really waits, the test hung)
            await reader.read(4096)
            try:
                await asyncio.wait_for(reader.read(), 5)
            except asyncio.TimeoutError:
                pass
            finally:
                writer.close()

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
            # a router that leaves its connection to the instance open after the timeout keeps the server from closing: fail, do not hang
            await asyncio.wait_for(server.wait_closed(), 5)
            return writer.data
        finally:
            router_mod.IDLE_TIMEOUT_SECONDS = orig_timeout

    data = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 504"), data[:80]


def test_the_connection_to_the_instance_is_closed_by_the_router_when_it_gives_up():
    """A backend that never answers: after the 504 the router must have closed its own connection to the instance, at once, not leave it to the garbage collector.

    Integration audit: forward_stream closed the connection only on the success path; a timeout or an error left it open (a file descriptor per request
    that a hostile instance could pile up). The backend here is a spy, so only an explicit close() counts.
    """
    import services.instancer.router.router as router_mod

    class _SpyWriter:
        def __init__(self):
            self.closed = False

        def write(self, chunk):
            pass

        async def drain(self):
            pass

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    spy = _SpyWriter()

    async def run():
        async def fake_open_connection(host, port):
            return asyncio.StreamReader(), spy  # a reader that never gets a byte

        inst_id = "0123456789abcdef0123456789abcdef"
        router = InstanceRouter(instance_domain="inst.test")
        router.update_routes([{"id": inst_id, "protocol": "http", "target_ip": "127.0.0.1", "target_port": 9}])
        orig_timeout, orig_open = router_mod.IDLE_TIMEOUT_SECONDS, router_mod.asyncio.open_connection
        router_mod.IDLE_TIMEOUT_SECONDS = 0.2
        router_mod.asyncio.open_connection = fake_open_connection
        try:
            reader = asyncio.StreamReader()
            reader.feed_data(f"GET / HTTP/1.1\r\nHost: {inst_id}.inst.test\r\n\r\n".encode())
            reader.feed_eof()
            writer = _Writer()
            await handle_client(reader, writer, router)
            return writer.data, router._total_active_connections
        finally:
            router_mod.IDLE_TIMEOUT_SECONDS = orig_timeout
            router_mod.asyncio.open_connection = orig_open

    data, still_counted = asyncio.run(run())
    assert data.startswith(b"HTTP/1.1 504"), data[:80]
    assert spy.closed, "the router left its connection to the instance open"
    assert still_counted == 0, "and the slot it held was not given back"


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
    """Client declared Content-Length but never sends body bytes -> times out with 408."""
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 50\r\n\r\npart")
        # Do not feed EOF
        writer = _Writer()
        await handle_client(reader, writer, InstanceRouter(instance_domain="inst.test"), header_timeout=1.0, body_timeout=0.2)
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



