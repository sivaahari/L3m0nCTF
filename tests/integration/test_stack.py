"""Integration tests for the running compose stack (development environment).

Prerequisites (see docs/deploy/local.md):
    python -m l3mon secrets generate --dir .secrets        (from the tools folder: --dir ../.secrets)
    python -m l3mon config render config/event.example.toml --out deploy/compose/generated
    tools/compose.sh up -d --build --wait

Run:  python -m pytest tests/integration -q

Everything here talks to nginx on 127.0.0.1:8080 exactly as a browser behind Cloudflare would, and inspects the containers
with `docker inspect`. Standard library only. The one test that exhausts a rate limit restarts nginx afterwards so the
next run starts clean.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / ".secrets"
HOST = os.environ.get("L3MON_TEST_HOST", "play.l3m0nctf.xyz")
PORT = int(os.environ.get("L3MON_TEST_PORT", "8080"))
SERVICES = ("ctfd", "db", "cache", "nginx")

pytestmark = pytest.mark.integration


GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")  # on Windows, plain "bash" can be the WSL one


def compose(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, str(ROOT / "tools" / "compose.sh"), *args], capture_output=True, text=True, timeout=timeout, check=check)


def container_id(service: str) -> str:
    out = compose("ps", "-q", service).stdout.strip()
    assert out, f"service {service} is not running; start the stack first (tools/compose.sh up -d --wait)"
    return out.splitlines()[0]


def inspect(service: str) -> dict:
    out = subprocess.run(["docker", "inspect", container_id(service)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)[0]


def secret(name: str) -> str:
    return (SECRETS / name).read_text(encoding="utf-8").strip()


def request(path: str, method: str = "GET", host: str | None = HOST, headers: dict | None = None, body: bytes | None = None):
    """One raw HTTP request to nginx. Returns (status, headers as a list of pairs, body bytes)."""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    h = {"Host": host} if host else {}
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    pairs = resp.getheaders()
    conn.close()
    return resp.status, pairs, data


def header_values(pairs, name: str) -> list[str]:
    return [v for k, v in pairs if k.lower() == name.lower()]


def wait_healthy(service: str, seconds: int = 120) -> None:
    end = time.time() + seconds
    while time.time() < end:
        state = inspect(service)["State"]
        if state.get("Status") == "running" and state.get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(2)
    raise AssertionError(f"{service} did not become healthy within {seconds} s")


class Session:
    """A browser-like session: cookies and the form token, over HTTP with the right Host header."""

    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())

    def open(self, path: str, data: dict | None = None, headers: dict | None = None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body, headers={"Host": HOST, **(headers or {})})
        try:
            return self.opener.open(req, timeout=20)
        except urllib.error.HTTPError as e:  # a redirect or an error status is a normal answer here
            return e

    def nonce(self, path: str) -> str:
        page = self.open(path).read().decode("utf-8", "replace")
        m = re.search(r"""name=["']nonce["'][^>]*value=["']([0-9a-f]+)["']|value=["']([0-9a-f]+)["'][^>]*name=["']nonce["']|csrfNonce['"]?\s*[:=]\s*['"]([0-9a-f]+)['"]""", page)
        assert m, f"no form token found on {path}"
        return next(g for g in m.groups() if g)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@pytest.fixture(scope="module", autouse=True)
def stack_is_up():
    for service in SERVICES:
        wait_healthy(service, 180)


# ---------------------------------------------------------------- through nginx

def test_health_routes_answer_through_nginx_with_no_cookie_and_no_cache():
    for path in ("/healthcheck", "/l3mon/healthz"):
        status, pairs, _ = request(path)
        assert status == 200, path
        assert not header_values(pairs, "Set-Cookie"), f"{path} must not set a cookie"
        assert "no-store" in " ".join(header_values(pairs, "Cache-Control")), path
    status, _, body = request("/l3mon/healthz")
    assert json.loads(body) == {"status": "ok", "plugin": "l3mon_core"}


def test_a_request_for_any_other_host_gets_no_answer_at_all():
    with pytest.raises((http.client.RemoteDisconnected, ConnectionError, http.client.BadStatusLine)):
        request("/login", host="evil.example")


def test_security_headers_are_present_once_and_the_server_says_nothing_about_itself():
    status, pairs, _ = request("/login")
    assert status == 200
    expected = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    for name, value in expected.items():
        assert header_values(pairs, name) == [value], name
    assert len(header_values(pairs, "Permissions-Policy")) == 1
    assert header_values(pairs, "Server") == ["nginx"], "no version number"
    assert not header_values(pairs, "X-Powered-By")
    # HSTS only when the visitor came over HTTPS, as Cloudflare would tell us
    assert not header_values(pairs, "Strict-Transport-Security")
    _, https_pairs, _ = request("/login", headers={"X-Forwarded-Proto": "https"})
    assert header_values(https_pairs, "Strict-Transport-Security") == ["max-age=31536000; includeSubDomains"]


def test_the_session_cookie_is_http_only_and_same_site():
    _, pairs, _ = request("/login")
    cookie = header_values(pairs, "Set-Cookie")[0]
    assert cookie.startswith("session=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_the_bare_address_goes_to_sign_in():
    status, pairs, _ = request("/")
    assert status == 303 and header_values(pairs, "Location")[0].endswith("/login")


def test_the_login_page_carries_the_event_name_and_our_theme():
    _, _, body = request("/login")
    html = body.decode("utf-8", "replace")
    assert "<title>L3m0nCTF 2026</title>" in html
    assert "themes/l3mon/static" in html


def test_a_made_up_forwarded_address_never_reaches_the_application():
    # nginx rewrites X-Forwarded-For from the connection it actually saw; a visitor cannot choose their address
    status, _, _ = request("/login", headers={"X-Forwarded-For": "203.0.113.9", "CF-Connecting-IP": "203.0.113.9"})
    assert status == 200  # and nothing in the application trusts either header: checked by the config below
    config = subprocess.run(["docker", "exec", container_id("nginx"), "nginx", "-T"], capture_output=True, text=True).stdout
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert "set_real_ip_from 173.245.48.0/20;" in config and "real_ip_recursive off;" in config


# -------------------------------------------------------- the preset administrator

def test_the_preset_admin_token_works_and_names_the_organiser():
    status, _, body = request("/api/v1/users/me", headers={"Authorization": f"Token {secret('PRESET_ADMIN_TOKEN')}", "Content-Type": "application/json"})
    assert status == 200
    data = json.loads(body)["data"]
    assert data["name"] == "organiser" and data["email"] == "organiser@l3m0nctf.xyz"


def test_a_wrong_token_is_refused():
    status, _, _ = request("/api/v1/users/me", headers={"Authorization": "Token ctfd_" + "0" * 64, "Content-Type": "application/json"})
    assert status in (401, 403)


def test_the_preset_admin_can_sign_in():
    s = Session()
    nonce = s.nonce("/login")
    r = s.open("/login", {"name": "organiser", "password": secret("PRESET_ADMIN_PASSWORD"), "_submit": "Submit", "nonce": nonce})
    assert r.status == 302, f"sign-in answered {r.status}"
    assert s.open("/admin/config").status == 200


def effective_settings(keys) -> dict:
    """What CTFd itself reports for each setting, read inside the container with the same environment CTFd starts with."""
    code = (
        "import json" + chr(10) + "from CTFd import create_app" + chr(10) + "from CTFd.utils import get_config" + chr(10)
        + "app = create_app()" + chr(10) + "with app.app_context():" + chr(10)
        + "    print('RESULT ' + json.dumps({k: get_config(k) for k in %r}))" % (list(keys),)
    )
    out = subprocess.run(["docker", "exec", container_id("ctfd"), "l3mon-run", "/opt/venv/bin/python", "-c", code], capture_output=True, text=True, timeout=120)
    lines = [l for l in out.stdout.splitlines() if l.startswith("RESULT ")]
    assert lines, f"no result: {out.stdout[-400:]} {out.stderr[-400:]}"
    return json.loads(lines[-1][len("RESULT "):])


def test_every_preset_setting_is_in_force_inside_ctfd():
    sys.path.insert(0, str(ROOT / "tools"))
    from l3mon import config as event_config

    wanted = event_config.preset_configs(event_config.load(ROOT / "config" / "event.example.toml"))
    actual = effective_settings(wanted)
    differences = {k: (wanted[k], actual.get(k)) for k in wanted if str(wanted[k]).lower() != str(actual.get(k)).lower()}
    assert not differences, f"setting, (wanted, CTFd reports): {differences}"


# --------------------------------------------------------------- the containers

def test_only_nginx_publishes_a_port_and_only_on_the_loopback_address():
    for service in SERVICES:
        ports = inspect(service)["NetworkSettings"]["Ports"] or {}
        published = {p: b for p, b in ports.items() if b}
        if service == "nginx":
            assert list(published) == ["8080/tcp"], published
            assert all(b["HostIp"] == "127.0.0.1" for b in published["8080/tcp"])
        else:
            assert not published, f"{service} publishes {published}"


@pytest.mark.parametrize("service", SERVICES)
def test_every_container_is_unprivileged_read_only_and_limited(service):
    c = inspect(service)
    host = c["HostConfig"]
    user = c["Config"]["User"]
    assert user and user.split(":")[0] not in ("0", "root"), f"{service} runs as '{user}'"
    assert host["ReadonlyRootfs"] is True
    assert host["Privileged"] is False
    assert "ALL" in (host["CapDrop"] or []) and not host["CapAdd"]
    assert any("no-new-privileges" in o for o in (host["SecurityOpt"] or []))
    assert host["Memory"] > 0 and host["PidsLimit"] and host["PidsLimit"] > 0
    assert host["NetworkMode"] != "host" and not host.get("PidMode") and not host.get("IpcMode") in ("host",)
    binds = " ".join(host.get("Binds") or [])
    assert "docker.sock" not in binds


def test_the_database_and_the_cache_are_only_on_the_internal_network():
    for service in ("db", "cache"):
        nets = set(inspect(service)["NetworkSettings"]["Networks"])
        assert nets == {"l3mon_internal"}, (service, nets)
    internal = json.loads(subprocess.run(["docker", "network", "inspect", "l3mon_internal"], capture_output=True, text=True, check=True).stdout)[0]
    assert internal["Internal"] is True


def test_the_code_cannot_be_changed_by_the_running_process_but_uploads_can_be_written():
    cid = container_id("ctfd")
    deny = subprocess.run(["docker", "exec", cid, "sh", "-c", "touch /opt/CTFd/CTFd/plugins/l3mon_core/x 2>&1"], capture_output=True, text=True)
    assert deny.returncode != 0
    allow = subprocess.run(["docker", "exec", cid, "sh", "-c", "touch /var/uploads/.probe && rm /var/uploads/.probe"], capture_output=True, text=True)
    assert allow.returncode == 0, allow.stderr


def test_logs_hold_no_secret():
    logs = compose("logs", "--no-color").stdout + compose("logs", "--no-color").stderr
    for name in ("SECRET_KEY", "DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "REDIS_PASSWORD", "PRESET_ADMIN_PASSWORD", "PRESET_ADMIN_TOKEN", "FLAG_HMAC_SECRET"):
        assert secret(name) not in logs, f"the value of {name} appears in the logs"


def test_no_secret_is_visible_in_the_container_configuration():
    for service in SERVICES:
        text = json.dumps(inspect(service)["Config"]["Env"])
        for name in ("SECRET_KEY", "DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "REDIS_PASSWORD", "PRESET_ADMIN_PASSWORD", "PRESET_ADMIN_TOKEN"):
            assert secret(name) not in text, f"{name} is visible in docker inspect of {service}"


# ---------------------------------------------------------------- state and limits

def test_data_survives_a_restart_of_the_application():
    token = {"Authorization": f"Token {secret('PRESET_ADMIN_TOKEN')}", "Content-Type": "application/json"}
    route = f"it-survives-{int(time.time())}"
    payload = json.dumps({"title": "Restart marker", "route": route, "content": "<p>marker</p>", "format": "html", "draft": False, "hidden": False}).encode()
    status, _, body = request("/api/v1/pages", method="POST", headers=token, body=payload)
    assert status == 200, body
    page_id = json.loads(body)["data"]["id"]
    compose("restart", "ctfd")
    wait_healthy("ctfd", 180)
    status, _, body = request(f"/api/v1/pages/{page_id}", headers=token)
    assert status == 200 and json.loads(body)["data"]["route"] == route
    request(f"/api/v1/pages/{page_id}", method="DELETE", headers=token)


def test_zz_repeated_sign_in_attempts_from_one_address_are_slowed_down_and_nginx_is_reset_afterwards():
    codes = []
    s = Session()
    for _ in range(20):
        r = s.open("/login", {"name": "nobody", "password": "wrong-password", "_submit": "Submit", "nonce": s.nonce("/login")})
        codes.append(r.status)
        if r.status == 429:
            break
    try:
        assert 429 in codes, f"no limit was hit: {codes}"
    finally:
        compose("restart", "nginx")  # forget the counters, so the next run starts clean
        wait_healthy("nginx", 60)
