"""The Stream C player pages (Scoreboard, Guide, Reset, Login) and their static files, through nginx.

Needs the running development stack after the image has been rebuilt with the theme in it (docs/deploy/theme-slices.md).
Same conventions as test_theme.py and test_release.py: nginx on 127.0.0.1:8080, the host name the platform uses.

Run:  python -m pytest tests/integration/test_theme_c.py -q
"""
from __future__ import annotations

import http.client
import json
import os
import re
import secrets as pysecrets

import pytest

import test_stack as t

pytestmark = pytest.mark.integration

HOST = os.environ.get("L3MON_TEST_HOST", "play.l3m0nctf.xyz")
PORT = int(os.environ.get("L3MON_TEST_PORT", "8080"))

SECURITY_HEADERS = (
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Resource-Policy",
)


def get(path: str, headers: dict[str, str] | None = None):
    """One GET through nginx. Returns (status, header pairs, body text)."""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    req_headers = {"Host": HOST}
    if headers:
        req_headers.update(headers)
    conn.request("GET", path, headers=req_headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    pairs = resp.getheaders()
    conn.close()
    return resp.status, pairs, data


def header_value(pairs, name: str) -> str | None:
    for k, v in pairs:
        if k.lower() == name.lower():
            return v
    return None


def values(pairs, name: str) -> list[str]:
    return [v for k, v in pairs if k.lower() == name.lower()]


def admin(method: str, path: str, body=None):
    headers = {"Authorization": f"Token {t.secret('PRESET_ADMIN_TOKEN')}", "Content-Type": "application/json"}
    status, _, data = t.request(path, method=method, headers=headers, body=json.dumps(body).encode() if body is not None else None)
    try:
        return status, json.loads(data)
    except ValueError:
        return status, data


def test_the_scoreboard_redirects_signed_out_visitor_when_private():
    status, headers, _ = get("/scoreboard")
    assert status == 302
    loc = header_value(headers, "Location") or ""
    assert "/login" in loc and "next=" in loc


def test_the_guide_page_redirects_signed_out_visitor_through_nginx():
    status, headers, _ = get("/guide")
    assert status == 302
    loc = header_value(headers, "Location") or ""
    assert "/login" in loc and "next=" in loc


def test_the_reset_password_page_is_the_theme_through_nginx_with_security_headers():
    for path in ("/reset_password", "/reset"):
        status, headers, body = get(path)
        assert status == 200, f"Expected 200 on {path}"
        assert ('name="email"' in body or 'f-email' in body or "pg-register" in body)
        for name in SECURITY_HEADERS:
            assert len(values(headers, name)) == 1, f"{name} is sent once on {path}"


def test_the_reset_password_page_names_static_files_that_nginx_serves():
    _, _, body = get("/reset_password")
    links = sorted(set(re.findall(r'(?:href|src)="(/themes/l3mon/static/[^"]+)"', body)))
    assert len(links) >= 2, links
    for link in links:
        status, _, _ = get(link)
        assert status == 200, link


def test_the_scoreboard_and_guide_render_theme_for_authenticated_player():
    name = f"tcplay-{pysecrets.token_hex(4)}"
    password = pysecrets.token_urlsafe(18)
    status, out = admin("POST", "/api/v1/users", {"name": name, "email": f"{name}@example.com", "password": password, "type": "user", "verified": True})
    assert status == 200, out
    user_id = out["data"]["id"]

    session = t.Session()
    ans = session.open("/login", {"name": name, "password": password, "_submit": "Submit", "nonce": session.nonce("/login")})
    assert ans.status == 302

    ans = session.open("/teams/new", {"name": f"{name}-studio", "password": pysecrets.token_urlsafe(12), "_submit": "Create", "nonce": session.nonce("/teams/new")})
    assert ans.status == 302

    status, who = admin("GET", f"/api/v1/users/{user_id}")
    team_id = who["data"]["team_id"]

    try:
        # Scoreboard
        resp = session.open("/scoreboard")
        assert resp.status == 200
        body = resp.read().decode("utf-8", "replace")
        assert ('id="sb-list"' in body or 'id="sb-banner"' in body or "pg-scoreboard" in body)

        sb_links = sorted(set(re.findall(r'(?:href|src)="(/themes/l3mon/static/[^"]+)"', body)))
        assert len(sb_links) >= 2, sb_links
        for link in sb_links:
            st, _, _ = get(link)
            assert st == 200, link

        # Guide
        resp_guide = session.open("/guide")
        assert resp_guide.status == 200
        g_body = resp_guide.read().decode("utf-8", "replace")
        assert ("pg-guide" in g_body or "gd-score" in g_body or "gd-head" in g_body)

        g_links = sorted(set(re.findall(r'(?:href|src)="(/themes/l3mon/static/[^"]+)"', g_body)))
        assert len(g_links) >= 2, g_links
        for link in g_links:
            st, _, _ = get(link)
            assert st == 200, link

    finally:
        # Clean up
        if team_id:
            admin("DELETE", f"/api/v1/teams/{team_id}")
        admin("DELETE", f"/api/v1/users/{user_id}")
