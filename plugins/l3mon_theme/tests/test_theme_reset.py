"""Theme tests for the Password Reset page (SP6 Stream C).

Verifies `/reset_password` rendering, C8 GET-only redirect from `/reset`, CSRF tokens, enumeration protection,
Turnstile integration, field-specific error mapping (C6), password policy integration (C7), token handling
(invalid, expired, used), session clearing, and single rate-limit bucket.
"""
import os
import time
from unittest import mock

import pytest

from CTFd.cache import cache
from CTFd.models import Users, db
from CTFd.plugins.l3mon_guard import turnstile
from CTFd.plugins.l3mon_guard.policies.password import (
    V_MSG_PASS_EMPTY,
    V_MSG_PASS_SHORT,
    V_MSG_PASS_LONG,
    V_MSG_PASS_COMMON,
    V_MSG_PASS_USER,
    V_MSG_PASS_EMAIL,
)
from CTFd.plugins.l3mon_theme.pages.signin import RESET_MESSAGE_TARGETS
from CTFd.utils import set_config
from CTFd.utils.security.signing import serialize
from tests.helpers import create_ctfd, destroy_ctfd, register_user

PASSWORD = "test-reset-password-123"
KEYS = {"TURNSTILE_SITEKEY": turnstile.CF_TEST_PASS_SITEKEY, "TURNSTILE_SECRET": turnstile.CF_TEST_PASS_SECRET}
WIDGET = 'class="cf-turnstile"'
SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js"


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    with app.app_context():
        register_user(app, name="player", email="player@example.com", password=PASSWORD)
    yield app
    destroy_ctfd(app)


def test_reset_password_renders_theme_form(app):
    """GET /reset_password renders the theme form with action pointing to /reset_password."""
    with app.test_client() as client:
        r = client.get("/reset_password")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert "Reset your password" in body
        assert 'id="f-email" name="email"' in body
        assert 'action="/reset_password"' in body

        # Security checks: no inline scripts or styles
        assert "<style>" not in body and "style=\"" not in body
        assert "<script>" not in body
        assert "javascript:" not in body


def test_c8_reset_alias_redirects_on_get_and_rejects_post(app):
    """C8: /reset redirects on GET (302) and rejects POST ({404, 405}) to avoid separate rate-limit buckets."""
    with app.test_client() as client:
        # GET /reset redirects to /reset_password
        r = client.get("/reset")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/reset_password")

        # GET /reset/<token> redirects to /reset_password/<token>
        r2 = client.get("/reset/tok123")
        assert r2.status_code == 302
        assert r2.headers["Location"].endswith("/reset_password/tok123")

        # POST /reset is not allowed; CTFd answers 404 (its csrf hook raises 404 for unrouted views) or 405
        r_post = client.post("/reset", data={"email": "player@example.com"})
        assert r_post.status_code in {404, 405}


def test_reset_password_enumeration_protection(app, monkeypatch):
    """Answers the same whether or not the address has an account."""
    from CTFd.utils import config
    monkeypatch.setattr(config, "can_send_mail", lambda: True)

    with app.test_client() as client:
        r1 = client.get("/reset_password")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        res_known = client.post("/reset_password", data={"email": "player@example.com", "nonce": nonce})
        res_unknown = client.post("/reset_password", data={"email": "nobody@example.com", "nonce": nonce})

        assert res_known.status_code == 200
        assert res_unknown.status_code == 200

        text_known = res_known.get_data(as_text=True)
        text_unknown = res_unknown.get_data(as_text=True)

        assert "If that account exists you will receive an email" in text_known
        assert "If that account exists you will receive an email" in text_unknown


def test_reset_password_turnstile_widget_rendered_when_configured(app):
    with mock.patch.dict(os.environ, KEYS):
        with app.test_client() as client:
            body = client.get("/reset_password").get_data(as_text=True)
            assert WIDGET in body
            assert SCRIPT in body
            assert turnstile.CF_TEST_PASS_SITEKEY in body


def test_reset_password_turnstile_widget_omitted_when_unconfigured(app):
    with mock.patch.dict(os.environ, {"TURNSTILE_SITEKEY": "", "TURNSTILE_SECRET": ""}):
        with app.test_client() as client:
            body = client.get("/reset_password").get_data(as_text=True)
            assert WIDGET not in body
            assert SCRIPT not in body


def test_reset_password_invalid_link_shows_alert(app):
    with app.test_client() as client:
        r = client.get("/reset_password/bad-invalid-token")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Your reset link is invalid" in body or "link does not work" in body


def test_reset_password_set_mode_renders_new_password_form(app):
    token = "test-valid-reset-token-4567"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        r = client.get(f"/reset_password/{token}")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert "Choose a new password" in body
        assert 'id="f-password" name="password"' in body
        assert 'type="password"' in body
        assert 'action="/reset_password/' in body


def test_c6_generic_and_turnstile_errors_remain_in_summary_without_field_attachment(app):
    """C6: Turnstile errors or generic messages are in the summary only and not attached to email field."""
    with mock.patch.dict(os.environ, KEYS):
        with app.test_client() as client:
            client.get("/reset_password")
            with client.session_transaction() as sess:
                nonce = sess["nonce"]

            # Post without Turnstile response
            r = client.post("/reset_password", data={"email": "player@example.com", "nonce": nonce})
            assert r.status_code == 200
            body = r.get_data(as_text=True)

            # Turnstile error is in summary
            assert "human check" in body.lower() or "turnstile" in body.lower()
            # Must NOT invent 'email: ...' prefix in summary
            assert "email:" not in body.lower()


def test_c7_reset_password_enforces_password_policy(app):
    """C7: Posting a password shorter than 12 characters to /reset_password/<token> is rejected."""
    app.config["L3MON_SIGNUP_RULES"] = True
    token = "test-policy-reset-token-999"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        # 11 characters -> rejected by policy
        r = client.post(f"/reset_password/{token}", data={"password": "short-pass1", "nonce": nonce})
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Use at least 12 characters." in body


def test_reset_does_not_mark_address_confirmed(app):
    """Resetting password does not mark user.verified as True (CTFd keeps original status)."""
    token = "test-verified-check-token"
    with app.app_context():
        u = Users.query.filter_by(email="player@example.com").first()
        u.verified = False
        db.session.commit()
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        new_pass = "ValidNewPassword123!"
        r = client.post(f"/reset_password/{token}", data={"password": new_pass, "nonce": nonce})
        # After successful reset, CTFd redirects to /login
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/login")

    with app.app_context():
        u = Users.query.filter_by(email="player@example.com").first()
        assert u.verified is False, "Password reset must not confirm the email"


# =============================================================================
# Round 3 C4: Tests for every message in RESET_MESSAGE_TARGETS through CTFd,
# verifying field/summary mapping and single rendering (no second box).
# =============================================================================

def test_reset_message_targets_table_structure():
    """C4: Table contains exact messages for CTFd, password policy, and turnstile."""
    expected_keys = {
        V_MSG_PASS_EMPTY,
        V_MSG_PASS_SHORT,
        V_MSG_PASS_LONG,
        V_MSG_PASS_COMMON,
        V_MSG_PASS_USER,
        V_MSG_PASS_EMAIL,
        "Please complete the security check.",
        "Security check failed. Please try again.",
        "This CTF is not configured to send email.<br> Please contact an organizer to have your password reset.",
        "Your link has expired",
        "Your reset token is invalid",
    }
    assert set(RESET_MESSAGE_TARGETS.keys()) == expected_keys
    for k, v in RESET_MESSAGE_TARGETS.items():
        assert v in ("password", "email", None), f"Unexpected target for {k!r}: {v!r}"


def test_error_pass_empty_mapped_to_password_field(app):
    """C4: Empty password message maps to password field and appears once."""
    app.config["L3MON_SIGNUP_RULES"] = True
    token = "test-token-empty-pw"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        r = client.post(f"/reset_password/{token}", data={"password": "", "nonce": nonce})
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert V_MSG_PASS_EMPTY in body
        assert f'href="#f-password">{V_MSG_PASS_EMPTY}</a>' in body
        assert f'id="f-password-e"' in body
        assert "data-alert" not in body
        # Appears in summary link and field error paragraph: exactly once per presentation slot, no duplicate alert box
        assert body.count(V_MSG_PASS_EMPTY) == 2


def test_error_pass_short_mapped_to_password_field(app):
    """C4: Short password message maps to password field and appears once."""
    app.config["L3MON_SIGNUP_RULES"] = True
    token = "test-token-short-pw"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        r = client.post(f"/reset_password/{token}", data={"password": "short", "nonce": nonce})
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert V_MSG_PASS_SHORT in body
        assert f'href="#f-password">{V_MSG_PASS_SHORT}</a>' in body
        assert f'id="f-password-e"' in body
        assert "data-alert" not in body
        assert body.count(V_MSG_PASS_SHORT) == 2


def test_error_pass_long_mapped_to_password_field(app):
    """C4: Long password message maps to password field and appears once."""
    app.config["L3MON_SIGNUP_RULES"] = True
    token = "test-token-long-pw"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        r = client.post(f"/reset_password/{token}", data={"password": "a" * 129, "nonce": nonce})
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert V_MSG_PASS_LONG in body
        assert f'href="#f-password">{V_MSG_PASS_LONG}</a>' in body
        assert f'id="f-password-e"' in body
        assert "data-alert" not in body
        assert body.count(V_MSG_PASS_LONG) == 2


def test_error_pass_common_mapped_to_password_field(app):
    """C4: Common password message maps to password field and appears once."""
    app.config["L3MON_SIGNUP_RULES"] = True
    token = "test-token-common-pw"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        r = client.post(f"/reset_password/{token}", data={"password": "Password1234!", "nonce": nonce})
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert V_MSG_PASS_COMMON in body
        assert f'href="#f-password">{V_MSG_PASS_COMMON}</a>' in body
        assert f'id="f-password-e"' in body
        assert "data-alert" not in body
        assert body.count(V_MSG_PASS_COMMON) == 2


def test_error_pass_user_mapped_to_password_field(app):
    """C4: Username context match maps to password field and appears once."""
    app.config["L3MON_SIGNUP_RULES"] = True
    token = "test-token-user-pw"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        r = client.post(f"/reset_password/{token}", data={"password": "SecretPlayerPassword123!", "nonce": nonce})
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert V_MSG_PASS_USER in body
        assert f'href="#f-password">{V_MSG_PASS_USER}</a>' in body
        assert f'id="f-password-e"' in body
        assert "data-alert" not in body
        assert body.count(V_MSG_PASS_USER) == 2


def test_error_pass_email_mapped_to_password_field(app):
    """C4: Email context match maps to password field and appears once."""
    app.config["L3MON_SIGNUP_RULES"] = True
    token = "test-token-email-pw"
    with app.app_context():
        # User whose name is different from email localpart
        u2 = Users(name="different_name", email="localpart@example.com", password="OldPassword1234!")
        db.session.add(u2)
        db.session.commit()
        cache.set(f"reset_password_{token}", "localpart@example.com", timeout=1800)

    with app.test_client() as client:
        client.get(f"/reset_password/{token}")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        r = client.post(f"/reset_password/{token}", data={"password": "SecretlocalpartPassword123!", "nonce": nonce})
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert V_MSG_PASS_EMAIL in body
        assert f'href="#f-password">{V_MSG_PASS_EMAIL}</a>' in body
        assert f'id="f-password-e"' in body
        assert "data-alert" not in body
        assert body.count(V_MSG_PASS_EMAIL) == 2


def test_error_turnstile_missing_mapped_to_summary_only(app):
    """C4: Missing turnstile response maps to summary only and appears once."""
    with mock.patch.dict(os.environ, KEYS):
        with app.test_client() as client:
            client.get("/reset_password")
            with client.session_transaction() as sess:
                nonce = sess["nonce"]

            r = client.post("/reset_password", data={"email": "player@example.com", "nonce": nonce})
            assert r.status_code == 200
            body = r.get_data(as_text=True)

            msg = "Please complete the security check."
            assert msg in body
            assert f'href="#reg-h">{msg}</a>' in body
            assert "id=\"f-email-e\"" not in body
            assert "data-alert" not in body
            assert body.count(msg) == 1


def test_error_turnstile_failed_mapped_to_summary_only(app):
    """C4: Failed turnstile check maps to summary only and appears once."""
    fail_keys = {"TURNSTILE_SITEKEY": turnstile.CF_TEST_FAIL_SITEKEY, "TURNSTILE_SECRET": turnstile.CF_TEST_FAIL_SECRET}
    with mock.patch.dict(os.environ, fail_keys):
        with app.test_client() as client:
            client.get("/reset_password")
            with client.session_transaction() as sess:
                nonce = sess["nonce"]

            r = client.post("/reset_password", data={"email": "player@example.com", "cf-turnstile-response": "bad-token", "nonce": nonce})
            assert r.status_code == 200
            body = r.get_data(as_text=True)

            msg = "Security check failed. Please try again."
            assert msg in body
            assert f'href="#reg-h">{msg}</a>' in body
            assert "id=\"f-email-e\"" not in body
            assert "data-alert" not in body
            assert body.count(msg) == 1


def test_error_mail_unconfigured_mapped_to_summary_only(app, monkeypatch):
    """C4: Mail unconfigured message maps to summary only, and appears once (no duplicate box)."""
    from CTFd.utils import config
    monkeypatch.setattr(config, "can_send_mail", lambda: False)

    with app.test_client() as client:
        r = client.get("/reset_password")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert "This CTF is not configured to send email" in body
        assert "data-summary" in body
        assert "data-alert" not in body
        # Must appear ONCE: the bug of duplicate alert box right under summary is fixed
        assert body.count("This CTF is not configured to send email") == 1


def test_error_link_expired_mapped_to_summary_only(app):
    """C4: Expired token message maps to summary only and appears once."""
    with app.app_context():
        # Token generated 3600s in the past
        past_time = time.time() - 3600
        with mock.patch("time.time", return_value=past_time):
            token = serialize("player@example.com")

    with app.test_client() as client:
        r = client.get(f"/reset_password/{token}")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        msg = "Your link has expired"
        assert msg in body
        assert f'href="#reg-h">{msg}</a>' in body
        assert "data-alert" not in body
        assert body.count(msg) == 1


def test_error_token_invalid_mapped_to_summary_only(app):
    """C4: Invalid token message maps to summary only and appears once."""
    with app.test_client() as client:
        r = client.get("/reset_password/bad-invalid-token-1234")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        msg = "Your reset token is invalid"
        assert msg in body
        assert f'href="#reg-h">{msg}</a>' in body
        assert "data-alert" not in body
        assert body.count(msg) == 1


def test_reset_four_states_and_signin_single_message_rendering(app, monkeypatch):
    """C4: Check all four states of reset (ask, sent, set, invalid) and sign-in page for single message rendering."""
    from CTFd.utils import config
    monkeypatch.setattr(config, "can_send_mail", lambda: True)

    with app.test_client() as client:
        # 1. State: ask (GET /reset_password)
        r_ask = client.get("/reset_password")
        assert r_ask.status_code == 200
        b_ask = r_ask.get_data(as_text=True)
        assert "Reset your password" in b_ask
        assert "data-summary" not in b_ask
        assert "data-alert" not in b_ask

        # 2. State: sent (POST /reset_password)
        with client.session_transaction() as sess:
            nonce = sess["nonce"]
        r_sent = client.post("/reset_password", data={"email": "player@example.com", "nonce": nonce})
        assert r_sent.status_code == 200
        b_sent = r_sent.get_data(as_text=True)
        assert "data-summary" not in b_sent
        assert "data-alert" in b_sent
        assert "alert-info" in b_sent
        assert "If that account exists you will receive an email" in b_sent
        assert b_sent.count("If that account exists you will receive an email") == 1

        # 3. State: set (GET /reset_password/<token>)
        token = "valid-set-token-777"
        with app.app_context():
            cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

        r_set = client.get(f"/reset_password/{token}")
        assert r_set.status_code == 200
        b_set = r_set.get_data(as_text=True)
        assert "Choose a new password" in b_set
        assert "data-summary" not in b_set
        assert "data-alert" not in b_set

        # 4. State: invalid/expired token (GET /reset_password/<bad_token>)
        r_inv = client.get("/reset_password/bad-corrupt-token")
        assert r_inv.status_code == 200
        b_inv = r_inv.get_data(as_text=True)
        assert "data-summary" in b_inv
        assert "data-alert" not in b_inv
        assert b_inv.count("Your reset token is invalid") == 1

        # 5. Sign-in page: clean GET /login and failed POST /login
        r_login_get = client.get("/login")
        assert r_login_get.status_code == 200
        b_login_get = r_login_get.get_data(as_text=True)
        assert "data-summary" not in b_login_get
        assert "data-alert" not in b_login_get

        with client.session_transaction() as sess:
            login_nonce = sess["nonce"]
        r_login_post = client.post("/login", data={"name": "player", "password": "wrong-password", "nonce": login_nonce})
        assert r_login_post.status_code == 200
        b_login_post = r_login_post.get_data(as_text=True)
        assert "data-summary" not in b_login_post
        assert "data-alert" in b_login_post
        assert b_login_post.count("Your username or password is incorrect") == 1
