"""The human check on the theme's pages (SP6 plumbing): the value, and the sign-in page that draws the widget.

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md).
Cloudflare's published always-pass test keys are used; nothing is sent anywhere (the widget is only markup here).
"""
import logging
import os
import re
from unittest import mock

import pytest

from CTFd.plugins.l3mon_guard import turnstile
from CTFd.plugins.l3mon_theme import values
from tests.helpers import create_ctfd, destroy_ctfd

KEYS = {"TURNSTILE_SITEKEY": turnstile.CF_TEST_PASS_SITEKEY, "TURNSTILE_SECRET": turnstile.CF_TEST_PASS_SECRET}
WIDGET = 'class="cf-turnstile"'
SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js"
STAND_IN = "stand-in for the real check"


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    yield app
    destroy_ctfd(app)


def sign_in_page(app, **form):
    with app.test_client() as client:
        first = client.get("/login", environ_overrides={"REMOTE_ADDR": "192.0.2.77"}).get_data(as_text=True)
        if form:
            nonce = re.search(r'name="nonce" value="([^"]+)"', first).group(1)  # without the session's token CTFd answers 403 before the sign-in view runs
            return client.post("/login", data={**form, "nonce": nonce}, environ_overrides={"REMOTE_ADDR": "192.0.2.77"}).get_data(as_text=True)
        return first


def test_without_keys_the_value_is_off_and_the_page_names_nothing_outside(app):
    with mock.patch.dict(os.environ, {"TURNSTILE_SITEKEY": "", "TURNSTILE_SECRET": ""}):
        with app.test_request_context("/"):
            assert values.Values(app).captcha == {"sitekey": "", "register": False, "login": False, "reset": False}
        page = sign_in_page(app)
    assert WIDGET not in page and "challenges.cloudflare.com" not in page and STAND_IN not in page


def test_with_keys_the_check_is_for_sign_up_and_reset_but_not_yet_for_sign_in(app):
    with mock.patch.dict(os.environ, KEYS):
        with app.test_request_context("/"):
            state = values.Values(app).captcha
        assert state["sitekey"] == turnstile.CF_TEST_PASS_SITEKEY and state["register"] and state["reset"] and not state["login"]
        page = sign_in_page(app)
    assert WIDGET not in page, "a first visit to the sign-in page is not asked for the check"


def test_after_three_failures_for_an_address_the_sign_in_page_draws_the_widget_and_never_the_stand_in(app):
    with mock.patch.dict(os.environ, KEYS):
        with app.test_request_context("/login", environ_overrides={"REMOTE_ADDR": "192.0.2.77"}):
            for _ in range(turnstile.LOGIN_FAIL_THRESHOLD):
                turnstile.record_login_failure("nobody", "192.0.2.77")
        page = sign_in_page(app)
    assert WIDGET in page and f'data-sitekey="{turnstile.CF_TEST_PASS_SITEKEY}"' in page and SCRIPT in page
    assert STAND_IN not in page and 'name="human"' not in page


def test_after_three_failures_for_a_name_the_page_that_answers_a_post_for_that_name_draws_the_widget(app):
    with mock.patch.dict(os.environ, KEYS):
        with app.test_request_context("/login"):
            for _ in range(turnstile.LOGIN_FAIL_THRESHOLD):
                turnstile.record_login_failure("someone", "198.51.100.5")  # another address: only the name is over the line
        quiet = sign_in_page(app, name="another", password="not-it")
        asked = sign_in_page(app, name="someone", password="not-it")
    assert WIDGET not in quiet
    assert WIDGET in asked


def test_a_failure_in_the_guard_gives_the_safe_default_and_one_quiet_line(app, caplog):
    values._logged.discard("captcha")
    with mock.patch.object(turnstile, "get_guard_context", side_effect=RuntimeError("SECRET text")):
        with app.test_request_context("/"):
            found = values.Values(app)
            with caplog.at_level(logging.WARNING, logger="l3mon"):
                assert found.captcha["sitekey"] == "" and found.captcha["login"] is False
                assert found.captcha["register"] is False
    lines = [r.getMessage() for r in caplog.records if "captcha" in r.getMessage()]
    assert len(lines) == 1 and "SECRET" not in lines[0]
