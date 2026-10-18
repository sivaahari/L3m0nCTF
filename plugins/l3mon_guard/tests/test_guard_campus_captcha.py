"""Tests for campus network exemption in login CAPTCHA requirements (SP5 F2).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_campus_captcha.py
"""
import os
import pytest
from tests.helpers import create_ctfd, destroy_ctfd

from CTFd.cache import cache
from CTFd.plugins.l3mon_guard.turnstile import (
    CF_TEST_PASS_SECRET,
    CF_TEST_PASS_SITEKEY,
    clear_login_failure,
    is_login_captcha_required,
    record_login_failure,
)


@pytest.fixture
def campus_env(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SITEKEY", CF_TEST_PASS_SITEKEY)
    monkeypatch.setenv("TURNSTILE_SECRET", CF_TEST_PASS_SECRET)
    monkeypatch.setenv("L3MON_GUARD_CAMPUS_NETS", "192.168.1.0/24,10.0.0.0/8,2001:db8::/32")


def test_campus_address_exempt_from_ip_failure_bucket(campus_env):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        campus_ip = "192.168.1.42"
        # 10 failures across 10 distinct accounts from the same campus IP
        for i in range(10):
            record_login_failure(f"student_{i}", campus_ip)

        # 11th distinct account logging in from that campus IP must NOT require CAPTCHA
        assert is_login_captcha_required("student_11", campus_ip) is False
    destroy_ctfd(app)


def test_non_campus_address_triggers_ip_failure_bucket(campus_env):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        external_ip = "198.51.100.99"
        # 10 failures across 10 distinct accounts from a non-campus IP
        for i in range(10):
            record_login_failure(f"attacker_target_{i}", external_ip)

        # 11th account from the external IP MUST require CAPTCHA
        assert is_login_captcha_required("innocent_user", external_ip) is True
    destroy_ctfd(app)


def test_per_account_threshold_enforced_on_both_campus_and_non_campus(campus_env):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        campus_ip = "10.5.20.100"
        external_ip = "203.0.113.55"

        # Account targeted with 3 failures from campus IP
        for _ in range(3):
            record_login_failure("victim_on_campus", campus_ip)

        # The targeted account requires CAPTCHA on campus IP
        assert is_login_captcha_required("victim_on_campus", campus_ip) is True

        # Account targeted with 3 failures from external IP
        for _ in range(3):
            record_login_failure("victim_on_external", external_ip)

        # The targeted account requires CAPTCHA on external IP
        assert is_login_captcha_required("victim_on_external", external_ip) is True
    destroy_ctfd(app)
