"""Theme tests for the Password Reset page (SP6 Stream C).

Verifies `/reset_password` and `/reset` rendering, gating, CSRF tokens, enumeration protection,
Turnstile integration, and error handling through full CTFd application.
"""
import os
from unittest import mock

import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_guard import turnstile
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


def test_reset_password_renders_theme_form_at_both_urls(app):
    with app.test_client() as client:
        for path in ("/reset_password", "/reset"):
            r = client.get(path)
            assert r.status_code == 200
            body = r.get_data(as_text=True)

            assert "Reset your password" in body
            assert 'id="f-email" name="email"' in body
            assert 'action="/reset"' in body

            # Security checks: no inline scripts or styles
            assert "<style>" not in body and "style=\"" not in body
            assert "<script>" not in body
            assert "javascript:" not in body


def test_reset_password_enumeration_protection(app, monkeypatch):
    """Answers the same whether or not the address has an account."""
    from CTFd.utils import config
    monkeypatch.setattr(config, "can_send_mail", lambda: True)

    with app.test_client() as client:
        r1 = client.get("/reset")
        with client.session_transaction() as sess:
            nonce = sess["nonce"]

        res_known = client.post("/reset", data={"email": "player@example.com", "nonce": nonce})
        res_unknown = client.post("/reset", data={"email": "nobody@example.com", "nonce": nonce})

        assert res_known.status_code == 200
        assert res_unknown.status_code == 200

        text_known = res_known.get_data(as_text=True)
        text_unknown = res_unknown.get_data(as_text=True)

        assert "If that account exists you will receive an email" in text_known
        assert "If that account exists you will receive an email" in text_unknown


def test_reset_password_turnstile_widget_rendered_when_configured(app):
    with mock.patch.dict(os.environ, KEYS):
        with app.test_client() as client:
            body = client.get("/reset").get_data(as_text=True)
            assert WIDGET in body
            assert SCRIPT in body
            assert turnstile.CF_TEST_PASS_SITEKEY in body


def test_reset_password_turnstile_widget_omitted_when_unconfigured(app):
    with mock.patch.dict(os.environ, {"TURNSTILE_SITEKEY": "", "TURNSTILE_SECRET": ""}):
        with app.test_client() as client:
            body = client.get("/reset").get_data(as_text=True)
            assert WIDGET not in body
            assert SCRIPT not in body


def test_reset_password_invalid_link_shows_alert(app):
    with app.test_client() as client:
        r = client.get("/reset/bad-invalid-token")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Your reset link is invalid" in body or "link does not work" in body


def test_reset_password_set_mode_renders_new_password_form(app):
    from CTFd.cache import cache

    token = "test-valid-reset-token-4567"
    with app.app_context():
        cache.set(f"reset_password_{token}", "player@example.com", timeout=1800)

    with app.test_client() as client:
        for path in (f"/reset_password/{token}", f"/reset/{token}"):
            r = client.get(path)
            assert r.status_code == 200
            body = r.get_data(as_text=True)

            assert "Choose a new password" in body
            assert 'id="f-password" name="password"' in body
            assert 'type="password"' in body
