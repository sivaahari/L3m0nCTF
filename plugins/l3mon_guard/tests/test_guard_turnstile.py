"""Tests for Cloudflare Turnstile CAPTCHA and login failure tracking (SP5 B4.5).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_turnstile.py
"""
import os
import unittest.mock as mock
from tests.helpers import create_ctfd, destroy_ctfd

from CTFd.plugins.l3mon_guard.models import Evidence
from CTFd.plugins.l3mon_guard.turnstile import (
    CF_TEST_FAIL_SECRET,
    CF_TEST_FAIL_SITEKEY,
    CF_TEST_PASS_SECRET,
    CF_TEST_PASS_SITEKEY,
    clear_login_failure,
    get_guard_context,
    is_active,
    is_login_captcha_required,
    record_login_failure,
    verify_turnstile,
)


def test_turnstile_inactive_when_unconfigured():
    with mock.patch.dict(os.environ, {"TURNSTILE_SITEKEY": "", "TURNSTILE_SECRET": ""}, clear=False):
        assert not is_active()
        valid, err = verify_turnstile("dummy_token", action="register")
        assert valid is True
        assert err is None


def test_turnstile_cf_test_pass_key():
    env = {
        "TURNSTILE_SITEKEY": CF_TEST_PASS_SITEKEY,
        "TURNSTILE_SECRET": CF_TEST_PASS_SECRET,
    }
    with mock.patch.dict(os.environ, env, clear=False):
        assert is_active()
        # Valid when token present
        valid, err = verify_turnstile("any_token", action="register")
        assert valid is True
        assert err is None

        # Invalid when token empty
        valid, err = verify_turnstile("", action="register")
        assert valid is False
        assert "complete the security check" in err


def test_turnstile_cf_test_fail_key():
    env = {
        "TURNSTILE_SITEKEY": CF_TEST_FAIL_SITEKEY,
        "TURNSTILE_SECRET": CF_TEST_FAIL_SECRET,
    }
    with mock.patch.dict(os.environ, env, clear=False):
        assert is_active()
        valid, err = verify_turnstile("any_token", action="register")
        assert valid is False
        assert "failed" in err


def test_turnstile_mock_siteverify_success_and_failure():
    env = {
        "TURNSTILE_SITEKEY": "0x4AAAAAAAAAA_sitekey",
        "TURNSTILE_SECRET": "0x4AAAAAAAAAA_secret",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        # Mock siteverify returns success=true
        with mock.patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.content = b'{"success": true}'
            mock_post.return_value.json.return_value = {"success": True}

            valid, err = verify_turnstile("valid_token", "1.2.3.4", action="register")
            assert valid is True
            assert err is None

        # Mock siteverify returns success=false
        with mock.patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.content = b'{"success": false}'
            mock_post.return_value.json.return_value = {"success": False}

            valid, err = verify_turnstile("bad_token", "1.2.3.4", action="register")
            assert valid is False
            assert "failed" in err


def test_turnstile_unreachable_fail_open_login_and_reset():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        env = {
            "TURNSTILE_SITEKEY": "0x4AAAAAAAAAA_sitekey",
            "TURNSTILE_SECRET": "0x4AAAAAAAAAA_secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            import requests

            with mock.patch("requests.post", side_effect=requests.RequestException("Network down")):
                # Login fails open, records evidence
                valid, err = verify_turnstile("token", "192.0.2.1", action="login")
                assert valid is True
                assert err is None

                # Reset fails open, records evidence
                valid, err = verify_turnstile("token", "192.0.2.2", action="reset")
                assert valid is True
                assert err is None

                # Verify captcha_unverified evidence recorded
                rows = Evidence.query.filter_by(kind="captcha_unverified").order_by(Evidence.id).all()
                assert len(rows) == 2
                assert rows[0].address == "192.0.2.1"
                assert "192.0.2.1" not in rows[0].detail
                assert rows[0].detail == "action=login"
                assert rows[1].address == "192.0.2.2"
                assert "192.0.2.2" not in rows[1].detail

                # Register fails closed
                valid, err = verify_turnstile("token", "192.0.2.3", action="register")
                assert valid is False
                assert "could not be verified" in err
    destroy_ctfd(app)


def test_turnstile_verify_url_whitelist():
    from CTFd.plugins.l3mon_guard.turnstile import _get_verify_url, VERIFY_URL

    # Disallowed external URL falls back to official Cloudflare URL
    with mock.patch.dict(os.environ, {"TURNSTILE_VERIFY_URL": "https://evil.com/siteverify", "L3MON_AUTH_TEST_MODE": "0"}, clear=False):
        assert _get_verify_url() == VERIFY_URL

    # Localhost disallowed in production mode
    with mock.patch.dict(os.environ, {"TURNSTILE_VERIFY_URL": "http://localhost:8000/siteverify", "L3MON_AUTH_TEST_MODE": "0"}, clear=False):
        assert _get_verify_url() == VERIFY_URL

    # Localhost allowed only in test mode
    with mock.patch.dict(os.environ, {"TURNSTILE_VERIFY_URL": "http://localhost:8000/siteverify", "L3MON_AUTH_TEST_MODE": "1"}, clear=False):
        assert _get_verify_url() == "http://localhost:8000/siteverify"

    # Cloudflare domain always accepted
    cf_url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    with mock.patch.dict(os.environ, {"TURNSTILE_VERIFY_URL": cf_url, "L3MON_AUTH_TEST_MODE": "0"}, clear=False):
        assert _get_verify_url() == cf_url


def test_login_failure_tracking_and_captcha_threshold():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        env = {
            "TURNSTILE_SITEKEY": CF_TEST_PASS_SITEKEY,
            "TURNSTILE_SECRET": CF_TEST_PASS_SECRET,
        }
        with mock.patch.dict(os.environ, env, clear=False):
            # Initially no CAPTCHA required
            assert not is_login_captcha_required("target_user", "10.0.0.1")

            # Record 2 failures -> still below threshold of 3
            record_login_failure("target_user", "10.0.0.1")
            record_login_failure("target_user", "10.0.0.1")
            assert not is_login_captcha_required("target_user", "10.0.0.1")

            # 3rd failure reaches threshold
            record_login_failure("target_user", "10.0.0.1")
            assert is_login_captcha_required("target_user", "10.0.0.1")

            # Another user on different IP not affected
            assert not is_login_captcha_required("other_user", "10.0.0.99")

            # Successful login clears the counter
            clear_login_failure("target_user", "10.0.0.1")
            assert not is_login_captcha_required("target_user", "10.0.0.99")
    destroy_ctfd(app)


def test_guard_context_processor():
    env = {
        "TURNSTILE_SITEKEY": CF_TEST_PASS_SITEKEY,
        "TURNSTILE_SECRET": CF_TEST_PASS_SECRET,
    }
    with mock.patch.dict(os.environ, env, clear=False):
        ctx = get_guard_context()
        assert "captcha" in ctx
        assert ctx["captcha"]["sitekey"] == CF_TEST_PASS_SITEKEY
        assert ctx["captcha"]["register"] is True
        assert ctx["captcha"]["reset"] is True
