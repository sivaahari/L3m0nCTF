"""The sign-in page and the error pages come from the theme, and they behave as the contract says (SP6 slice 1).

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme

Each error page is provoked for real where CTFd can be made to produce that status. A 429 page is only reachable through a route
that aborts with 429 (CTFd's own sign-in limit answers with JSON, not the error page), and a 500 through a route that raises; those
two routes exist only in these tests, added to the test app's routing table.
"""
import re

import pytest
from flask import abort
from werkzeug.routing import Rule

from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

SIGN_IN = 'id="login-h"'  # the sign-in heading's id in the prototype; CTFd's core login page has none
SLATE = 'id="sl-h"'  # the slate heading's id in the prototype; CTFd's core error pages have none
PASSWORD = "a-long-test-password"  # made up, for the throwaway test app only


@pytest.fixture
def app():
    """A CTFd app with the theme and our plugins, and one player who can sign in."""
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    register_user(app, name="player", email="player@example.com", password=PASSWORD)
    yield app
    destroy_ctfd(app)


def nonce_in(body):
    m = re.search(r'name="nonce" value="([^"]+)"', body)
    assert m, "the sign-in form has a hidden nonce"
    return m.group(1)


def test_the_sign_in_page_is_the_theme_and_carries_the_session_token(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/login").get_data(as_text=True)
            assert SIGN_IN in body
            with client.session_transaction() as sess:
                token = sess["nonce"]
            assert nonce_in(body) == token
            assert f'<meta name="csrf-token" content="{token}">' in body
            # the controls CTFd cannot offer are not shown
            assert "Keep me tuned in" not in body
            assert "Human check" not in body
            assert "Continue with Google" not in body


def test_a_wrong_password_comes_back_on_the_theme_with_the_alert_and_the_typed_name(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/login").get_data(as_text=True)
            r = client.post("/login", data={"nonce": nonce_in(body), "name": "player", "password": "not-the-password-1"})
            text = r.get_data(as_text=True)
            assert r.status_code == 200
            assert SIGN_IN in text
            assert "Your username or password is incorrect" in text
            assert 'value="player"' in text
            assert "not-the-password-1" not in text
            assert "data-alert" in text


def test_a_sign_in_form_with_a_stale_token_gets_the_session_changed_page(app):
    # CTFd answers a form whose token is not the session's with 403; the theme shows the prototype's "session changed" wording
    with app.app_context():
        with app.test_client() as client:
            client.get("/login")
            r = client.post("/login", data={"nonce": "stale-token", "name": "player", "password": PASSWORD})
            assert r.status_code == 403
            body = r.get_data(as_text=True)
            assert SLATE in body and "SESSION CHANGED" in body


def test_a_right_password_goes_to_the_default_page(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/login").get_data(as_text=True)
            r = client.post("/login", data={"nonce": nonce_in(body), "name": "player", "password": PASSWORD})
            assert r.status_code == 302
            assert r.headers["Location"].endswith("/challenges")  # CTFd's own default


def test_a_right_password_goes_to_a_safe_next_that_came_in_the_form(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/login?next=%2Fguide").get_data(as_text=True)
            assert re.search(r'name="next" value="/guide"', body), "the hidden next keeps the page's next"
            r = client.post("/login", data={"nonce": nonce_in(body), "name": "player", "password": PASSWORD, "next": "/guide"})
            assert r.status_code == 302
            assert r.headers["Location"].endswith("/guide")


@pytest.mark.parametrize("unsafe", ["https://evil.example/x", "//evil.example", "/\\evil.example", "javascript:alert(1)"])
def test_an_unsafe_next_is_ignored_and_the_default_page_is_used(app, unsafe):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/login").get_data(as_text=True)
            r = client.post("/login", data={"nonce": nonce_in(body), "name": "player", "password": PASSWORD, "next": unsafe})
            assert r.status_code == 302
            assert "evil" not in r.headers["Location"] and "javascript" not in r.headers["Location"]
            assert r.headers["Location"].endswith("/challenges")


def test_the_ctftime_button_is_there_exactly_when_its_sign_in_is_on(app, monkeypatch):
    monkeypatch.delenv("CTFTIME_CLIENT_ID", raising=False)
    monkeypatch.delenv("CTFTIME_CLIENT_SECRET", raising=False)
    with app.app_context():
        with app.test_client() as client:
            off = client.get("/login").get_data(as_text=True)
            assert "Login with CTFtime" not in off
            assert "Email and password are the way in for now." in off
            monkeypatch.setenv("CTFTIME_CLIENT_ID", "1234")
            monkeypatch.setenv("CTFTIME_CLIENT_SECRET", "test-only-secret")
            on = client.get("/login").get_data(as_text=True)
            assert 'href="/auth/ctftime"' in on
            assert "Login with CTFtime" in on
            assert "test-only-secret" not in on
            monkeypatch.delenv("CTFTIME_CLIENT_SECRET")  # half configured is off, as the sign-in itself is
            half = client.get("/login").get_data(as_text=True)
            assert "Login with CTFtime" not in half


def test_a_forbidden_page_is_ours_with_ctfds_own_status(app):
    # CTFd's admins-only pages send a player to the sign-in page for a page request and answer 403 only for a JSON request,
    # so the request says it is JSON; CTFd's own 403 handler then renders the page
    with app.app_context():
        player = login_as_user(app, "player", PASSWORD)  # a player, not the crew
        r = player.get("/admin/plugins/l3mon_theme", headers={"Content-Type": "application/json"})
        assert r.status_code == 403
        body = r.get_data(as_text=True)
        assert SLATE in body and "RESTRICTED CHANNEL" in body


def test_a_missing_page_is_ours_with_ctfds_own_status(app):
    with app.app_context():
        with app.test_client() as client:
            r = client.get("/no-such-channel-here")
            assert r.status_code == 404
            body = r.get_data(as_text=True)
            assert SLATE in body and "NO SIGNAL" in body


def test_a_rate_limited_page_is_ours_with_status_429(app):
    # CTFd registers its error handler for 401, 403, 404, 500 and 502 only, so a 429 would be Werkzeug's plain page. The test
    # registers CTFd's own renderer for 429 on this test app only; whether production should do the same is for the lead to decide.
    from CTFd.errors import render_error
    from werkzeug.exceptions import TooManyRequests

    app.error_handler_spec.setdefault(None, {}).setdefault(429, {})[TooManyRequests] = render_error
    app.url_map.add(Rule("/l3mon-test/limited", endpoint="l3mon_test_limited"))
    app.view_functions["l3mon_test_limited"] = lambda: abort(429)
    with app.app_context():
        with app.test_client() as client:
            r = client.get("/l3mon-test/limited")
            assert r.status_code == 429
            body = r.get_data(as_text=True)
            assert SLATE in body and "PLEASE STAND BY" in body


def test_a_server_error_page_is_ours_with_status_500_and_no_traceback(app):
    app.url_map.add(Rule("/l3mon-test/broken", endpoint="l3mon_test_broken"))

    def broken():
        raise ZeroDivisionError("a test-only failure")

    app.view_functions["l3mon_test_broken"] = broken
    app.config["PROPAGATE_EXCEPTIONS"] = False  # as in production: the error page, not the traceback
    with app.app_context():
        with app.test_client() as client:
            r = client.get("/l3mon-test/broken")
            assert r.status_code == 500
            body = r.get_data(as_text=True)
            assert SLATE in body and "TECHNICAL DIFFICULTY" in body
            assert "a test-only failure" not in body


def test_the_pages_have_no_inline_script_style_or_event_attribute(app):
    with app.app_context():
        with app.test_client() as client:
            pages = [client.get("/login").get_data(as_text=True), client.get("/no-such-channel-here").get_data(as_text=True)]
    for body in pages:
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", body), "a script with no file"
        assert "<style" not in body
        assert not re.search(r"\sstyle\s*=", body)
        assert not re.search(r"\son[a-z]+\s*=", body)


def test_every_static_file_the_sign_in_page_names_exists_and_is_served(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/login").get_data(as_text=True)
            links = sorted(set(re.findall(r'(?:href|src)="(/themes/l3mon/static/[^"]+)"', body)))
            assert len(links) == 3, links  # the app stylesheet, the boot script and the sign-in script
            for link in links:
                assert client.get(link).status_code == 200, link


def test_ctfds_own_pages_still_render_with_the_theme_in_place(app):
    # the pages the theme does not have fall back to CTFd's core theme; core's own asset calls (Assets.css, Assets.js) must still work
    with app.app_context():
        with app.test_client() as client:
            for path in ("/register", "/scoreboard"):
                r = client.get(path)
                assert r.status_code == 200, path
                body = r.get_data(as_text=True)
                assert SIGN_IN not in body and SLATE not in body, path
        player = login_as_user(app, "player", PASSWORD)
        settings = player.get("/settings")
        assert settings.status_code == 200


def test_the_sign_in_and_error_pages_carry_no_prototype_wording(app):
    with app.app_context():
        with app.test_client() as client:
            texts = {
                "sign in": client.get("/login").get_data(as_text=True),
                "missing page": client.get("/no-such-channel-here").get_data(as_text=True),
            }
    for name, body in texts.items():
        for word in ("prototype", "stand-in", "mock"):
            assert word not in body.lower(), f"{name}: {word!r} must not appear on a platform page"
        assert "DIRECTOR" not in body, f"{name}: DIRECTOR must not appear"
