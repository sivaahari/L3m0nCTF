"""Tests for the username policy (SP6): the prototype's `vUsername`, enforced on the server.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_username.py
"""
import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_guard.policies.username import (
    ALL_MESSAGES,
    MSG_EMPTY,
    MSG_SHAPE,
    MSG_TAKEN,
    RESERVED_NAMES,
    validate_username,
)
from tests.helpers import create_ctfd, destroy_ctfd


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True)
    yield app
    destroy_ctfd(app)


def test_the_messages_are_the_prototypes_words():
    assert MSG_EMPTY == "Choose a username."
    assert MSG_SHAPE == "Usernames are 3 to 24 characters: letters, numbers, dots, dashes and underscores."
    assert MSG_TAKEN == "That username is taken. Try another."
    assert set(ALL_MESSAGES) == {MSG_EMPTY, MSG_SHAPE, MSG_TAKEN}


@pytest.mark.parametrize("name", ["abc", "a.b", "a-b", "a_b", "Lemon.Squad_7", "x" * 24, "A1b2C3", "9" * 5])
def test_good_names_are_kept_as_they_are(name):
    assert validate_username(name) == (name, None)


def test_the_ends_are_trimmed():
    assert validate_username("  sivaa  ") == ("sivaa", None)
    assert validate_username("sivaa\n") == ("sivaa", None)


@pytest.mark.parametrize("name", [None, "", "   ", "\t\n"])
def test_nothing_is_empty(name):
    assert validate_username(name) == (None, MSG_EMPTY)


@pytest.mark.parametrize(
    "name",
    [
        "ab",  # too short
        "x" * 25,  # too long
        "has space",
        "tab\tinside",
        "line\nbreak",
        "caf\u00e9",  # an accent
        "zero\u200bwidth",  # invisible
        "ev\u202eil",  # direction override
        "\u0430dmin_x",  # Cyrillic a, drawn like Latin a
        "emoji\U0001f34b",
        "<script>",
        "a@b.co",
        "semi;colon",
        "quo'te",
        "\uff21\uff22\uff23",  # full-width ABC
    ],
)
def test_anything_outside_letters_numbers_dots_dashes_and_underscores_is_refused(name):
    assert validate_username(name) == (None, MSG_SHAPE)


@pytest.mark.parametrize("name", sorted(RESERVED_NAMES) + ["Admin", "ROOT", "L3m0nCTF", "  Crew  "])
def test_a_reserved_name_answers_taken_ignoring_case(name):
    assert validate_username(name) == (None, MSG_TAKEN)


def test_the_reserved_list_is_the_prototypes():
    assert {"admin", "administrator", "root", "crew", "staff", "support", "system", "moderator", "owner", "security", "l3mon", "l3m0n", "lemon", "l3monctf",
            "l3m0nctf", "lemonctf", "ctfd", "organiser", "organizer", "director", "guest", "anonymous", "null", "undefined"} == set(RESERVED_NAMES)


def test_a_reserved_word_inside_a_longer_name_is_fine():
    assert validate_username("admin_fan") == ("admin_fan", None)
    assert validate_username("the.crew") == ("the.crew", None)


def test_the_crew_may_use_any_name_but_not_an_empty_one():
    assert validate_username("admin", is_crew=True) == ("admin", None)
    assert validate_username("Some Name \u00e9", is_crew=True) == ("Some Name \u00e9", None)
    assert validate_username("   ", is_crew=True) == (None, MSG_EMPTY)


def test_a_name_in_use_is_taken_ignoring_case(app):
    with app.app_context():
        db.session.add(Users(name="Sivaa", email="sivaa@example.com", password="correct-horse-battery"))
        db.session.commit()
        assert validate_username("sivaa") == (None, MSG_TAKEN)
        assert validate_username("SIVAA") == (None, MSG_TAKEN)
        assert validate_username("sivaa2") == ("sivaa2", None)


def test_a_player_may_keep_their_own_name_when_it_changes_case(app):
    with app.app_context():
        user = Users(name="Sivaa", email="sivaa@example.com", password="correct-horse-battery")
        db.session.add(user)
        db.session.commit()
        assert validate_username("SIVAA", exclude_user_id=user.id) == ("SIVAA", None)
        assert validate_username("SIVAA") == (None, MSG_TAKEN)
