"""Tests for team name policy (SP5 B4.4).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_team_names.py
"""
from tests.helpers import create_ctfd, destroy_ctfd, gen_team

from CTFd.models import db
from CTFd.plugins.l3mon_guard.policies.team_name import (
    canonical_team_name,
    clean_team_name,
    validate_team_name,
)


def test_team_name_whitespace_and_unicode_cleaning():
    cleaned = clean_team_name("   Team    Alpha   \u200b  ")
    assert cleaned == "Team Alpha"

    # Confusable non-breaking space
    cleaned_nbsp = clean_team_name("Team\u00a0Beta")
    assert cleaned_nbsp == "Team Beta"


def test_team_name_impersonation_prevention_and_crew_exemption():
    # Non-crew attempts
    _, err1 = validate_team_name("Admin Crew", is_crew=False)
    assert err1 == "That team name is reserved."

    _, err2 = validate_team_name("Official l3mon staff", is_crew=False)
    assert err2 == "That team name is reserved."

    _, err3 = validate_team_name("Moderator", is_crew=False)
    assert err3 == "That team name is reserved."

    # Substring is permitted if not whole word
    clean4, err4 = validate_team_name("Administering Hackers", is_crew=False)
    assert err4 is None
    assert clean4 == "Administering Hackers"

    # Crew exemption
    clean_crew, err_crew = validate_team_name("Admin Team", is_crew=True)
    assert err_crew is None
    assert clean_crew == "Admin Team"


def test_team_name_collision_casefold():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        gen_team(db, name="Alpha Studio")

        # Collision with case folding and extra spaces
        _, err1 = validate_team_name("alpha studio")
        assert err1 == "That team name is already taken."

        _, err2 = validate_team_name("ALPHA   STUDIO")
        assert err2 == "That team name is already taken."

        # Different name allowed
        clean3, err3 = validate_team_name("Beta Studio")
        assert err3 is None
        assert clean3 == "Beta Studio"
    destroy_ctfd(app)


def test_team_name_length_boundaries():
    _, err_empty = validate_team_name("")
    assert err_empty is not None

    long_name = "X" * 65
    _, err_long = validate_team_name(long_name)
    assert err_long is not None

    exact_64 = "X" * 64
    clean_exact, err_exact = validate_team_name(exact_64)
    assert err_exact is None
    assert len(clean_exact) == 64
