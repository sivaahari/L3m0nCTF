"""The branded sign-in page and the theme's static files, through nginx (SP6 slice 1).

Needs the running development stack after the image has been rebuilt with the theme in it (docs/deploy/theme-slices.md).
Same conventions as test_stack.py: nginx on 127.0.0.1:8080, the host name the platform uses, standard library only.

Run:  python -m pytest tests/integration/test_theme.py -q
"""
from __future__ import annotations

import http.client
import os
import re

import pytest

HOST = os.environ.get("L3MON_TEST_HOST", "play.l3m0nctf.xyz")
PORT = int(os.environ.get("L3MON_TEST_PORT", "8080"))
SIGN_IN = 'id="login-h"'  # the sign-in heading of the theme's own page; CTFd's core sign-in page has none

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


def test_the_sign_in_page_is_the_theme_through_nginx_with_each_security_header_once():
    status, headers, body = get("/login")
    assert status == 200
    assert SIGN_IN in body
    for name in ("X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy", "Cross-Origin-Resource-Policy"):
        assert len(values(headers, name)) == 1, f"{name} is sent once"


def test_the_sign_in_page_names_static_files_that_nginx_serves():
    _, _, body = get("/login")
    links = sorted(set(re.findall(r'(?:href|src)="(/themes/l3mon/static/[^"]+)"', body)))
    assert len(links) == 3, links
    for link in links:
        status, _, _ = get(link)
        assert status == 200, link


def test_the_register_page_is_still_ctfds_own_page():
    status, _, body = get("/register")
    assert status == 200
    assert SIGN_IN not in body
