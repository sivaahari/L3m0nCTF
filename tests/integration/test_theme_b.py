"""Integration tests for Stream B: Registration, Verification and Studio Enrollment (SP6).

Validates the full player onboarding flow through the theme's templates and adapters.
"""
from __future__ import annotations

import http.client
import os
import re

import pytest

HOST = os.environ.get("L3MON_TEST_HOST", "play.l3m0nctf.xyz")
PORT = int(os.environ.get("L3MON_TEST_PORT", "8080"))
REG_H = 'id="reg-h"'

pytestmark = pytest.mark.integration


def get(path: str):
    """One GET through nginx. Returns (status, header pairs, body text)."""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    conn.request("GET", path, headers={"Host": HOST})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    pairs = resp.getheaders()
    conn.close()
    return resp.status, pairs, data


def values(pairs, name: str) -> list[str]:
    return [v for k, v in pairs if k.lower() == name.lower()]


def test_the_register_account_page_is_the_theme_through_nginx():
    status, headers, body = get("/register")
    assert status == 200
    assert REG_H in body
    assert "Create your account" in body
    assert "ACCOUNT · CH 000" in body
    assert "Step 1 of 3: Account" in body
    for name in ("X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy"):
        assert len(values(headers, name)) == 1, f"{name} is sent once"


def test_the_register_page_names_theme_static_files():
    _, _, body = get("/register")
    links = sorted(set(re.findall(r'(?:href|src)="(/themes/l3mon/static/[^"]+)"', body)))
    assert len(links) >= 3, links
    for link in links:
        status, _, _ = get(link)
        assert status == 200, link


def test_unauthenticated_studio_availability_is_refused_through_nginx():
    status, _, body = get("/api/v1/l3mon/teams/available?name=SomeTeam")
    assert status == 401
    assert "auth_required" in body


def test_unauthenticated_team_enrollment_redirects_to_login():
    status, headers, _ = get("/team")
    assert status in (302, 303)
    loc = values(headers, "Location")
    assert loc and "/login" in loc[0]
