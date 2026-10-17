"""Tests for disposable domain blocking and disguised email normalization (SP5 B4.2, B4.3).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_policies.py
"""
from tests.helpers import create_ctfd, destroy_ctfd, register_user

from CTFd.plugins.l3mon_guard.policies.disposable import is_disposable_domain
from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used, normalize_email


def test_disposable_exact_and_subdomain_matching():
    assert is_disposable_domain("player@mailinator.com") is True
    assert is_disposable_domain("player@sub.mailinator.com") is True
    assert is_disposable_domain("player@tempmail.com") is True
    assert is_disposable_domain("player@gmail.com") is False
    assert is_disposable_domain("player@university.edu") is False
    assert is_disposable_domain("") is False


def test_email_normalization_rules():
    # Plus tags removed
    norm, err = normalize_email("alice+spam@example.com")
    assert err is None
    assert norm == "alice@example.com"

    # Gmail dots removed & googlemail treated as gmail
    norm, err = normalize_email("a.l.i.c.e+newsletter@googlemail.com")
    assert err is None
    assert norm == "alice@gmail.com"

    norm, err = normalize_email("bob.builder@gmail.com")
    assert err is None
    assert norm == "bobbuilder@gmail.com"

    # Zero-width / bidi control characters refused
    norm, err = normalize_email("hacker\u200b@gmail.com")
    assert norm is None
    assert err is not None

    norm, err = normalize_email("evil\u202e@gmail.com")
    assert norm is None
    assert err is not None


def test_disguised_email_collision_against_existing_user():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        # Register user with standard gmail
        register_user(app, name="victim", email="victim@gmail.com")

        # Disguised forms must be detected
        assert is_email_disguise_used("victim+alias@gmail.com") is True
        assert is_email_disguise_used("v.i.c.t.i.m@googlemail.com") is True
        assert is_email_disguise_used("VICTIM@gmail.com") is True

        # Unrelated user
        assert is_email_disguise_used("someone.else@gmail.com") is False
    destroy_ctfd(app)
