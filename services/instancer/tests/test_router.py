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
