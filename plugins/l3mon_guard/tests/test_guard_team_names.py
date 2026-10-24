"""Tests for the studio name policy (SP5 B4.4; the prototype's rules since SP6).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_team_names.py
"""
import pytest

from tests.helpers import create_ctfd, destroy_ctfd, gen_team

from CTFd.models import db
from CTFd.plugins.l3mon_guard.policies.team_name import (
    ALL_MESSAGES,
    MSG_CHARS,
    MSG_EMPTY,
    MSG_LENGTH,
    MSG_SCRIPTS,
    MSG_TAKEN,
    canonical_team_name,
    clean_team_name,
    validate_team_name,
)

CYR_E = "\u0435"  # Cyrillic small letter ie, drawn like a Latin "e"
GREEK_O = "\u03bf"  # Greek small letter omicron, drawn like a Latin "o"


def test_team_name_whitespace_and_unicode_cleaning():
    cleaned = clean_team_name("   Team    Alpha   \u200b  ")
    assert cleaned == "Team Alpha"

    # Confusable non-breaking space
    cleaned_nbsp = clean_team_name("Team\u00a0Beta")
    assert cleaned_nbsp == "Team Beta"


def test_a_tab_or_a_line_break_between_words_is_a_space_as_the_browser_reads_it():
    assert clean_team_name("Team\tAlpha") == "Team Alpha"
    assert clean_team_name("Team\r\nAlpha") == "Team Alpha"


def test_zero_width_and_direction_characters_are_removed_from_the_name_that_is_kept():
    assert validate_team_name("Zero\u200bWidth") == ("ZeroWidth", None)
    assert validate_team_name("Evil\u202eStudio") == ("EvilStudio", None)


def test_the_messages_are_the_prototypes_words():
    assert MSG_EMPTY == "Choose a studio name."
    assert MSG_LENGTH == "Studio names are 2 to 32 characters."
    assert MSG_CHARS == "Use letters, numbers, spaces and punctuation only."
    assert MSG_SCRIPTS == "Do not mix letters from different alphabets in one name."
    assert MSG_TAKEN == "That studio name is taken."
    assert set(ALL_MESSAGES) == {MSG_EMPTY, MSG_LENGTH, MSG_CHARS, MSG_SCRIPTS, MSG_TAKEN}


def test_team_name_impersonation_prevention_and_crew_exemption():
    # Non-crew attempts: a reserved word answers "taken", so the list is not revealed
    for name in ("Admin Crew", "Official l3mon staff", "Moderator"):
        assert validate_team_name(name, is_crew=False) == (None, MSG_TAKEN)

    # Substring is permitted if not whole word
    clean4, err4 = validate_team_name("Administering Hackers", is_crew=False)
    assert err4 is None
    assert clean4 == "Administering Hackers"

    # Crew exemption
    clean_crew, err_crew = validate_team_name("Admin Team", is_crew=True)
    assert err_crew is None
    assert clean_crew == "Admin Team"


def test_the_crew_is_exempt_from_the_reserved_words_only():
    assert validate_team_name("Q", is_crew=True)[1] == MSG_LENGTH
    assert validate_team_name("X" * 33, is_crew=True)[1] == MSG_LENGTH
    assert validate_team_name("Mix\u0435d", is_crew=True)[1] == MSG_SCRIPTS


def test_team_name_collision_casefold():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        gen_team(db, name="Alpha Studio")

        # Collision with case folding and extra spaces
        _, err1 = validate_team_name("alpha studio")
        assert err1 == MSG_TAKEN

        _, err2 = validate_team_name("ALPHA   STUDIO")
        assert err2 == MSG_TAKEN

        # Different name allowed
        clean3, err3 = validate_team_name("Beta Studio")
        assert err3 is None
        assert clean3 == "Beta Studio"
    destroy_ctfd(app)


def test_a_studio_may_keep_its_own_name_when_it_renames():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        team = gen_team(db, name="Alpha Studio")
        assert validate_team_name("ALPHA studio", exclude_team_id=team.id) == ("ALPHA studio", None)
        assert validate_team_name("ALPHA studio")[1] == MSG_TAKEN
    destroy_ctfd(app)


def test_a_name_in_one_alphabet_that_reads_like_an_existing_one_is_taken():
    """All-Cyrillic letters that are drawn like Latin ones: "camo" written with Cyrillic c, a, m, o (the common look-alike trick)."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        gen_team(db, name="\u0441\u0430\u043c\u043e")
        assert validate_team_name("Camo")[1] == MSG_TAKEN
        assert validate_team_name("CAMO")[1] == MSG_TAKEN
        # and the other way round
    destroy_ctfd(app)

    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        gen_team(db, name="Camo")
        assert validate_team_name("\u0441\u0430\u043c\u043e")[1] == MSG_TAKEN
    destroy_ctfd(app)


def test_canonical_form_reads_look_alike_letters_as_the_latin_letter():
    assert canonical_team_name("\u0430dmin") == "admin"
    assert canonical_team_name("R" + GREEK_O + GREEK_O + "T") == "root"
    assert canonical_team_name("\uff33\uff54\uff55\uff44\uff49\uff4f") == "studio"  # full-width forms: NFKC
    assert canonical_team_name("  Mixed   CASE ") == "mixed case"


@pytest.mark.parametrize("name", ["Oth" + CYR_E + "r Studio", "Pr" + GREEK_O + "ject", "\u0410dmin", "Team \u0410lpha"])
def test_letters_of_two_alphabets_in_one_name_are_refused(name):
    assert validate_team_name(name) == (None, MSG_SCRIPTS)


def test_one_alphabet_alone_is_fine():
    assert validate_team_name("\u041a\u043e\u043c\u0430\u043d\u0434\u0430")[1] is None  # a Russian word
    assert validate_team_name("\u039f\u03bc\u03ac\u03b4\u03b1")[1] is None  # a Greek word
    assert validate_team_name("Caf\u00e9 N\u00fa\u00f1ez")[1] is None  # accented Latin
    assert validate_team_name("\u30c1\u30fc\u30e0 7")[1] is None  # Japanese with a digit


@pytest.mark.parametrize("name", ["Team\ue000X", "Team\u0600X", "Team\u0378X"])
def test_a_character_that_draws_nothing_is_refused(name):
    """Private-use, format and unassigned characters that the cleaning does not know are refused, not stored."""
    assert validate_team_name(name) == (None, MSG_CHARS)


def test_line_and_paragraph_separators_are_spaces():
    assert validate_team_name("Team\u2028Alpha") == ("Team Alpha", None)
    assert validate_team_name("Team\u2029Alpha") == ("Team Alpha", None)


def test_length_is_two_to_thirty_two_characters_as_the_browser_counts_them():
    assert validate_team_name("") == (None, MSG_EMPTY)
    assert validate_team_name("   ") == (None, MSG_EMPTY)
    assert validate_team_name(None) == (None, MSG_EMPTY)
    assert validate_team_name("A") == (None, MSG_LENGTH)
    assert validate_team_name("AB") == ("AB", None)
    assert validate_team_name("X" * 32) == ("X" * 32, None)
    assert validate_team_name("X" * 33) == (None, MSG_LENGTH)
    assert validate_team_name("X" * 65) == (None, MSG_LENGTH)
    # an emoji is one character, not two (the browser counts code points)
    assert validate_team_name("\U0001f34b") == (None, MSG_LENGTH)
    assert validate_team_name("\U0001f34b\U0001f34b") == ("\U0001f34b\U0001f34b", None)
    assert validate_team_name("\U0001f34b" * 32)[1] is None
    assert validate_team_name("\U0001f34b" * 33)[1] == MSG_LENGTH


def test_only_the_cleaned_length_counts():
    assert validate_team_name("  A  ")[1] == MSG_LENGTH
    assert validate_team_name("A\u200bB") == ("AB", None)
