"""The sign-up rules switch (`signup_rules.enforced`): always on for a real platform, off for CTFd's own test helper unless a test turns it on.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core/test_signup_rules.py
"""
import pytest

from CTFd.plugins.l3mon_core.signup_rules import enforced
from tests.helpers import create_ctfd, destroy_ctfd


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True)
    yield app
    destroy_ctfd(app)


def test_a_test_app_does_not_enforce_them_by_default(app):
    assert app.testing is True
    with app.app_context():
        assert enforced() is False


def test_a_test_can_turn_them_on(app):
    app.config["L3MON_SIGNUP_RULES"] = True
    with app.app_context():
        assert enforced() is True


def test_a_real_platform_always_enforces_them(app):
    app.testing = False
    with app.app_context():
        assert enforced() is True


def test_nothing_in_the_environment_switches_them_off_on_a_real_platform(app, monkeypatch):
    """CTFd builds its settings from its Config class (`app.config.from_object`), which reads named environment variables only."""
    monkeypatch.setenv("L3MON_SIGNUP_RULES", "0")
    app.testing = False
    app.config.pop("L3MON_SIGNUP_RULES", None)
    with app.app_context():
        assert enforced() is True


def test_an_explicit_false_is_honoured_for_a_test_that_wants_the_rules_off(app):
    app.testing = False
    app.config["L3MON_SIGNUP_RULES"] = False
    with app.app_context():
        assert enforced() is False
