"""Tests for the themed rate-limit page (SP6 / Stream E, E4).

Verifies that when a route protected by CTFd's @ratelimit decorator is pushed over its limit:
- Browser page requests (GET or POST with CSRF nonce) receive the themed HTML slate (status 429, 'Please stand by')
- Headers Retry-After: <seconds> and Cache-Control: no-store are set
- JSON API requests, AJAX requests (X-Requested-With: XMLHttpRequest), and Accept: application/json requests keep CTFd's JSON response untouched
- Excluded prefixes (/api/, /events, /themes/, /files/, /story/, /admin/) are never converted to HTML
- Non-429 responses and non-l3mon themes leave responses untouched
- Hook cost on non-429 requests is zero database statements
- Exception handling in the conversion logs one line naming the error kind and path without leaking data, returning CTFd's JSON untouched
"""
import logging
import re
from unittest import mock

import pytest
from sqlalchemy import event

from CTFd.models import Users, db
from CTFd.plugins.l3mon_theme.pages import errors
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PASSWORD = "test-ratelimit-password-123"
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"


@pytest.fixture
def app():
    """A CTFd app with the l3mon theme and plugins in teams mode."""
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    with app.app_context():
        register_user(app, name="rl_user", email="rl_user@example.com", password=PASSWORD)
    yield app
    destroy_ctfd(app)


def _get_nonce(client):
    """Retrieve form CSRF token from the login page."""
    client.get("/login")
    with client.session_transaction() as sess:
        return sess.get("nonce")


def _trigger_login_ratelimit(client, headers=None):
    """POST /login repeatedly with nonce until rate-limited (CTFd default limit is 10 in 5s/60s)."""
    nonce = _get_nonce(client)
    r = None
    for _ in range(12):
        r = client.post("/login", data={"name": "rl_user", "password": "wrong-password", "nonce": nonce}, headers=headers)
        if r.status_code == 429:
            return r
    assert r is not None and r.status_code == 429, f"expected 429, got {r.status_code if r else None}"
    return r


# --------------------------------------------------------------------------------------------------
# Themed Slate for Page Requests
# --------------------------------------------------------------------------------------------------

def test_rate_limit_browser_form_post_returns_themed_slate(app):
    """A browser submitting a form with nonce that hits the rate limit receives the themed HTML slate."""
    with app.test_client() as client:
        r = _trigger_login_ratelimit(client)
        assert r.status_code == 429
        assert "text/html" in r.headers.get("Content-Type", "")
        assert r.headers.get("Cache-Control") == "no-store"
        assert "Retry-After" in r.headers
        assert r.headers["Retry-After"].isdigit()
        assert int(r.headers["Retry-After"]) > 0

        html = r.get_data(as_text=True)
        assert "Please stand by" in html
        assert "Too many requests" in html
        assert "Slow down a little." in html
        assert "data-retry-bar" in html


def test_rate_limit_browser_with_html_accept_header_returns_slate(app):
    """A request with standard browser Accept header receives the themed HTML slate."""
    with app.test_client() as client:
        r = _trigger_login_ratelimit(client, headers={"Accept": BROWSER_ACCEPT})
        assert r.status_code == 429
        assert "text/html" in r.headers.get("Content-Type", "")
        html = r.get_data(as_text=True)
        assert "Please stand by" in html
        assert "Too many requests" in html


# --------------------------------------------------------------------------------------------------
# Preserving JSON for API and AJAX Requests
# --------------------------------------------------------------------------------------------------

def test_rate_limit_accept_application_json_keeps_json_response(app):
    """When client prefers application/json, 429 response remains CTFd's raw JSON."""
    with app.test_client() as client:
        r = _trigger_login_ratelimit(client, headers={"Accept": "application/json"})
        assert r.status_code == 429
        assert "application/json" in r.headers.get("Content-Type", "")
        payload = r.get_json()
        assert isinstance(payload, dict)
        assert payload.get("code") == 429 or "Too many requests" in payload.get("message", "")


def test_rate_limit_xmlhttprequest_header_keeps_json_response(app):
    """An AJAX call with X-Requested-With: XMLHttpRequest keeps CTFd's JSON response."""
    with app.test_client() as client:
        r = _trigger_login_ratelimit(client, headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 429
        assert "application/json" in r.headers.get("Content-Type", "")
        payload = r.get_json()
        assert isinstance(payload, dict)
        assert payload.get("code") == 429 or "Too many requests" in payload.get("message", "")


def test_rate_limit_json_content_type_body_keeps_json_response():
    """A request with Content-Type: application/json keeps CTFd's JSON response."""
    from unittest.mock import MagicMock
    from CTFd.plugins.l3mon_theme.pages.errors import _is_page_request

    req = MagicMock(method="POST", path="/login", accept_mimetypes=None, headers={}, content_type="application/json")
    resp = MagicMock(is_json=True, get_json=lambda silent=True: {"code": 429, "message": "Too many requests"})
    assert _is_page_request(req, resp) is False


def test_rate_limit_api_prefix_path_never_converted():
    """Routes starting with /api/, /events, /themes/, /files/, /story/, /admin/ are never converted."""
    from unittest.mock import MagicMock
    from CTFd.plugins.l3mon_theme.pages.errors import EXCLUDED_PREFIXES, _is_page_request

    expected_prefixes = {"/api/", "/events", "/themes/", "/files/", "/story/", "/admin/"}
    assert set(EXCLUDED_PREFIXES) == expected_prefixes

    resp = MagicMock(is_json=True, get_json=lambda silent=True: {"code": 429, "message": "Too many requests"})
    for prefix in ("/api/", "/events", "/themes/", "/files/", "/story/", "/admin/"):
        req = MagicMock(method="POST", path=f"{prefix}something", accept_mimetypes=None, headers={}, content_type="")
        assert _is_page_request(req, resp) is False, f"prefix {prefix} should not be treated as a page request"


# --------------------------------------------------------------------------------------------------
# Safe Fallback and Error Logging
# --------------------------------------------------------------------------------------------------

def test_rate_limit_conversion_error_logs_one_line_and_returns_ctfd_json(app, caplog):
    """When HTML rendering raises, safe fallback logs one line with error kind and returns raw JSON."""
    errors._logged.clear()

    with mock.patch("CTFd.plugins.l3mon_theme.pages.errors.render_template", side_effect=RuntimeError("simulated render fail")):
        with app.test_client() as client:
            with caplog.at_level(logging.WARNING, logger="l3mon"):
                r = _trigger_login_ratelimit(client)
                assert r.status_code == 429
                assert "application/json" in r.headers.get("Content-Type", "")
                assert isinstance(r.get_json(), dict)

                # Check log record: exactly one warning naming the error kind and path
                warnings = [rec for rec in caplog.records if rec.name == "l3mon" and rec.levelno >= logging.WARNING]
                assert len(warnings) == 1
                assert "RuntimeError" in warnings[0].message
                assert "/login" in warnings[0].message


# --------------------------------------------------------------------------------------------------
# Excluded Prefixes, Methods, and Non-l3mon Theme
# --------------------------------------------------------------------------------------------------

def test_rate_limit_other_theme_leaves_ctfd_json(app):
    """When ctf_theme is not l3mon (e.g. core), 429 response is left as raw JSON."""
    with app.app_context():
        set_config("ctf_theme", "core")

    with app.test_client() as client:
        r = _trigger_login_ratelimit(client)
        assert r.status_code == 429
        assert "application/json" in r.headers.get("Content-Type", "")
        assert isinstance(r.get_json(), dict)


def test_rate_limit_non_429_responses_completely_untouched(app):
    """Non-429 responses (e.g. 200, 302, 404) are completely untouched."""
    with app.test_client() as client:
        r200 = client.get("/login")
        assert r200.status_code == 200
        assert "Retry-After" not in r200.headers

        r404 = client.get("/non_existent_page_12345")
        assert r404.status_code == 404
        assert "Retry-After" not in r404.headers


# --------------------------------------------------------------------------------------------------
# Hook Cost on Non-429 Requests
# --------------------------------------------------------------------------------------------------

def test_rate_limit_hook_cost_on_non_429_requests(app):
    """The after_request interceptor costs zero database statements on non-429 requests."""
    seen_queries = []

    def on_query(conn, cursor, statement, parameters, context, executemany):
        seen_queries.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", on_query)
        try:
            with app.test_client() as client:
                client.get("/login")
                seen_queries.clear()

                for _ in range(5):
                    res = client.get("/login")
                    assert res.status_code == 200

                assert len(seen_queries) == 0, f"expected 0 queries on warm cache, got {len(seen_queries)}"
        finally:
            event.remove(db.engine, "before_cursor_execute", on_query)
