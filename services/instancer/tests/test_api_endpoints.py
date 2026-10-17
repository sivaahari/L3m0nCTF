import json
import threading
import time
import urllib.request
import urllib.error
import pytest

from services.instancer.manager import InstanceManager
from services.instancer.manifest import load_manifest_from_string
from services.instancer.server import create_instancer_server, create_metrics_server
from services.instancer.tests.test_instancer_core import MockDockerClient, MANIFEST_TOML, FLAG_EXAMPLE

INSTANCER_TOKEN = "test-instancer-token-abcdef1234567890"
ROUTER_TOKEN = "test-router-token-1234567890abcdef"


@pytest.fixture
def instancer_api(unused_tcp_port_factory):
    api_port = unused_tcp_port_factory()
    metrics_port = unused_tcp_port_factory()

    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=True)
    docker = MockDockerClient()
    mgr = InstanceManager(manifest=manifest, docker_client=docker, instance_domain="inst.test", dev_mode=True)

    server = create_instancer_server(
        host="127.0.0.1",
        port=api_port,
        manager=mgr,
        instancer_token=INSTANCER_TOKEN,
        router_token=ROUTER_TOKEN,
    )
    m_server = create_metrics_server(
        host="127.0.0.1",
        port=metrics_port,
        manager=mgr,
    )

    t1 = threading.Thread(target=server.serve_forever, daemon=True)
    t2 = threading.Thread(target=m_server.serve_forever, daemon=True)
    t1.start()
    t2.start()
    time.sleep(0.05)

    base_url = f"http://127.0.0.1:{api_port}"
    metrics_url = f"http://127.0.0.1:{metrics_port}"

    yield {"base_url": base_url, "metrics_url": metrics_url, "manager": mgr}

    server.shutdown()
    m_server.shutdown()


def _request(url, method="GET", body=None, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            content = resp.read()
            return status, json.loads(content) if content else None, resp.headers
    except urllib.error.HTTPError as e:
        content = e.read()
        parsed = json.loads(content) if content else None
        return e.code, parsed, e.headers


def test_auth_token_handling(instancer_api):
    url = f"{instancer_api['base_url']}/v1/capacity"

    # Missing token -> 401
    status, data, _ = _request(url, method="GET", token=None)
    assert status == 401

    # Wrong token -> 401
    status, data, _ = _request(url, method="GET", token="wrong-token")
    assert status == 401

    # Right token -> 200
    status, data, _ = _request(url, method="GET", token=INSTANCER_TOKEN)
    assert status == 200
    assert "slots_total" in data


def test_router_token_for_routes_only(instancer_api):
    routes_url = f"{instancer_api['base_url']}/v1/routes"
    capacity_url = f"{instancer_api['base_url']}/v1/capacity"

    # Router token succeeds on /v1/routes
    status, data, _ = _request(routes_url, method="GET", token=ROUTER_TOKEN)
    assert status == 200
    assert "routes" in data

    # Router token fails on /v1/capacity
    status, data, _ = _request(capacity_url, method="GET", token=ROUTER_TOKEN)
    assert status == 401


def test_instance_crud_lifecycle(instancer_api):
    base = instancer_api["base_url"]

    # 1. Start instance
    body = {"team_id": 7, "challenge_id": 42, "slug": "demo-web", "flag": FLAG_EXAMPLE}
    status, data, _ = _request(f"{base}/v1/instances", method="POST", body=body, token=INSTANCER_TOKEN)
    assert status == 201
    inst = data["instance"]
    inst_id = inst["id"]
    assert inst["team_id"] == 7
    assert inst["challenge_id"] == 42
    assert inst["slug"] == "demo-web"

    # 2. Idempotent start -> 200
    status, data2, _ = _request(f"{base}/v1/instances", method="POST", body=body, token=INSTANCER_TOKEN)
    assert status == 200
    assert data2["instance"]["id"] == inst_id

    # 3. Get instance by id
    status, data3, _ = _request(f"{base}/v1/instances/{inst_id}", method="GET", token=INSTANCER_TOKEN)
    assert status == 200
    assert data3["instance"]["id"] == inst_id

    # 4. List instances by team
    status, data4, _ = _request(f"{base}/v1/instances?team_id=7", method="GET", token=INSTANCER_TOKEN)
    assert status == 200
    assert len(data4["instances"]) == 1

    # 5. Delete instance -> 204
    status, _, _ = _request(f"{base}/v1/instances/{inst_id}", method="DELETE", token=INSTANCER_TOKEN)
    assert status == 204

    # 6. Get deleted instance -> 404
    status, data_gone, _ = _request(f"{base}/v1/instances/{inst_id}", method="GET", token=INSTANCER_TOKEN)
    assert status == 404


def test_admin_kill(instancer_api):
    base = instancer_api["base_url"]
    _request(f"{base}/v1/instances", method="POST", body={"team_id": 1, "challenge_id": 1, "slug": "demo-web", "flag": FLAG_EXAMPLE}, token=INSTANCER_TOKEN)
    _request(f"{base}/v1/instances", method="POST", body={"team_id": 2, "challenge_id": 1, "slug": "demo-web", "flag": FLAG_EXAMPLE}, token=INSTANCER_TOKEN)

    # Kill all for challenge 1
    status, data, _ = _request(f"{base}/v1/admin/kill", method="POST", body={"challenge_id": 1}, token=INSTANCER_TOKEN)
    assert status == 200
    assert data["stopped"] == 2


def test_healthz_and_metrics(instancer_api):
    m_base = instancer_api["metrics_url"]

    # Healthz
    req = urllib.request.Request(f"{m_base}/healthz")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data.get("status") == "ok"

    # Metrics
    req = urllib.request.Request(f"{m_base}/metrics")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        text = resp.read().decode()
        assert "l3mon_instances_active" in text


# ---- added by the integration audit (2026-10-18) ---------------------------------------------------------------------------------------------

def _raw(url, authorization):
    """A GET with an exact Authorization header (urllib refuses non-Latin-1 header text, so the header is sent as raw bytes over a socket)."""
    import socket
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    with socket.create_connection((parts.hostname, parts.port), timeout=3) as sock:
        sock.sendall(b"GET " + parts.path.encode() + b" HTTP/1.1\r\nHost: x\r\nAuthorization: " + authorization + b"\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return int(data.split(b" ", 2)[1])


def test_a_non_ascii_token_is_refused_with_401_and_does_not_break_the_handler(instancer_api):
    """compare_digest on two str raises TypeError for non-ASCII text: the handler thread died and the caller got no answer."""
    base = instancer_api["base_url"]
    assert _raw(base + "/v1/capacity", "Bearer tøken-ünï".encode("utf-8")) == 401
    assert _raw(base + "/v1/capacity", b"Bearer " + INSTANCER_TOKEN.encode()) == 200  # the server is still fine


def test_an_empty_router_token_never_matches_an_empty_bearer(tmp_path):
    """If ROUTER_TOKEN were empty, `Authorization: Bearer ` (an empty token) would equal it and open /v1/routes. An empty token matches nothing."""
    from services.instancer.tests.test_instancer_core import MockDockerClient as Mock
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=True)
    mgr = InstanceManager(manifest=manifest, docker_client=Mock(), instance_domain="inst.test", dev_mode=True)
    server = create_instancer_server(host="127.0.0.1", port=port, manager=mgr, instancer_token=INSTANCER_TOKEN, router_token="")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        assert _raw(f"http://127.0.0.1:{port}/v1/routes", b"Bearer ") == 401
        assert _raw(f"http://127.0.0.1:{port}/v1/routes", b"Bearer  ") == 401
        assert _raw(f"http://127.0.0.1:{port}/v1/routes", b"Bearer " + INSTANCER_TOKEN.encode()) == 200
    finally:
        server.shutdown()
