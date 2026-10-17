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
