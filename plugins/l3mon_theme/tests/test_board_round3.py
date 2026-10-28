"""Stream A, round 3 (the real-browser finding of 25 October 2026):

  * GET and HEAD /challenges from any signed-in account redirects 302 to /board;
  * a visitor keeps CTFd's own sign-in redirect with next=/challenges;
  * only /challenges and only GET/HEAD: other methods and subpaths are untouched;
  * the hook costs nothing on other paths (0 statements);
  * the redirect happens before CTFd's 403 (with the hook disabled, player gets 403).

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_board_round3.py
"""
import re
from unittest import mock

import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_theme.pages import board as page
from CTFd.utils import set_config
from theme_a_world import PASSWORD, T_END, T_LIVE, T_START, clock, play, register_user, statements_of, text  # noqa: F401
from tests.helpers import login_as_user


# ---- A1: GET and HEAD /challenges redirects 302 to /board for any signed-in account ------------------


@pytest.mark.parametrize("method", ["get", "head"])
def test_before_the_start_signed_in_player_and_crew_are_redirected_to_board(play, method):
    set_config("start", T_LIVE + 7200)
    set_config("end", T_END)
    with clock(T_LIVE):
        for client in (play.alice, play.admin):
            r = getattr(client, method)("/challenges")
            assert r.status_code == 302, f"{method.upper()} /challenges status was {r.status_code}"
            assert r.headers["Location"].endswith("/board"), r.headers["Location"]

        # following the redirect lands on the "not on air yet" slate
        followed = play.alice.get("/challenges", follow_redirects=True)
        assert followed.status_code == 200
        assert 'data-slate="before"' in text(followed)


def test_before_the_start_hook_disabled_on_purpose_proves_redirect_happens_before_ctfds_403(play):
    """Proves the redirect happens BEFORE CTFd's 403: turning the hook off makes the player hit CTFd's 403."""
    set_config("start", T_LIVE + 7200)
    set_config("end", T_END)
    with clock(T_LIVE):
        with mock.patch.dict(page.HOOKS, {"redirect_challenges": False}):
            r = play.alice.get("/challenges")
            assert r.status_code == 403, "without the hook, CTFd gives 403 before the start"


@pytest.mark.parametrize("method", ["get", "head"])
def test_live_signed_in_player_and_crew_are_redirected_to_board(play, method):
    set_config("start", T_START)
    set_config("end", T_END)
    set_config("paused", False)
    with clock(T_LIVE):
        for client in (play.alice, play.admin):
            r = getattr(client, method)("/challenges")
            assert r.status_code == 302
            assert r.headers["Location"].endswith("/board")

        followed = play.alice.get("/challenges", follow_redirects=True)
        assert followed.status_code == 200
        assert 'data-board' in text(followed)


@pytest.mark.parametrize("method", ["get", "head"])
def test_paused_signed_in_player_is_redirected_to_board(play, method):
    set_config("start", T_START)
    set_config("end", T_END)
    set_config("paused", True)
    with clock(T_LIVE):
        r = getattr(play.alice, method)("/challenges")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/board")

        followed = play.alice.get("/challenges", follow_redirects=True)
        assert followed.status_code == 200
        assert 'data-banner="paused"' in text(followed)


@pytest.mark.parametrize("method", ["get", "head"])
def test_ended_signed_in_player_is_redirected_to_board(play, method):
    set_config("start", T_START)
    set_config("end", T_END)
    set_config("view_after_ctf", True)
    with clock(T_END + 60):
        r = getattr(play.alice, method)("/challenges")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/board")

        followed = play.alice.get("/challenges", follow_redirects=True)
        assert followed.status_code == 200
        assert 'data-banner="wrap"' in text(followed)


@pytest.mark.parametrize("method", ["get", "head"])
def test_frozen_signed_in_player_is_redirected_to_board(play, method):
    set_config("start", T_START)
    set_config("end", T_END)
    set_config("freeze", T_START + 600)
    with clock(T_LIVE):
        r = getattr(play.alice, method)("/challenges")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/board")

        followed = play.alice.get("/challenges", follow_redirects=True)
        assert followed.status_code == 200
        assert 'data-banner="frozen"' in text(followed)


@pytest.mark.parametrize("method", ["get", "head"])
def test_player_with_no_studio_is_redirected_to_board_and_forwarded_by_gates(play, method):
    register_user(play.app, name=f"loner_{method}", email=f"loner_{method}@example.com", password=PASSWORD)
    loner = login_as_user(play.app, f"loner_{method}", PASSWORD)
    with clock(T_LIVE):
        r = getattr(loner, method)("/challenges")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/board")

        # Following to /board sends them on to make a studio (/team)
        at_board = loner.get(r.headers["Location"])
        assert at_board.status_code == 302
        assert "/team" in at_board.headers["Location"]


@pytest.mark.parametrize("method", ["get", "head"])
def test_suspended_player_is_turned_away(play, method):
    user = Users.query.filter_by(name="alice").one()
    user.banned = True
    db.session.commit()
    try:
        with clock(T_LIVE):
            r = getattr(play.alice, method)("/challenges")
            assert r.status_code == 403
    finally:
        user.banned = False
        db.session.commit()


def test_visitor_keeps_ctfds_own_answer_and_then_sent_to_board_after_login(play):
    visitor = play.app.test_client()
    with clock(T_LIVE):
        r = visitor.get("/challenges")
        assert r.status_code == 302
        loc = r.headers["Location"]
        assert "/login" in loc and ("next=%2Fchallenges" in loc or "next=/challenges" in loc)

        head_r = visitor.head("/challenges")
        assert head_r.status_code == 302
        assert "/login" in head_r.headers["Location"]


def test_only_exact_challenges_path_and_get_head_methods_are_ours(play):
    with clock(T_LIVE):
        # POST /challenges is not ours (CTFd responds with 404/405)
        r_post = play.alice.post("/challenges")
        assert r_post.status_code in (404, 405)

        # Subpaths /challenges/... are not ours
        r_sub = play.alice.get("/challenges/unknown")
        assert r_sub.status_code in (404, 405)

        # API is not touched
        r_api = play.alice.get("/api/v1/challenges")
        assert r_api.status_code == 200


def test_challenges_hook_under_other_themes_returns_none(play):
    with clock(T_LIVE):
        with play.app.test_request_context("/challenges"):
            with mock.patch("CTFd.utils.get_config", return_value="core"):
                # Hook steps aside when theme is not l3mon
                assert page._redirect_challenges() is None


def test_challenges_redirect_hook_costs_a_request_that_is_not_ours_nothing(play):
    with play.app.test_request_context("/api/v1/challenges"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []

    with play.app.test_request_context("/challenges", method="POST"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []

    with play.app.test_request_context("/challenges/1"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []

    with play.app.test_request_context("/notifications"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []

    with play.app.test_request_context("/scoreboard"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []

    with play.app.test_request_context("/login"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []
