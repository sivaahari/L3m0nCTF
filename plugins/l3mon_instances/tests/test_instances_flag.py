"""Tests for l3mon_dynamic flag type and per-team flag derivation."""
from types import SimpleNamespace
from unittest import mock

import pytest

from CTFd.models import Flags, db
from CTFd.plugins.l3mon_instances.flag import (
    L3monDynamicFlag,
    derive_team_flag,
    is_valid_flag_shape,
)
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_flag, login_as_user, register_user

_PREFIX = "L3m0n" + "{"
_SUFFIX = "}"


def test_flag_derivation_vectors():
    """Verify test vectors from specification B3.1."""
    key = "test-secret-value"

    # Team 7, challenge 42 -> digits 94fa853e968cd402000a376fae89470d
    flag1 = derive_team_flag(key, 7, 42)
    assert flag1 == _PREFIX + "94fa853e968cd402000a376fae89470d" + _SUFFIX

    # Team 8, challenge 42 -> digits d6c753d8dea03a34b65ee9e4676ce3c3
    flag2 = derive_team_flag(key, 8, 42)
    assert flag2 == _PREFIX + "d6c753d8dea03a34b65ee9e4676ce3c3" + _SUFFIX

    # Team 7, challenge 43 -> digits 435b00a57ad252e91c15db1238e489f6
    flag3 = derive_team_flag(key, 7, 43)
    assert flag3 == _PREFIX + "435b00a57ad252e91c15db1238e489f6" + _SUFFIX


def test_flag_shape_validation():
    valid = _PREFIX + "94fa853e968cd402000a376fae89470d" + _SUFFIX
    assert is_valid_flag_shape(valid) is True

    # Uppercase hex is invalid
    assert is_valid_flag_shape(_PREFIX + "94FA853E968CD402000A376FAE89470D" + _SUFFIX) is False

    # Short or long hex
    assert is_valid_flag_shape(_PREFIX + "94fa853e" + _SUFFIX) is False
    assert is_valid_flag_shape(_PREFIX + "94fa853e968cd402000a376fae89470d1234" + _SUFFIX) is False

    # Trailing characters / newline
    assert is_valid_flag_shape(valid + "\n") is False
    assert is_valid_flag_shape(" " + valid) is False


def test_dynamic_flag_compare():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        chal = gen_challenge(db, name="Dynamic Chal")
        chal_id = chal.id
        chal_key = gen_flag(db, challenge_id=chal_id, type="l3mon_dynamic", content="")
        key_id = chal_key.id
        db.session.commit()

        # User and team
        register_user(app, name="player1", email="player1@example.com")
        client = login_as_user(app, "player1")
        client.get("/team")
        with client.session_transaction() as sess:
            nonce = sess.get("nonce")
        r = client.post("/teams/new", data={"name": "team1", "password": "password", "nonce": nonce})
        assert r.status_code == 302

        from CTFd.models import Teams, Users
        user = Users.query.filter_by(name="player1").first()
        team = Teams.query.filter_by(name="team1").first()
        key_row = Flags.query.get(key_id)

        secret = "test-secret-value"
        with mock.patch("CTFd.plugins.l3mon_instances.flag.get_flag_secret", return_value=secret):
            # Test when request context has current team
            with app.test_request_context():
                with mock.patch("CTFd.plugins.l3mon_instances.flag.get_current_team", return_value=team):
                    correct_flag = derive_team_flag(secret, team.id, chal_id)
                    other_team_flag = derive_team_flag(secret, team.id + 1, chal_id)

                    # Right team flag matches
                    assert L3monDynamicFlag.compare(key_row, correct_flag) is True

                    # Other team flag does not match
                    assert L3monDynamicFlag.compare(key_row, other_team_flag) is False

                    # Wrong flag does not match
                    assert L3monDynamicFlag.compare(key_row, "wrong_flag") is False

                    # Empty flag does not match
                    assert L3monDynamicFlag.compare(key_row, "") is False

                # Player without team
                with mock.patch("CTFd.plugins.l3mon_instances.flag.get_current_team", return_value=None):
                    assert L3monDynamicFlag.compare(key_row, correct_flag) is False

    destroy_ctfd(app)


@pytest.mark.parametrize("provided", ["é" * 40, _PREFIX + "ünï" + _SUFFIX, "\u202e" + _PREFIX + "0" * 32 + _SUFFIX, "日本語のフラグ", "\ud800"])
def test_a_submission_with_non_ascii_text_is_simply_wrong_and_never_an_error(provided):
    """Found by the integration audit: hmac.compare_digest on two str raises TypeError when either holds a non-ASCII character, so one accented letter
    in a submitted flag made CTFd's flag check raise and the player got a server error instead of 'incorrect'."""
    team = SimpleNamespace(id=7)
    row = SimpleNamespace(challenge_id=42)
    with mock.patch("CTFd.plugins.l3mon_instances.flag.get_flag_secret", return_value="test-secret-value"), \
            mock.patch("CTFd.plugins.l3mon_instances.flag.get_current_team", return_value=team):
        assert L3monDynamicFlag.compare(row, provided) is False


def test_the_right_flag_is_still_right_after_the_comparison_moved_to_bytes():
    team = SimpleNamespace(id=7)
    row = SimpleNamespace(challenge_id=42)
    with mock.patch("CTFd.plugins.l3mon_instances.flag.get_flag_secret", return_value="test-secret-value"), \
            mock.patch("CTFd.plugins.l3mon_instances.flag.get_current_team", return_value=team):
        assert L3monDynamicFlag.compare(row, derive_team_flag("test-secret-value", 7, 42)) is True
        assert L3monDynamicFlag.compare(row, derive_team_flag("test-secret-value", 8, 42)) is False
