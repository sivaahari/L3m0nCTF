"""CTFd's own team and player pages, closed to everyone but the crew (owner's decision, 22 October 2026: "close them, like the notifications route").

Stock CTFd shows the list of studios (`/teams`), a studio's public page (`/teams/<id>`), the list of players (`/users`), a player's public page
(`/users/<id>`) and a player's own profile (`/user`) to anybody, with the programmes each has solved. None of them is in the platform's design, a
studio is met through the Guide and the scoreboard, and the strict content security policy cannot go on while stock pages are open. So they answer
404 before CTFd's own view runs, for a visitor and for a player alike; the crew keeps them. What stays open is what the design uses: `/team` (a
studio's own page), `/teams/new`, `/teams/join` and `/teams/invite` (the way into a studio), and `/settings`.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
from types import SimpleNamespace
from unittest import mock

import pytest

from CTFd.models import Teams, Users
from board_world import T_LIVE, clock, join_client, make_app, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user, register_user


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            world(app, admin)
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            register_user(app, name="fresh", email="fresh@example.com")
            fresh = login_as_user(app, "fresh")  # signed in, no studio yet
            yield SimpleNamespace(
                app=app, admin=admin, alice=alice, abe=abe, bob=bob, fresh=fresh, visitor=app.test_client(),
                team_a=Teams.query.filter_by(name="studio-a").one().id, team_b=Teams.query.filter_by(name="studio-b").one().id,
                user_a=Users.query.filter_by(name="alice").one().id, user_b=Users.query.filter_by(name="bob").one().id,
            )
    destroy_ctfd(app)


def closed(p):
    return ["/teams", "/users", "/user", f"/teams/{p.team_a}", f"/teams/{p.team_b}", f"/users/{p.user_a}", f"/users/{p.user_b}"]


def test_the_five_stock_pages_are_closed_to_a_visitor_and_to_every_player(play):
    for who in ("visitor", "alice", "abe", "bob", "fresh"):
        client = getattr(play, who)
        for path in closed(play):
            assert client.get(path).status_code == 404, (who, path)
            assert client.head(path).status_code == 404, (who, "HEAD", path)


def test_a_player_cannot_read_their_own_stock_page_either(play):
    assert play.alice.get(f"/teams/{play.team_a}").status_code == 404
    assert play.alice.get(f"/users/{play.user_a}").status_code == 404
    assert play.alice.get("/user").status_code == 404


@pytest.mark.parametrize("odd", ["{t}/", "0{t}", "00{t}", "{t}.0", "+{t}", " {t}", "{t}%20", "%31", "٣", "{t}/solves", "{t}/x"])
def test_no_spelling_of_an_id_opens_a_studios_page(play, odd):
    path = "/teams/" + odd.format(t=play.team_a)
    for who in ("visitor", "alice", "bob"):
        r = getattr(play, who).get(path)
        assert r.status_code in (404, 308), (who, path, r.status_code)  # a redirect to the plain address is followed by the same refusal
        if r.status_code == 308:
            assert getattr(play, who).get(r.headers["Location"]).status_code == 404, (who, path)


@pytest.mark.parametrize("odd", ["{u}/", "0{u}", "00{u}", "{u}.0", "+{u}", " {u}", "{u}%20", "٣", "{u}/solves", "{u}/x", "abc", ""])
def test_no_spelling_of_an_id_opens_a_players_page(play, odd):
    path = "/users/" + odd.format(u=play.user_a)
    for who in ("visitor", "alice", "bob"):
        r = getattr(play, who).get(path)
        assert r.status_code in (404, 308), (who, path, r.status_code)
        if r.status_code == 308:
            assert getattr(play, who).get(r.headers["Location"]).status_code == 404, (who, path)


def arabic_indic(number):
    return "".join(chr(0x660 + int(digit)) for digit in str(number))


def test_a_spelling_that_ctfds_router_reads_as_the_id_is_closed_too(play):
    """The tests above are only as good as the addresses CTFd would really answer: these it does (the guard off proves it), so a guard that
    recognised only `[1-9][0-9]*` would let them through."""
    from CTFd.plugins.l3mon_board import stock_pages

    spelt = [f"/teams/0{play.team_b}", f"/teams/000{play.team_b}", f"/teams/{arabic_indic(play.team_b)}",
             f"/users/0{play.user_b}", f"/users/000{play.user_b}", f"/users/{arabic_indic(play.user_b)}"]
    with mock.patch.dict(stock_pages.ON, {"guard": False}):
        for path in spelt:
            assert play.alice.get(path).status_code == 200, f"CTFd itself answers {path}, so the check below means something"
    for who in ("visitor", "alice", "abe"):
        for path in spelt:
            assert getattr(play, who).get(path).status_code == 404, (who, path)
    for path in spelt:
        assert play.admin.get(path).status_code == 200, f"the crew keeps {path}"


@pytest.mark.parametrize("path", ["/teams/", "/users/", "/user/", "//teams/{t}", "/teams//{t}"])
def test_a_trailing_slash_or_a_doubled_slash_opens_nothing(play, path):
    for who in ("visitor", "alice"):
        r = getattr(play, who).get(path.format(t=play.team_a), follow_redirects=True)
        assert r.status_code == 404, (who, path, r.status_code)


def test_the_crew_keeps_every_one_of_them(play):
    for path in closed(play):
        r = play.admin.get(path)
        assert r.status_code == 200, (path, r.status_code)


def test_what_the_design_uses_stays_open(play):
    assert play.alice.get("/team").status_code == 200, "a studio's own page (stream D's)"
    assert play.fresh.get("/teams/new").status_code == 200, "creating a studio"
    assert play.fresh.get("/teams/join").status_code == 200, "joining one"
    invite = play.fresh.get("/teams/invite?code=not-a-code")
    assert invite.status_code == 403 and "invalid" in invite.get_data(as_text=True).lower(), "an invite answers as CTFd does (an invalid code is refused by CTFd itself, not closed by us)"
    assert play.alice.get("/settings").status_code == 200
    assert play.visitor.get("/login").status_code == 200


def test_the_refusal_is_ctfds_own_missing_page_and_names_nothing(play):
    page = play.alice.get(f"/teams/{play.team_b}").get_data(as_text=True)
    other = play.alice.get("/teams/999999").get_data(as_text=True)
    assert page == other, "a studio that exists and one that does not give the same page"
    assert "studio-b" not in page and "bob" not in page


@pytest.mark.parametrize("path", ["/login", "/api/v1/challenges", "/team", "/teams/new", "/teams/join", "/teams/invite", "/scoreboard", "/settings", "/usersX", "/teamsX", "/userX", "/users-x/1", "/themes/core/static/css/nothing.css"])
def test_the_guard_looks_at_the_path_before_it_asks_who_is_asking(play, path):
    """`is_admin()` reads a cached value (a round trip to Redis in production); a hook that runs on every GET asks it only for the paths it guards."""
    from CTFd.plugins.l3mon_board import stock_pages

    with mock.patch.object(stock_pages, "is_admin", wraps=stock_pages.is_admin) as asked:
        play.alice.get(path)
    assert asked.call_count == 0, path


@pytest.mark.parametrize("path", ["/teams", "/teams/1", "/users", "/users/1", "/user"])
def test_the_guarded_paths_still_ask(play, path):
    from CTFd.plugins.l3mon_board import stock_pages

    with mock.patch.object(stock_pages, "is_admin", wraps=stock_pages.is_admin) as asked:
        play.alice.get(path)
    assert asked.call_count >= 1, path


def test_the_switch_is_a_real_switch(play):
    """Break it on purpose: with the guard off the pages answer again, so the tests above prove the guard and not something else."""
    from CTFd.plugins.l3mon_board import stock_pages

    with mock.patch.dict(stock_pages.ON, {"guard": False}):
        assert play.alice.get("/teams").status_code == 200
        assert play.alice.get(f"/teams/{play.team_b}").status_code == 200
    assert play.alice.get("/teams").status_code == 404
