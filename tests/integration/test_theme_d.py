"""Integration tests for Stream D: Studio (/team) and Settings (/settings) through nginx.

Verifies:
- Visitor access is redirected to /login
- Security headers are properly applied through nginx
- Static bundles (js.account) are served through nginx
- Settings page rendering and player access
- Studio page rendering for captain, invite links, team details update (POST /team)
- Captain handover (POST /team/members/<id>/captain)
- Settings page rendering and username update (POST /settings)

Run:
    python -m pytest tests/integration/test_theme_d.py -v
"""
from __future__ import annotations

import json
import os
import re
import secrets as pysecrets
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import tests.integration.test_stack as t

pytestmark = pytest.mark.integration

HOST = t.HOST
PORT = t.PORT


def admin(method: str, path: str, body: dict | None = None):
    headers = {
        "Authorization": f"Token {t.secret('PRESET_ADMIN_TOKEN')}",
        "Content-Type": "application/json",
    }
    status, _, data = t.request(
        path,
        method=method,
        headers=headers,
        body=json.dumps(body).encode() if body is not None else None,
    )
    try:
        return status, json.loads(data)
    except ValueError:
        return status, data


def make_player(name: str) -> tuple[int, str]:
    password = pysecrets.token_urlsafe(18)
    status, out = admin("POST", "/api/v1/users", {
        "name": name,
        "email": f"{name}@example.com",
        "password": password,
        "type": "user",
        "verified": True,
    })
    assert status == 200, out
    user_id = out["data"]["id"]
    return user_id, password


def login_player(name: str, password: str) -> t.Session:
    session = t.Session()
    answer = session.open("/login", {
        "name": name,
        "password": password,
        "_submit": "Submit",
        "nonce": session.nonce("/login"),
    })
    assert answer.status == 302, f"sign-in failed: {answer.status}"
    return session


def test_visitor_redirected_to_login():
    for path in ("/team", "/settings"):
        status, pairs, _ = t.request(path)
        assert status in (302, 303), f"{path} did not redirect"
        locations = [v for k, v in pairs if k.lower() == "location"]
        assert locations and "/login" in locations[0]


def test_security_headers_and_account_bundle_through_nginx():
    # Sign in as a test player
    uname = f"u_sec_{pysecrets.token_hex(4)}"
    _, pwd = make_player(uname)
    session = login_player(uname, pwd)

    resp = session.open("/settings")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "pg-settings" in body
    assert 'id="st-h"' in body

    # Check security headers
    headers = dict(resp.headers)
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") in ("DENY", "SAMEORIGIN")

    # Find account bundle script tag
    match = re.search(r'src="(/themes/l3mon/static/account\.[0-9a-f]+\.js)"', body)
    assert match, f"account bundle not found in settings HTML: {body[:500]}"
    bundle_path = match.group(1)

    # Static bundle is served directly through nginx
    status, _, _ = t.request(bundle_path)
    assert status == 200, f"nginx failed to serve {bundle_path}"


def test_player_without_studio_can_access_settings():
    uname = f"u_solo_{pysecrets.token_hex(4)}"
    _, pwd = make_player(uname)
    session = login_player(uname, pwd)

    # On settings page
    resp = session.open("/settings")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "pg-settings" in body
    assert uname in body


def test_studio_captain_details_update_and_handover():
    uname1 = f"u_cap1_{pysecrets.token_hex(4)}"
    uid1, pwd1 = make_player(uname1)
    s1 = login_player(uname1, pwd1)

    tname = f"Studio {pysecrets.token_hex(4)}"
    tpwd = pysecrets.token_urlsafe(12)
    res_create = s1.open("/teams/new", {
        "name": tname,
        "password": tpwd,
        "_submit": "Create",
        "nonce": s1.nonce("/teams/new"),
    })
    assert res_create.status == 302, f"team creation failed: {res_create.status}"

    # Captain 1 visits /team
    resp = s1.open("/team")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "pg-team" in body
    assert 'id="tm-h"' in body
    assert tname in body
    assert "/teams/invite?code=" in body

    # Captain 1 updates studio details
    nonce = s1.nonce("/team")
    new_team_name = f"Renamed {pysecrets.token_hex(4)}"
    res_update = s1.open("/team", {
        "nonce": nonce,
        "name": new_team_name,
        "country": "DE",
        "affiliation": "Lemon Security Group",
    })
    assert res_update.status in (302, 303)

    follow = s1.open("/team")
    follow_body = follow.read().decode("utf-8")
    assert "Your changes are saved." in follow_body
    assert new_team_name in follow_body
    assert "Lemon Security Group" in follow_body

    # Add second member to team via /teams/join
    uname2 = f"u_mem2_{pysecrets.token_hex(4)}"
    uid2, pwd2 = make_player(uname2)
    s2 = login_player(uname2, pwd2)
    res_join = s2.open("/teams/join", {
        "name": new_team_name,
        "password": tpwd,
        "_submit": "Join",
        "nonce": s2.nonce("/teams/join"),
    })
    assert res_join.status == 302, f"join team failed: {res_join.status}"

    # Captain 1 hands over the chair to Member 2
    nonce = s1.nonce("/team")
    res_handover = s1.open(f"/team/members/{uid2}/captain", {"nonce": nonce})
    assert res_handover.status in (302, 303)

    handover_body = s1.open("/team").read().decode("utf-8")
    assert "Done. The captain" in handover_body or "The captain&#39;s chair has been handed over" in handover_body

    # Old captain now gets 403 when trying to update details
    res_forbidden = s1.open("/team", {"nonce": s1.nonce("/team"), "name": "Illegal Update"})
    assert res_forbidden.status == 403


def test_settings_details_update():
    uname = f"u_edit_{pysecrets.token_hex(4)}"
    _, pwd = make_player(uname)
    session = login_player(uname, pwd)

    resp = session.open("/settings")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "pg-settings" in body
    assert "Confirmed." in body

    new_uname = f"renamed_{pysecrets.token_hex(4)}"
    nonce = session.nonce("/settings")
    res_save = session.open("/settings", {
        "nonce": nonce,
        "part": "details",
        "name": new_uname,
    })
    assert res_save.status in (302, 303)

    follow = session.open("/settings")
    follow_body = follow.read().decode("utf-8")
    assert "Your changes are saved." in follow_body
    assert new_uname in follow_body
