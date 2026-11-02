"""What the board page and the notifications page cost (SP6, stream A; the rules of SP3 part 3.7 and of the theme's hook-cost test).

  * one request builds the board's data once (the board plugin's `build` is called once; never for a request that is not ours);
  * the page costs a few database statements, the same for 4 and for 29 studios, for 8 and for 40 programmes, and for a studio of 1 and of 5 players;
  * the notifications page does not build the board;
  * a request that is not ours runs none of our code (no statement, no value, no board).

Measured cold (caches emptied) and warm, three times each, the cheapest taken, as the board plugin's budget test does.

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_board_cost.py
"""
from types import SimpleNamespace
from unittest import mock

import pytest

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Teams, Users
from theme_a_world import T_LIVE, add_notification, clock, make_app, started, statements_of, team_client
from test_query_budget import add_studios, make_player, make_programmes, solve_all
from tests.helpers import destroy_ctfd, login_as_user

PAGES = {"board": "/board", "challenges": "/challenges", "programme": "/board/extra_0", "notifications": "/notifications"}
# (cold, warm) ceilings, a little above what each page costs today (measured 2026-10-22 with 8 programmes and 4 studios: board 24 cold, 21 warm; 33 cold with a studio
# of five players; notifications 6 and 6), so a change that doubles it is noticed
# A programme's own page also reads the task, the files, the hints and what the studio opened of them, the tries left, the studio's score and its instance (round 2: 32 cold,
# 29 warm with 8 programmes and 4 studios, about 8 more than the board; the same for 4 and 29 studios, 8 and 40 programmes: tested below)
CEILING = {"board": (36, 24), "challenges": (36, 24), "programme": (46, 34), "notifications": (9, 9)}
ALLOWED_GROWTH = 2
# CTFd works out a studio's score member by member (two statements each) whenever its cache is cold, and a studio has at most four players, so a full
# studio costs at most this much more on a cold request: bounded, and not an N+1 (the same allowance as the board plugin's budget test)
MEMBERS = 4
COLD_COST_OF_A_MEMBER = 2


def measure(client, path):
    colds, warms = [], []
    for _ in range(3):
        clear_standings()
        clear_challenges()
        first, cold = statements_of(lambda: client.get(path, follow_redirects=True))
        second, warm = statements_of(lambda: client.get(path, follow_redirects=True))
        assert first.status_code == 200 and second.status_code == 200, (path, first.status_code, second.status_code)
        colds.append(len(cold))
        warms.append(len(warm))
    return min(colds), min(warms)


def measure_all(client):
    return {name: measure(client, path) for name, path in PAGES.items()}


@pytest.fixture(scope="module")
def measured():
    app = make_app()
    try:
        with app.app_context():
            started()
            with clock(T_LIVE):
                admin = login_as_user(app, "admin")
                ids = make_programmes(admin, 8)
                viewer = team_client(app, "viewer", "viewer-studio")
                add_studios(3, 0, ids)
                add_notification("Welcome", "The broadcast is on.")
                sizes = SimpleNamespace(base=measure_all(viewer))
                add_studios(25, 3, ids)
                sizes.studios = measure_all(viewer)
                ids += make_programmes(admin, 32, first=8)
                me = Users.query.filter_by(name="viewer").one()
                solve_all(me, ids[:30])
                for n in range(40):
                    add_notification(f"News {n}", "A line.")
                sizes.programmes = measure_all(viewer)
                for n in range(4):
                    solve_all(make_player(f"mate{n}", Teams.query.get(me.team_id)), ids[30 + 2 * n : 32 + 2 * n])
                sizes.members = measure_all(viewer)
        return sizes
    finally:
        destroy_ctfd(app)


@pytest.mark.parametrize("name", list(PAGES))
@pytest.mark.parametrize("size", ["studios", "programmes", "members"])
def test_a_page_does_not_ask_the_database_more_for_more_studios_programmes_or_players(measured, name, size):
    base, grown = measured.base[name], getattr(measured, size)[name]
    for when, label in ((0, "cold"), (1, "warm")):
        allowed = ALLOWED_GROWTH + (MEMBERS * COLD_COST_OF_A_MEMBER if size == "members" and when == 0 else 0)
        assert grown[when] <= base[when] + allowed, f"{name} {label}: {base[when]} statements at the start, {grown[when]} after {size} grew"


@pytest.mark.parametrize("name", list(PAGES))
def test_each_page_stays_under_its_ceiling(measured, name):
    for size in ("base", "studios", "programmes", "members"):
        cold, warm = getattr(measured, size)[name]
        assert cold <= CEILING[name][0] and warm <= CEILING[name][1], f"{name} at {size}: {cold} cold, {warm} warm; ceiling {CEILING[name]}"


# ---- the board is built once, and only for a page that needs it ----------------------------------------------------------------------


@pytest.fixture()
def counted():
    """The app with a studio, and the board plugin's `build` counted."""
    app = make_app()
    try:
        with app.app_context():
            started()
            with clock(T_LIVE):
                admin = login_as_user(app, "admin")
                make_programmes(admin, 3)
                player = team_client(app, "viewer", "viewer-studio")
                from CTFd.plugins.l3mon_board import board

                real = board.build
                calls = []

                def build(viewer):
                    calls.append(1)
                    return real(viewer)

                with mock.patch.object(board, "build", build):
                    yield SimpleNamespace(app=app, player=player, admin=admin, calls=calls)
    finally:
        destroy_ctfd(app)


@pytest.mark.parametrize("path", ["/board", "/challenges", "/board/extra_0", "/board?ch=2"])
def test_a_board_request_builds_the_board_once(counted, path):
    counted.calls.clear()
    assert counted.player.get(path, follow_redirects=True).status_code == 200
    assert len(counted.calls) == 1, f"{path} built the board {len(counted.calls)} times"


@pytest.mark.parametrize("path,who", [
    ("/notifications", "player"), ("/login", "visitor"), ("/scoreboard", "visitor"), ("/settings", "player"),
    ("/api/v1/challenges", "player"), ("/themes/l3mon/static/bundles.json", "visitor"), ("/board", "visitor"), ("/board/extra_0", "visitor"),
])
def test_a_request_that_does_not_draw_the_board_never_builds_it(counted, path, who):
    counted.calls.clear()
    client = counted.player if who == "player" else counted.app.test_client()
    client.get(path)
    assert counted.calls == [], f"{path} built the board for {who}"


def test_the_notifications_hook_costs_a_request_that_is_not_ours_nothing(counted):
    from CTFd.plugins.l3mon_theme.pages import board as page

    with counted.app.test_request_context("/api/v1/challenges"):
        result, seen = statements_of(page._serve_notifications)
    assert result is None and seen == []
    with counted.app.test_request_context("/notifications", method="POST"):
        result, seen = statements_of(page._serve_notifications)
    assert result is None and seen == [], "only a GET or a HEAD of exactly /notifications is ours"


def test_the_challenges_redirect_hook_costs_a_request_that_is_not_ours_nothing(counted):
    from CTFd.plugins.l3mon_theme.pages import board as page

    with counted.app.test_request_context("/api/v1/challenges"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []
    with counted.app.test_request_context("/challenges", method="POST"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == [], "only a GET or a HEAD of exactly /challenges is ours"
    with counted.app.test_request_context("/challenges/1"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []
    with counted.app.test_request_context("/notifications"):
        result, seen = statements_of(page._redirect_challenges)
    assert result is None and seen == []


def test_a_value_is_read_only_when_a_template_asks_for_it(counted):
    from CTFd.plugins.l3mon_theme.pages import board as page

    with mock.patch.object(page, "_build", side_effect=AssertionError("the board was built for a page that is not ours")):
        for path in ("/login", "/scoreboard", "/settings", "/api/v1/challenges", "/team"):
            assert counted.player.get(path).status_code in (200, 302), path
