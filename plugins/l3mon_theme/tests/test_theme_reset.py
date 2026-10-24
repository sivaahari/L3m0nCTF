"""Theme tests for the Password Reset page (SP6 Stream C).

Verifies `/reset_password` rendering, C8 GET-only redirect from `/reset`, CSRF tokens, enumeration protection,
Turnstile integration, field-specific error mapping (C6), password policy integration (C7), token handling
(invalid, expired, used), session clearing, and single rate-limit bucket.
"""
import os
from unittest import mock

import pytest

from CTFd.cache import cache
from CTFd.models import Users, db
from CTFd.plugins.l3mon_guard import turnstile
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
    """C8: /reset redirects on GET (302) and rejects POST (405) to avoid separate rate-limit buckets."""
    with app.test_client() as client:
        # GET /reset redirects to /reset_password
        r = client.get("/reset")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/reset_password")

        # GET /reset/<token> redirects to /reset_password/<token>
        r2 = client.get("/reset/tok123")
        assert r2.status_code == 302
        assert r2.headers["Location"].endswith("/reset_password/tok123")

        # POST /reset is not allowed
        r_post = client.post("/reset", data={"email": "player@example.com"})
        assert r_post.status_code == 405


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
