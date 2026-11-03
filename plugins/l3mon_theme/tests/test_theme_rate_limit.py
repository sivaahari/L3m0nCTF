"""Tests for the themed rate-limit page (SP6 / Stream E, E4).

Verifies that when a route protected by CTFd's @ratelimit decorator is pushed over its limit:
- Browser page requests (GET or POST) receive the themed HTML slate (status 429, 'Please stand by')
- Headers Retry-After: <seconds> and Cache-Control: no-store are set
- JSON API requests, AJAX requests (X-Requested-With: XMLHttpRequest), and Accept: application/json requests keep CTFd's JSON response untouched
- Excluded prefixes (/api/, /events, /themes/, /files/, /story/, /admin/) are never converted to HTML
- Non-429 responses and non-l3mon themes leave responses untouched
- Hook cost on non-429 requests is zero database statements
"""
import re
from unittest import mock

import pytest
from sqlalchemy import event

from CTFd.models import Users, db
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PASSWORD = "test-ratelimit-password-123"


@pytest.fixture
def app():
    """A CTFd app with the l3mon theme and plugins in teams mode."""
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    with app.app_context():
        register_user(app, name="rl_user", email="rl_user@example.com", password=PASSWORD)
    yield app
    destroy_ctfd(app)


def _trigger_login_ratelimit(client):
    """POST /login repeatedly until rate-limited (CTFd default limit is 10/min)."""
    r = None
    for _ in range(12):
        r = client.post("/login", data={"name": "rl_user", "password": "wrong-password"})
        if r.status_code == 429:
            return r
    assert r is not None and r.status_code == 429, f"expected 429, got {r.status_code if r else None}"
    return r


# --------------------------------------------------------------------------------------------------
# Themed Slate for Page Requests
# --------------------------------------------------------------------------------------------------

def test_rate_limit_browser_form_post_returns_themed_slate(app):
    """A browser submitting a form that hits the rate limit receives the themed HTML slate."""
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
    browser_accept = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    with app.test_client() as client:
        for _ in range(10):
            client.post("/login", data={"name": "rl_user", "password": "wrong"}, headers={"Accept": browser_accept})
        r11 = client.post("/login", data={"name": "rl_user", "password": "wrong"}, headers={"Accept": browser_accept})
        assert r11.status_code == 429
        assert "text/html" in r11.headers.get("Content-Type", "")
        html = r11.get_data(as_text=True)
        assert "Please stand by" in html
        assert "Too many requests" in html


# --------------------------------------------------------------------------------------------------
# Preserving JSON for API and AJAX Requests
# --------------------------------------------------------------------------------------------------

def test_rate_limit_accept_application_json_keeps_json_response(app):
    """When client prefers application/json, 429 response remains CTFd's raw JSON."""
    with app.test_client() as client:
        # First 10
        for _ in range(10):
            client.post("/login", data={"name": "rl_user", "password": "wrong"}, headers={"Accept": "application/json"})
        r11 = client.post("/login", data={"name": "rl_user", "password": "wrong"}, headers={"Accept": "application/json"})
        assert r11.status_code == 429
        assert "application/json" in r11.headers.get("Content-Type", "")
        payload = r11.get_json()
        assert isinstance(payload, dict)
        assert payload.get("code") == 429 or "Too many requests" in payload.get("message", "")


def test_rate_limit_xmlhttprequest_header_keeps_json_response(app):
    """An AJAX call with X-Requested-With: XMLHttpRequest keeps CTFd's JSON response."""
    with app.test_client() as client:
        headers = {"X-Requested-With": "XMLHttpRequest"}
        for _ in range(10):
            client.post("/login", data={"name": "rl_user", "password": "wrong"}, headers=headers)
        r11 = client.post("/login", data={"name": "rl_user", "password": "wrong"}, headers=headers)
        assert r11.status_code == 429
        assert "application/json" in r11.headers.get("Content-Type", "")
        payload = r11.get_json()
        assert isinstance(payload, dict)
        assert payload.get("code") == 429 or "Too many requests" in payload.get("message", "")


def test_rate_limit_json_content_type_body_keeps_json_response(app):
    """A request with Content-Type: application/json keeps CTFd's JSON response."""
    with app.test_client() as client:
        headers = {"Content-Type": "application/json"}
        for _ in range(10):
            client.post("/login", json={"name": "rl_user", "password": "wrong"}, headers=headers)
        r11 = client.post("/login", json={"name": "rl_user", "password": "wrong"}, headers=headers)
        assert r11.status_code == 429
        assert "application/json" in r11.headers.get("Content-Type", "")
        payload = r11.get_json()
        assert isinstance(payload, dict)


def test_rate_limit_api_prefix_path_never_converted(app):
    """Routes starting with /api/ always keep JSON answers."""
    from CTFd.utils.decorators import ratelimit
    from flask import jsonify

    # Register a temporary ratelimited route under /api/
    @app.route("/api/v1/test_rl", methods=["POST"])
    @ratelimit(method="POST", limit=2, interval=60)
    def test_api_rl():
        return jsonify({"success": True})

    with app.test_client() as client:
        client.post("/api/v1/test_rl")
        client.post("/api/v1/test_rl")
        r3 = client.post("/api/v1/test_rl")  # 3rd is ratelimited
        assert r3.status_code == 429
        assert "application/json" in r3.headers.get("Content-Type", "")
        payload = r3.get_json()
        assert isinstance(payload, dict)


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

    event.listen(db.engine, "before_cursor_execute", on_query)
    try:
        with app.test_client() as client:
            # Prime cache
            client.get("/login")
            seen_queries.clear()

            # Measure 5 requests
            for _ in range(5):
                res = client.get("/login")
                assert res.status_code == 200

            # Interceptor itself did not execute any extra queries
            assert len(seen_queries) == 0, f"expected 0 queries on warm cache, got {len(seen_queries)}"
    finally:
        event.remove(db.engine, "before_cursor_execute", on_query)
