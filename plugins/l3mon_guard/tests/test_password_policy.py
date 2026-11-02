"""Tests for password policy validation (SP5 / Stream C audit C7)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import pytest

try:
    from CTFd.plugins.l3mon_guard.policies.password import (
        validate_password,
        V_LIMITS_PASS_MIN,
        V_LIMITS_PASS_MAX,
        V_MSG_PASS_EMPTY,
        V_MSG_PASS_SHORT,
        V_MSG_PASS_LONG,
        V_MSG_PASS_COMMON,
        V_MSG_PASS_USER,
        V_MSG_PASS_EMAIL,
    )
except ModuleNotFoundError:
    import importlib.util
    spec = importlib.util.spec_from_file_location("password", str(Path(__file__).resolve().parents[1] / "policies" / "password.py"))
    password_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(password_mod)
    validate_password = password_mod.validate_password
    V_LIMITS_PASS_MIN = password_mod.V_LIMITS_PASS_MIN
    V_LIMITS_PASS_MAX = password_mod.V_LIMITS_PASS_MAX
    V_MSG_PASS_EMPTY = password_mod.V_MSG_PASS_EMPTY
    V_MSG_PASS_SHORT = password_mod.V_MSG_PASS_SHORT
    V_MSG_PASS_LONG = password_mod.V_MSG_PASS_LONG
    V_MSG_PASS_COMMON = password_mod.V_MSG_PASS_COMMON
    V_MSG_PASS_USER = password_mod.V_MSG_PASS_USER
    V_MSG_PASS_EMAIL = password_mod.V_MSG_PASS_EMAIL

FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "golden_password.json")


def test_password_empty():
    ok, err = validate_password("")
    assert not ok
    assert err == V_MSG_PASS_EMPTY

    ok, err = validate_password(None)
    assert not ok
    assert err == V_MSG_PASS_EMPTY


def test_password_length_boundaries():
    # 11 characters -> short
    ok, err = validate_password("aB3!eF5#hI7")
    assert not ok
    assert err == V_MSG_PASS_SHORT

    # 12 characters -> ok (non-pattern, non-common)
    ok, err = validate_password("aB3!eF5#hI7$")
    assert ok
    assert err is None

    # 128 characters -> ok
    long_128 = "aB3!eF5#hI7$" * 10 + "12345678"
    assert len(long_128) == 128
    ok, err = validate_password(long_128)
    assert ok
    assert err is None

    # 129 characters -> long
    long_129 = long_128 + "x"
    assert len(long_129) == 129
    ok, err = validate_password(long_129)
    assert not ok
    assert err == V_MSG_PASS_LONG


def test_password_username_context():
    ok, err = validate_password("SecretalicePassword123!", username="alice")
    assert not ok
    assert err == V_MSG_PASS_USER

    # Short username (< 3 chars) is ignored
    ok, err = validate_password("SecretalPassword123!", username="al")
    assert ok
    assert err is None


def test_password_email_context():
    ok, err = validate_password("SecretalicePassword123!", email="alice@example.com")
    assert not ok
    assert err == V_MSG_PASS_EMAIL

    # Short localpart (< 3 chars) is ignored
    ok, err = validate_password("SecretalPassword123!", email="al@example.com")
    assert ok
    assert err is None


def test_password_weak_patterns():
    # Too few distinct characters
    ok, err = validate_password("aaaaaaaaaaaa")
    assert not ok
    assert err == V_MSG_PASS_COMMON

    # Repeated block
    ok, err = validate_password("abcabcabcabc")
    assert not ok
    assert err == V_MSG_PASS_COMMON

    # Keyboard / counting runs
    ok, err = validate_password("123456789012")
    assert not ok
    assert err == V_MSG_PASS_COMMON

    ok, err = validate_password("qwertyuiopas")
    assert not ok
    assert err == V_MSG_PASS_COMMON


def test_password_common_words():
    ok, err = validate_password("Password1234!")
    assert not ok
    assert err == V_MSG_PASS_COMMON

    ok, err = validate_password("l3m0nCTF2026!")
    assert not ok
    assert err == V_MSG_PASS_COMMON

    ok, err = validate_password("letmeinletmein")
    assert not ok
    assert err == V_MSG_PASS_COMMON


def test_password_golden_cases():
    assert os.path.exists(FIXTURES_PATH), f"Fixture not found at {FIXTURES_PATH}"
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    for item in cases:
        inp = item["input"]
        expected = item["expected"]
        ok, err = validate_password(inp["pw"], username=inp["ctx"].get("username", ""), email=inp["ctx"].get("email", ""))
        if expected is None:
            assert ok is True, f"Expected {inp['pw']!r} to be valid"
            assert err is None
        else:
            assert ok is False, f"Expected {inp['pw']!r} to fail with {expected!r}, got {err!r}"
            assert err == expected


@pytest.fixture()
def app():
    from tests.helpers import create_ctfd, destroy_ctfd
    app = create_ctfd(enable_plugins=True)
    app.config["L3MON_SIGNUP_RULES"] = True
    yield app
    destroy_ctfd(app)


def test_user_api_patch_wrappers_installed(app):
    """C2: verify the wrapper is installed on real CTFd endpoints api.users_user_private and api.users_user_public."""
    assert hasattr(app.view_functions["api.users_user_private"], "__wrapped__"), "api.users_user_private must be wrapped"
    assert hasattr(app.view_functions["api.users_user_public"], "__wrapped__"), "api.users_user_public must be wrapped"


def test_register_route_enforces_password_policy(app):
    with app.app_context():
        with app.test_client() as client:
            client.get("/register")
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")
            r = client.post(
                "/register",
                data={
                    "name": "validname",
                    "email": "validname@example.com",
                    "password": "short",
                    "privacy": "1",
                    "nonce": nonce,
                    "_submit": "Submit",
                },
            )
            assert r.status_code == 200
            assert "Use at least 12 characters." in r.get_data(as_text=True)


def test_register_with_privacy_box_and_short_password_fails_and_creates_no_user(app):
    """C2: With privacy box ticked and short password 'abc', answer is 200 with error and no user created."""
    from CTFd.models import Users
    with app.app_context():
        with app.test_client() as client:
            client.get("/register")
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")
            r = client.post(
                "/register",
                data={
                    "name": "shortpwuser",
                    "email": "shortpw@example.com",
                    "password": "abc",
                    "privacy": "1",
                    "nonce": nonce,
                    "_submit": "Submit",
                },
            )
            assert r.status_code == 200
            assert "Use at least 12 characters." in r.get_data(as_text=True)
            assert Users.query.filter_by(email="shortpw@example.com").first() is None


def test_reset_password_route_enforces_password_policy(app):
    from CTFd.cache import cache
    from CTFd.models import Users, db
    with app.app_context():
        u = Users(name="resetuser", email="resetuser@example.com", password="OldPassword1234!")
        db.session.add(u)
        db.session.commit()
        token = "test_reset_token_1234"
        cache.set(f"reset_password_{token}", u.email)

        with app.test_client() as client:
            client.get(f"/reset_password/{token}")
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")
            r = client.post(
                f"/reset_password/{token}",
                data={
                    "password": "short",
                    "nonce": nonce,
                },
            )
            assert r.status_code == 200
            assert "Use at least 12 characters." in r.get_data(as_text=True)


def test_user_me_settings_patch_enforces_password_policy(app):
    from CTFd.models import Users, db
    from tests.helpers import login_as_user
    with app.app_context():
        u = Users(name="patchuser", email="patchuser@example.com", password="OldPassword1234!")
        db.session.add(u)
        db.session.commit()

        client = login_as_user(app, name="patchuser", password="OldPassword1234!")
        r = client.patch(
            "/api/v1/users/me",
            json={"password": "short"},
        )
        assert r.status_code == 400
        data = r.get_json()
        assert data["success"] is False
        assert "Use at least 12 characters." in data["errors"]["password"]


def test_register_route_refuses_spaces_surrounding_short_password(app):
    """C2: 11 spaces plus ab1 is refused on register."""
    with app.app_context():
        with app.test_client() as client:
            client.get("/register")
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")
            # 11 spaces + ab1 -> length 14, but stripped length is 3 (< 12)
            r = client.post(
                "/register",
                data={
                    "name": "spaced_player",
                    "email": "spaced_player@example.com",
                    "password": "           ab1",
                    "privacy": "1",
                    "nonce": nonce,
                    "_submit": "Submit",
                },
            )
            assert r.status_code == 200
            assert "Use at least 12 characters." in r.get_data(as_text=True)

            # Spaces before and after
            client.get("/register")
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")
            r2 = client.post(
                "/register",
                data={
                    "name": "spaced_player2",
                    "email": "spaced_player2@example.com",
                    "password": "   ab1   ",
                    "privacy": "1",
                    "nonce": nonce,
                    "_submit": "Submit",
                },
            )
            assert r2.status_code == 200
            assert "Use at least 12 characters." in r2.get_data(as_text=True)


def test_reset_password_route_refuses_spaces_surrounding_short_password(app):
    """C2: 11 spaces plus ab1 is refused on reset_password."""
    from CTFd.cache import cache
    from CTFd.models import Users, db
    with app.app_context():
        u = Users(name="reset_space_user", email="reset_space@example.com", password="OldPassword1234!")
        db.session.add(u)
        db.session.commit()
        token = "test_reset_token_spaces"
        cache.set(f"reset_password_{token}", u.email)

        with app.test_client() as client:
            client.get(f"/reset_password/{token}")
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")
            r = client.post(
                f"/reset_password/{token}",
                data={
                    "password": "           ab1",
                    "nonce": nonce,
                },
            )
            assert r.status_code == 200
            assert "Use at least 12 characters." in r.get_data(as_text=True)

            client.get(f"/reset_password/{token}")
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")
            r2 = client.post(
                f"/reset_password/{token}",
                data={
                    "password": "   ab1   ",
                    "nonce": nonce,
                },
            )
            assert r2.status_code == 200
            assert "Use at least 12 characters." in r2.get_data(as_text=True)


def test_user_me_settings_patch_refuses_spaces_surrounding_short_password(app):
    """C2: 11 spaces plus ab1 is refused on PATCH /api/v1/users/me and /api/v1/users/<id>."""
    from CTFd.models import Users, db
    from tests.helpers import login_as_user
    with app.app_context():
        u = Users(name="patch_space_user", email="patch_space@example.com", password="OldPassword1234!")
        db.session.add(u)
        db.session.commit()

        client = login_as_user(app, name="patch_space_user", password="OldPassword1234!")
        r = client.patch(
            "/api/v1/users/me",
            json={"password": "           ab1"},
        )
        assert r.status_code == 400
        data = r.get_json()
        assert data["success"] is False
        assert "Use at least 12 characters." in data["errors"]["password"]

        r2 = client.patch(
            "/api/v1/users/me",
            json={"password": "   ab1   "},
        )
        assert r2.status_code == 400
        data2 = r2.get_json()
        assert data2["success"] is False
        assert "Use at least 12 characters." in data2["errors"]["password"]


def test_user_public_api_patch_enforces_password_policy(app):
    """C2: PATCH /api/v1/users/<id> (crew) enforces password policy on target user."""
    from CTFd.models import Users, db
    from tests.helpers import login_as_user
    with app.app_context():
        crew_pw = "Lemon-Crew-Passphrase-2026"
        target_pw = "Lemon-Player-Passphrase-2026"
        u_admin = Users(name="admin_crew", email="admin_crew@example.com", password=crew_pw, type="admin", verified=True)
        u_target = Users(name="target_player", email="target@example.com", password=target_pw, verified=True)
        db.session.add(u_admin)
        db.session.add(u_target)
        db.session.commit()
        target_id = u_target.id

        client = login_as_user(app, name="admin_crew", password=crew_pw)
        r = client.patch(
            f"/api/v1/users/{target_id}",
            json={"password": "short"},
        )
        assert r.status_code == 400
        data = r.get_json()
        assert data["success"] is False
        assert "Use at least 12 characters." in data["errors"]["password"]


def _answer_text(response):
    """Everything in an answer that would tell the asker something about the account whose id is in the address."""
    return response.get_data(as_text=True)


def test_a_visitor_cannot_use_the_password_check_to_learn_a_name_or_an_address(app):
    """The password rule judges only a request CTFd would let through (merge of the four streams, found by the lead).
    Before: a visitor with a session token could PATCH /api/v1/users/<id> with a password and read 'Do not use your username in your password'
    for any player, which says whether the guess contains that player's name or the start of their address."""
    from CTFd.models import Users, db
    with app.app_context():
        target = Users(name="victimplayer", email="victimmail@example.com", password="Lemon-Player-Passphrase-2026", verified=True)
        db.session.add(target)
        db.session.commit()
        target_id = target.id
        with app.test_client() as client:
            client.get("/register")  # a session and its token, as a browser has
            for guess in ("my-victimplayer-secret-99", "my-victimmail-secret-99", "short"):
                for path in (f"/api/v1/users/{target_id}", "/api/v1/users/me"):
                    response = client.patch(path, json={"password": guess})
                    assert response.status_code in (401, 403), (path, guess, response.status_code, _answer_text(response)[:200])
                    text = _answer_text(response)
                    for sentence in (V_MSG_PASS_USER, V_MSG_PASS_EMAIL, V_MSG_PASS_SHORT, V_MSG_PASS_COMMON):
                        assert sentence not in text, (path, guess, sentence)


def test_a_player_cannot_use_the_crews_call_to_probe_the_password_check(app):
    """A signed-in player asking about another player gets CTFd's 403, not a verdict that reads the other account."""
    from CTFd.models import Users, db
    from tests.helpers import login_as_user
    with app.app_context():
        target = Users(name="victimplayer", email="victimmail@example.com", password="Lemon-Player-Passphrase-2026", verified=True)
        asker = Users(name="askingplayer", email="asking@example.com", password="Lemon-Asker-Passphrase-2026", verified=True)
        db.session.add_all([target, asker])
        db.session.commit()
        target_id = target.id
        client = login_as_user(app, name="askingplayer", password="Lemon-Asker-Passphrase-2026")
        for guess in ("my-victimplayer-secret-99", "my-victimmail-secret-99", "short"):
            response = client.patch(f"/api/v1/users/{target_id}", json={"password": guess})
            assert response.status_code == 403, (guess, response.status_code, _answer_text(response)[:200])
            text = _answer_text(response)
            for sentence in (V_MSG_PASS_USER, V_MSG_PASS_EMAIL, V_MSG_PASS_SHORT, V_MSG_PASS_COMMON):
                assert sentence not in text, (guess, sentence)


def test_the_crew_is_still_judged_on_the_account_it_changes_and_a_player_on_their_own(app):
    """The guard above must not loosen the rule where CTFd lets the call through."""
    from CTFd.models import Users, db
    from tests.helpers import login_as_user
    with app.app_context():
        crew = Users(name="crewmember", email="crewmember@example.com", password="Lemon-Crew-Passphrase-2026", type="admin", verified=True)
        target = Users(name="victimplayer", email="victimmail@example.com", password="Lemon-Player-Passphrase-2026", verified=True)
        db.session.add_all([crew, target])
        db.session.commit()
        target_id = target.id
        crew_client = login_as_user(app, name="crewmember", password="Lemon-Crew-Passphrase-2026")
        response = crew_client.patch(f"/api/v1/users/{target_id}", json={"password": "my-victimplayer-secret-99"})
        assert response.status_code == 400
        assert V_MSG_PASS_USER in response.get_json()["errors"]["password"]
        player_client = login_as_user(app, name="victimplayer", password="Lemon-Player-Passphrase-2026")
        response = player_client.patch("/api/v1/users/me", json={"password": "my-victimmail-secret-99"})
        assert response.status_code == 400
        assert V_MSG_PASS_EMAIL in response.get_json()["errors"]["password"]


if __name__ == "__main__":
    test_password_empty()
    test_password_length_boundaries()
    test_password_username_context()
    test_password_email_context()
    test_password_weak_patterns()
    test_password_common_words()
    test_password_golden_cases()
    print("ALL 7 PASSWORD POLICY UNIT TESTS PASSED!")
