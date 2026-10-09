"""Tests for the hardening round that followed the independent audit of the platform foundation (private repo, security/).

Same prerequisites and runner as test_stack.py. The "denied" tests restart nginx with a list of organisers' addresses that
matches nobody, so every real request counts as coming from outside, and restore the normal list afterwards.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys

import pytest

import test_stack as t

pytestmark = pytest.mark.integration

NGINX_ERROR_PAGE = b"<center>nginx</center>"
FIXTURE = t.ROOT / "tests" / "integration" / "fixtures" / "admin-nets-nobody.conf"
SNIPPETS = t.ROOT / "deploy" / "nginx" / "snippets"


def refused_by_nginx(status: int, body: bytes, code: int) -> bool:
    return status == code and NGINX_ERROR_PAGE in body


def routes(filename: str) -> list[tuple[str, str]]:
    """(method, a concrete path) for every entry of a generated map file."""
    out = []
    for line in (SNIPPETS / filename).read_text(encoding="utf-8").splitlines():
        m = re.match(r'^"~\^([A-Z]+):(.+?)/\?\$" 1;$', line)
        if m:
            path = re.sub(r"\(\?!\(\?:[^)]*\)\(\?:/\|\$\)\)", "", m.group(2))  # drop the "not me" look-aheads
            path = path.replace("[0-9]+", "1").replace("[^/]+", "x")
            out.append((m.group(1), path))
    return out


@pytest.fixture(scope="module")
def outside():
    """nginx restarted so that nobody counts as an organiser: every request below comes 'from outside'."""
    t.compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "nginx", env={"ADMIN_NETS_FILE": str(FIXTURE)})
    t.wait_healthy("nginx", 60)
    yield
    t.compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "nginx")
    t.wait_healthy("nginx", 60)


# ------------------------------------------------ the administrator surface (audit H1)

def test_the_admin_pages_are_refused_to_addresses_outside_the_list(outside):
    for path in ("/admin", "/admin/config", "/admin/challenges"):
        status, _, body = t.request(path)
        assert refused_by_nginx(status, body, 403), (path, status)


def test_anything_with_an_authorization_header_is_refused_outside_the_list(outside):
    token = t.secret("PRESET_ADMIN_TOKEN")
    for path in ("/api/v1/users/me", "/api/v1/challenges", "/login", "/"):
        status, _, body = t.request(path, headers={"Authorization": f"Token {token}", "Content-Type": "application/json"})
        assert refused_by_nginx(status, body, 403), (path, status)


def test_every_route_ctfd_guards_for_administrators_is_refused_outside_the_list(outside):
    admin_routes = routes("api-admin-routes.conf")
    assert len(admin_routes) > 50, "the generated list is suspiciously short"
    wrong = []
    for method, path in admin_routes:
        status, _, body = t.request(path, method=method, headers={"Content-Type": "application/json"}, body=b"{}" if method != "GET" else None)
        if not refused_by_nginx(status, body, 403):
            wrong.append((method, path, status))
    assert not wrong, f"reachable from outside: {wrong}"


def test_a_write_nobody_listed_is_refused_outside_the_list_so_new_routes_fail_closed(outside):
    for method, path in (("POST", "/api/v1/brand-new-thing"), ("DELETE", "/api/v1/anything/1"), ("PATCH", "/api/v1/challenges/attempt/x"), ("PUT", "/api/v1/configs")):
        status, _, body = t.request(path, method=method, headers={"Content-Type": "application/json"}, body=b"{}")
        assert refused_by_nginx(status, body, 403), (method, path, status)


def test_the_writes_a_player_makes_still_reach_ctfd_from_anywhere(outside):
    players = routes("api-player-writes.conf")
    assert len(players) >= 8
    for method, path in players:
        status, _, body = t.request(path, method=method, headers={"Content-Type": "application/json"}, body=b"{}")
        assert NGINX_ERROR_PAGE not in body, (method, path, status)  # CTFd answers (it will say "sign in"), nginx does not refuse


def test_ordinary_pages_and_public_reads_still_work_from_anywhere(outside):
    for path in ("/login", "/api/v1/challenges", "/api/v1/scoreboard", "/api/v1/users/me", "/api/v1/teams", "/api/v1/users", "/ctftime/standings.json"):
        status, _, body = t.request(path)
        assert NGINX_ERROR_PAGE not in body, (path, status)
    assert t.request("/login")[0] == 200
    status, _, body = t.request("/login", method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, body=b"name=a&password=b")
    assert NGINX_ERROR_PAGE not in body  # an ordinary form post is CTFd's business, not nginx's


# ------------------------------------------------ the crew's release API (SP3 part 3.2)

CREW_CALLS = [
    ("GET", "/api/v1/l3mon/admin/release"),
    ("PUT", "/api/v1/l3mon/admin/release"),
    ("PUT", "/api/v1/l3mon/admin/programmes"),
    ("POST", "/api/v1/l3mon/admin/release"),
    ("DELETE", "/api/v1/l3mon/admin/release"),
    ("GET", "/api/v1/l3mon/admin"),
    ("GET", "/api/v1/l3mon/admin/anything/else"),
]


def test_the_crews_release_api_is_refused_outside_the_list_for_every_method(outside):
    """api-player-writes-l3mon.conf lets a signed-in player write anywhere under /api/v1/l3mon/; the crew's prefix must win."""
    for method, path in CREW_CALLS:
        status, _, body = t.request(path, method=method, headers={"Content-Type": "application/json"}, body=b"{}" if method != "GET" else None)
        assert refused_by_nginx(status, body, 403), (method, path, status)


def test_the_crews_release_page_is_refused_outside_the_list(outside):
    for path in ("/admin/l3mon/release", "/plugins/l3mon_release/assets/release.js"):
        status, _, body = t.request(path)
        if path.startswith("/admin"):
            assert refused_by_nginx(status, body, 403), (path, status)
        else:
            assert status in (302, 403), (path, status)  # CTFd sends a visitor to sign in


def test_a_players_own_writes_under_the_l3mon_prefix_still_reach_ctfd_from_anywhere(outside):
    status, _, body = t.request("/api/v1/l3mon/board/attempt", method="POST", headers={"Content-Type": "application/json"}, body=b"{}")
    assert NGINX_ERROR_PAGE not in body, status  # CTFd answers (there is no such route yet), nginx does not refuse it


# ------------------------------------------------ multipart bodies (audit H2)

def test_multipart_bodies_are_refused_before_ctfd_parses_them_outside_the_list(outside):
    body = b"--b\r\nContent-Disposition: form-data; name=\"nonce\"\r\n\r\nx\r\n--b--\r\n"
    for path in ("/login", "/register", "/api/v1/challenges/attempt", "/settings"):
        status, _, answer = t.request(path, method="POST", headers={"Content-Type": "multipart/form-data; boundary=b"}, body=body)
        assert refused_by_nginx(status, answer, 415), (path, status)


def test_a_big_anonymous_upload_is_refused_before_it_is_read_outside_the_list(outside):
    big = b"\r\n" + b"x" * (5 * 1024 * 1024)
    status, _, body = t.request("/api/v1/files", method="POST", headers={"Content-Type": "multipart/form-data; boundary=b"}, body=big)
    assert refused_by_nginx(status, body, 403), status


# ------------------------------------------------ the list itself (audit M3)

def test_the_generated_admin_route_lists_match_ctfds_source_in_the_image():
    sys.path.insert(0, str(t.ROOT / "tools"))
    from l3mon import api_rules

    assert api_rules.stale("l3mon/ctfd:dev") == [], "run: cd tools && python -m l3mon api-rules generate"


def test_the_production_example_list_holds_no_private_loopback_or_link_local_range():
    for line in (SNIPPETS / "admin-nets.production.example.conf").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        net = ipaddress.ip_network(line.split()[0], strict=False)
        assert not (net.is_loopback or net.is_link_local or net.overlaps(ipaddress.ip_network("10.0.0.0/8")) or net.overlaps(ipaddress.ip_network("172.16.0.0/12")) or net.overlaps(ipaddress.ip_network("192.168.0.0/16")) or net.overlaps(ipaddress.ip_network("fc00::/7"))), line


# ------------------------------------------------ logs (audit M1)

def test_container_logs_are_rotated():
    for service in t.SERVICES:
        cfg = t.inspect(service)["HostConfig"]["LogConfig"]
        assert cfg["Type"] == "json-file" and cfg["Config"].get("max-size") and cfg["Config"].get("max-file"), (service, cfg)


def test_the_logs_never_hold_query_strings_or_the_tokens_in_reset_and_confirmation_links():
    marker = f"auditprobe{os.getpid()}"
    t.request(f"/login?{marker}=q1")
    t.request("/reset_password/reset-token-in-the-path-" + marker)
    t.request("/confirm/confirm-token-in-the-path-" + marker)
    logs = t.compose("logs", "--no-color", "--tail", "200").stdout
    assert marker not in logs, "a query string or a token reached a log"
    assert "/reset_password/[token]" in logs and "/confirm/[token]" in logs


# ------------------------------------------------ limits (audit M2)

def test_one_address_can_load_pages_at_campus_speed_without_being_limited():
    codes = [t.request("/login")[0] for _ in range(150)]
    assert 429 not in codes, f"limited after {codes.index(429)} requests"


# ------------------------------------------------ accounts (audit L5)

def test_the_database_root_account_cannot_sign_in_from_the_network():
    code = (
        "import os, pymysql" + chr(10)
        + "try:" + chr(10)
        + "    pymysql.connect(host='db', user='root', password=os.environ['ROOT_PW'], connect_timeout=5)" + chr(10)
        + "    print('CONNECTED')" + chr(10)
        + "except pymysql.err.OperationalError as e:" + chr(10)
        + "    print('REFUSED', e.args[0])" + chr(10)
    )
    done = subprocess.run(["docker", "exec", "-e", "ROOT_PW", t.container_id("ctfd"), "/opt/venv/bin/python", "-c", code], capture_output=True, text=True, timeout=60, env={**os.environ, "ROOT_PW": t.secret("DATABASE_ROOT_PASSWORD")})
    assert "REFUSED" in done.stdout, done.stdout + done.stderr
    assert "CONNECTED" not in done.stdout


def test_the_redis_password_is_not_in_any_process_argument_list():
    for service in ("cache",):
        out = subprocess.run(["docker", "top", t.container_id(service), "-eo", "args"], capture_output=True, text=True).stdout
        assert t.secret("REDIS_PASSWORD") not in out


# ------------------------------------------------ the public CTFtime feeds (plugin l3mon_ctftime)

def test_the_ctftime_feeds_are_public_json_with_no_cookie_and_one_shared_cache_entry():
    status, pairs, body = t.request("/ctftime/standings.json")
    assert status == 200
    data = json.loads(body)
    assert list(data) == ["standings"] and isinstance(data["standings"], list)
    for row in data["standings"]:
        assert set(row) == {"pos", "team", "score"}
    assert not t.header_values(pairs, "Set-Cookie"), "a cookie would be cached with the answer or leak a session"
    assert not any("cookie" in v.lower() for v in t.header_values(pairs, "Vary")), "the feed is the same for everyone"
    cache = " ".join(t.header_values(pairs, "Cache-Control"))
    assert "public" in cache and "max-age=15" in cache
    assert t.header_values(pairs, "Content-Type")[0].startswith("application/json")
    # the final standings are not published until the event has ended and an organiser says so
    status, pairs, body = t.request("/ctftime/final-standings.json")
    assert status == 404 and json.loads(body)["error"] == "not_found"
    assert "no-store" in " ".join(t.header_values(pairs, "Cache-Control"))
