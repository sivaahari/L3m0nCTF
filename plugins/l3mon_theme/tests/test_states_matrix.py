"""Every state of the broadcast and of the visitor, for the three pages of stream A, in one table (round 2).

Rows are the states of the first prompt's list: signed out, no studio, suspended, before the start, live, paused, ended, frozen, the crew. Columns are the
board, a programme's own page and the notifications page. Each cell is what the page does: the status, and where a redirect goes or what the page says.

The other test files of the stream test each page in depth; this one is the map that shows no cell was forgotten.

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_states_matrix.py
"""
import re

import pytest

from CTFd.models import Users, db
from CTFd.utils import set_config
from theme_a_world import PASSWORD, T_END, T_LIVE, T_START, clock, play, register_user, text  # noqa: F401  (the fixture is used by name)
from tests.helpers import login_as_user

PAGES = {"board": "/board", "programme": "/board/lantern_walk", "notifications": "/notifications"}


def outcome(response):
    """(status, where it goes or the mark of what it shows)."""
    html = response.get_data(as_text=True)
    if response.status_code in (301, 302, 303):
        return response.status_code, re.sub(r"\?.*", "", response.headers["Location"])
    if response.status_code != 200:
        return response.status_code, ""
    if 'data-slate="before"' in html:
        return 200, "slate:before"
    if 'id="nt-h"' in html:
        return 200, "notifications"
    if 'id="pn-ns"' in html:
        banner = re.search(r'data-banner="([a-z]+)"', html)
        return 200, "programme" + (f":{banner.group(1)}" if banner else "")
    if "data-board" in html:
        banner = re.search(r'data-banner="([a-z]+)"', html)
        return 200, "board" + (f":{banner.group(1)}" if banner else "")
    return 200, "?"


def visit(client, moment, **config):
    for key, value in {"start": T_START, "end": T_END, "view_after_ctf": True, "paused": False, "freeze": None, **config}.items():
        set_config(key, value)
    with clock(moment):
        return {name: outcome(client.get(path)) for name, path in PAGES.items()}


def test_signed_out_everything_goes_to_sign_in(play):
    got = visit(play.app.test_client(), T_LIVE)
    assert got == {"board": (302, "/login"), "programme": (302, "/login"), "notifications": (302, "/login")}


def test_a_player_without_a_studio_goes_to_make_one(play):
    register_user(play.app, name="loner", email="loner@example.com", password=PASSWORD)
    loner = login_as_user(play.app, "loner", PASSWORD)
    got = visit(loner, T_LIVE)
    assert got == {"board": (302, "/team"), "programme": (302, "/team"), "notifications": (302, "/team")}


def test_a_suspended_account_is_turned_away_everywhere(play):
    Users.query.filter_by(name="alice").one().banned = True
    db.session.commit()
    got = visit(play.alice, T_LIVE)
    assert {name: code for name, (code, _) in got.items()} == {"board": 403, "programme": 403, "notifications": 403}


def test_before_the_start_the_board_and_the_programme_show_the_not_on_air_page_and_the_bell_page_still_opens(play):
    got = visit(play.alice, T_START - 3600)
    assert got["board"] == (200, "slate:before") and got["programme"] == (200, "slate:before")
    assert got["notifications"][0] in (200, 403), "the news is a player's own business; the stock gate decides before the start"


def test_live_every_page_is_there(play):
    got = visit(play.alice, T_LIVE)
    assert got == {"board": (200, "board"), "programme": (200, "programme"), "notifications": (200, "notifications")}


def test_paused_every_page_is_there_with_the_paused_banner(play):
    got = visit(play.alice, T_LIVE, paused=True)
    assert got["board"] == (200, "board:paused") and got["programme"] == (200, "programme:paused") and got["notifications"][0] == 200


def test_ended_every_page_is_there_with_the_wrap_banner(play):
    got = visit(play.alice, T_END + 60)
    assert got["board"] == (200, "board:wrap") and got["programme"] == (200, "programme:wrap") and got["notifications"][0] == 200


def test_ended_with_the_event_closed_to_players_the_pages_follow_ctfds_gate(play):
    got = visit(play.alice, T_END + 60, view_after_ctf=False)
    assert got["board"][0] == got["programme"][0] and got["board"][0] in (200, 403)


def test_frozen_every_page_is_there_with_the_frozen_banner(play):
    got = visit(play.alice, T_LIVE, freeze=T_START + 600)
    assert got["board"] == (200, "board:frozen") and got["programme"] == (200, "programme:frozen") and got["notifications"][0] == 200


def test_the_crew_see_every_page_with_no_studio(play):
    got = visit(play.admin, T_LIVE)
    assert got == {"board": (200, "board"), "programme": (200, "programme"), "notifications": (200, "notifications")}


def test_the_crew_before_the_start_see_the_not_on_air_page_too(play):
    got = visit(play.admin, T_START - 3600)
    assert got["board"] == (200, "slate:before") and got["programme"] == (200, "slate:before")
