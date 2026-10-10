"""The scoreboard, "TRP ratings" (SP3 part 3.5): `GET /api/v1/l3mon/scoreboard` and the rows' markup at `/scoreboard/rows`.

The list is the CTFtime feed's list (`l3mon_core.standings.ranked`), so the site and the feed cannot disagree; the tests read both and
compare them row for row, with ties, a hidden studio, a banned studio, a studio with no score and a freeze in the world.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import calendar
import datetime
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from CTFd.cache import clear_standings
from CTFd.models import Awards, Teams, db
from CTFd.utils import set_config
from board_world import T_END, T_LIVE, T_START, clock, fixed, join_client, make_app, on_air, solve, started, team_client, user_id, world
from tests.helpers import destroy_ctfd, gen_team, login_as_user, register_user

SCOREBOARD = "/api/v1/l3mon/scoreboard"
ROWS = "/api/v1/l3mon/scoreboard/rows"
FEED = "/ctftime/standings.json"


def when(minutes):
    return datetime.datetime.utcfromtimestamp(T_LIVE) + datetime.timedelta(minutes=minutes)


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "hush_alley", "noodle_ledger", "steam_tunnel", "coffee_break")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            cara = team_client(app, "cara", "studio-c")
            hal = team_client(app, "hal", "studio-h")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob, cara=cara, hal=hal, visitor=app.test_client())
    destroy_ctfd(app)


def team_of(name):
    return Teams.query.filter_by(name=name).one()


def give(team, challenge, minutes, who=None):
    t = team_of(team)
    member = who or sorted(u.id for u in t.members)[0]
    return solve(t.id, member, challenge, at=when(minutes))


def bonus(team, value):
    """A bonus line straight in the database (what the crew's award tool writes), for a studio made by CTFd's own helper."""
    db.session.add(Awards(user_id=team.captain_id, team_id=team.id, name="bonus", value=value, category="bonus", date=when(0)))
    db.session.commit()


def data(client, path=SCOREBOARD):
    r = client.get(path)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]


def listed(markup):
    """[(pos, name, solves text, score)] from the rows' markup."""
    out = []
    for match in re.finditer(r'<li class="sb-row[^"]*">(.*?)</li>', markup, re.S):
        row = match.group(1)
        pos = re.search(r'<b class="sb-pos">(\d+)</b>', row).group(1)
        name = re.search(r"<bdi>(.*?)</bdi>", row, re.S).group(1)
        solves = re.search(r'<span class="sb-solves">(.*?)</span>', row).group(1)
        score = re.search(r'<span class="sb-trp"><b>(-?\d+)</b>', row).group(1)
        out.append((int(pos), name, solves, int(score)))
    return out


def feed(play):
    return [(row["pos"], row["team"], row["score"]) for row in play.visitor.get(FEED).get_json()["standings"]]


def board_rows(play, client=None):
    return [(pos, name, score) for pos, name, _, score in listed((client or play.alice).get(ROWS).get_data(as_text=True))]


# ---- the gate ---------------------------------------------------------------------------------------------------------------------

def test_a_visitor_is_asked_to_sign_in_for_both_endpoints(play):
    for path in (SCOREBOARD, ROWS):
        r = play.visitor.get(path)
        assert r.status_code == 401 and r.get_json()["error"] == "auth_required"


def test_a_player_with_no_studio_is_told_to_make_one(play):
    register_user(play.app, name="loner", email="loner@example.com")
    loner = login_as_user(play.app, "loner")
    for path in (SCOREBOARD, ROWS):
        r = loner.get(path)
        assert r.status_code == 403 and r.get_json()["error"] == "no_team"


# ---- the list is the feed's list ----------------------------------------------------------------------------------------------------

def test_the_scoreboard_is_the_ctftime_feed_row_for_row_with_ties_a_hidden_and_a_banned_studio_and_a_studio_with_no_score(play):
    give("studio-a", play.ids.lantern, 0)      # 100
    give("studio-a", play.ids.noodle, 5)       # 300 in all
    give("studio-b", play.ids.hush, 6)         # 300: a minute later, so it loses the tie to studio-a
    give("studio-c", play.ids.steam, 7)        # 400
    give("studio-h", play.ids.coffee, 8)       # 50: hidden below
    team_of("studio-h").hidden = True
    db.session.commit()
    gen_team(db, name="studio-x", email="x@example.com", member_count=1)
    banned = gen_team(db, name="studio-banned", email="z@example.com", member_count=1)
    give("studio-banned", play.ids.lantern, 9)
    banned.banned = True
    db.session.commit()
    clear_standings()
    assert feed(play) == [(1, "studio-c", 400), (2, "studio-a", 300), (3, "studio-b", 300)]
    assert board_rows(play) == feed(play), "the scoreboard and the feed give the same rows"
    assert board_rows(play, play.bob) == feed(play) and board_rows(play, play.admin) == feed(play), "whoever asks"
    body = data(play.alice)
    assert body["total"] == len(feed(play)) and body["shown"] == len(feed(play))
    names = [name for _, name, _ in board_rows(play)]
    assert "studio-h" not in names and "studio-x" not in names and "studio-banned" not in names


def test_the_tie_is_broken_as_ctfd_breaks_it_the_earlier_last_change_of_score_wins(play):
    give("studio-a", play.ids.lantern, 10)
    give("studio-b", play.ids.lantern, 5)
    assert [name for _, name, _ in board_rows(play)] == ["studio-b", "studio-a"]
    give("studio-a", play.ids.coffee, 20)  # 150 now: ahead
    give("studio-b", play.ids.hush, 30)    # 400: ahead again, by points
    assert [name for _, name, _ in board_rows(play)] == ["studio-b", "studio-a"]
    assert board_rows(play) == feed(play)


def test_a_row_is_a_position_a_name_a_solve_count_and_the_score_and_nothing_else(play):
    give("studio-a", play.ids.lantern, 0)
    give("studio-a", play.ids.hush, 1)
    give("studio-b", play.ids.lantern, 2)
    assert listed(play.alice.get(ROWS).get_data(as_text=True)) == [(1, "studio-a", "2 solves", 400), (2, "studio-b", "1 solve", 100)]
    html = play.alice.get(ROWS).get_data(as_text=True)
    assert "studio-c" not in html and "@" not in html and "date" not in html.lower()


def test_a_name_with_markup_is_shown_as_text_never_as_elements(play):
    give("studio-b", play.ids.lantern, 0)
    team_of("studio-b").name = '<img src=x onerror="alert(1)">&<b>'
    db.session.commit()
    clear_standings()
    html = play.alice.get(ROWS).get_data(as_text=True)
    assert "<img" not in html and "<b>&" not in html and "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;&amp;&lt;b&gt;" in html


def test_the_studios_own_row_is_marked_for_everyone_who_asks_in_their_own_way(play):
    give("studio-a", play.ids.lantern, 0)
    give("studio-b", play.ids.hush, 1)
    mine = play.alice.get(ROWS).get_data(as_text=True)
    theirs = play.bob.get(ROWS).get_data(as_text=True)
    assert mine != theirs
    assert re.search(r'class="sb-row is-you"[^>]*><b class="sb-pos">2</b>.*?studio-a.*?\(your studio\)', mine, re.S)
    assert re.search(r'class="sb-row is-you"[^>]*><b class="sb-pos">1</b>.*?studio-b.*?\(your studio\)', theirs, re.S)
    assert "(your studio)" not in play.admin.get(ROWS).get_data(as_text=True) and "is-you" not in play.admin.get(ROWS).get_data(as_text=True)


def test_the_first_three_are_marked_and_the_bar_is_relative_to_the_leader_and_never_zero(play):
    for n, (team, chal, score_cls) in enumerate([("studio-a", play.ids.steam, 400), ("studio-b", play.ids.hush, 300), ("studio-c", play.ids.noodle, 200), ("studio-h", play.ids.coffee, 50)]):
        give(team, chal, n)
    html = play.admin.get(ROWS).get_data(as_text=True)
    classes = re.findall(r'<li class="sb-row ([^"]*)">', html)
    bars = re.findall(r'sb-bar pv-(\d+)', html)
    assert classes == ["is-top is-p1", "is-top is-p2", "is-top is-p3", ""] and bars == ["100", "75", "50", "13"]


# ---- the studio's own line ------------------------------------------------------------------------------------------------------------

def test_me_is_the_studios_own_name_live_score_and_place_in_the_list_and_how_many_are_listed(play):
    give("studio-a", play.ids.noodle, 0)
    give("studio-b", play.ids.steam, 1)
    me = data(play.alice)["me"]
    assert me == {"name": "studio-a", "score": 200, "pos": 2, "of": 2}
    assert data(play.bob)["me"] == {"name": "studio-b", "score": 400, "pos": 1, "of": 2}


def test_a_studio_with_no_score_has_a_line_but_no_place_and_is_not_ranked(play):
    give("studio-a", play.ids.noodle, 0)
    assert data(play.cara)["me"] == {"name": "studio-c", "score": 0, "pos": None, "of": 1}


def test_a_hidden_studio_sees_its_own_live_score_and_no_place_while_the_list_leaves_it_out(play):
    give("studio-a", play.ids.noodle, 0)
    give("studio-h", play.ids.steam, 1)
    team_of("studio-h").hidden = True
    db.session.commit()
    clear_standings()
    assert data(play.hal)["me"] == {"name": "studio-h", "score": 400, "pos": None, "of": 1}
    assert "studio-h" not in play.hal.get(ROWS).get_data(as_text=True)


def test_the_crew_has_no_studio_line(play):
    give("studio-a", play.ids.noodle, 0)
    body = data(play.admin)
    assert body["me"] is None and body["total"] == 1


def test_a_score_below_zero_is_not_in_the_list_and_the_studio_is_not_ranked(play):
    give("studio-a", play.ids.coffee, 0)
    gift = Awards(user_id=user_id("alice"), team_id=team_of("studio-a").id, name="Adjustment -80 TRP", value=-80, category="bonus", date=when(1))
    db.session.add(gift)
    db.session.commit()
    clear_standings()
    me = data(play.alice)["me"]
    assert me["score"] == -30 and me["pos"] is None and data(play.alice)["total"] == 0


# ---- the phases ------------------------------------------------------------------------------------------------------------------------

def test_before_the_start_the_list_is_empty_whatever_was_scored_and_there_is_no_banner(play):
    give("studio-a", play.ids.noodle, 0)
    with clock(T_START - 3600):
        body = data(play.alice)
        assert body["phase"] == {"state": "before", "frozen": False} and body["total"] == 0 and body["shown"] == 0 and body["banner"] is None
        assert body["me"] == {"name": "studio-a", "score": 200, "pos": None, "of": 0}
        assert play.alice.get(ROWS).get_data(as_text=True) == ""


@pytest.mark.parametrize("state,banner", [
    ("live", None),
    ("paused", {"id": "paused", "cls": "banner-warn", "lead": "PAUSED.", "text": "Submissions are paused for a moment. Your progress is safe."}),
    ("ended", {"id": "wrap", "cls": "banner-wrap", "lead": "THAT'S A WRAP.", "text": "The broadcast has ended. You can still read every programme."}),
])
def test_the_banner_follows_the_phase(play, state, banner):
    if state == "paused":
        set_config("paused", True)
    with clock(T_END + 60 if state == "ended" else T_LIVE):
        body = data(play.alice)
        assert body["phase"]["state"] == state and body["banner"] == banner


def test_while_frozen_the_rows_are_the_frozen_standings_the_place_is_hidden_and_the_own_score_is_live(play):
    freeze = calendar.timegm(when(60).timetuple())
    set_config("freeze", freeze)
    give("studio-a", play.ids.noodle, 0)       # before the freeze: 200
    give("studio-b", play.ids.hush, 10)        # before the freeze: 300
    give("studio-a", play.ids.steam, 90)       # after the freeze: studio-a is really ahead now, the public does not see it
    clear_standings()
    with clock(T_LIVE + 2 * 3600):
        body = data(play.alice)
        assert body["phase"] == {"state": "live", "frozen": True}
        assert body["banner"] == {"id": "frozen", "cls": "banner-info", "lead": "SCOREBOARD FROZEN.", "text": "Standings are frozen for the final hour. Your solves still count."}
        assert body["me"] == {"name": "studio-a", "score": 600, "pos": None, "of": 2}, "the studio's own score is live and its place is hidden"
        assert listed(play.alice.get(ROWS).get_data(as_text=True)) == [(1, "studio-b", "1 solve", 300), (2, "studio-a", "1 solve", 200)], "the public rows and their solve counts stand still"
        assert board_rows(play) == feed(play)


# ---- the top 100 -----------------------------------------------------------------------------------------------------------------------

def test_the_list_stops_at_100_and_a_studio_below_them_still_has_its_own_line(play):
    for n in range(1, 106):
        team = gen_team(db, name=f"crowd-{n:03d}", email=f"crowd{n}@example.com", member_count=1)
        bonus(team, 1000 + n)
    clear_standings()
    body = data(play.alice)
    assert body["total"] == 105 and body["shown"] == 100
    rows = listed(play.alice.get(ROWS).get_data(as_text=True))
    assert len(rows) == 100 and rows[0][1] == "crowd-105" and rows[-1][1] == "crowd-006"
    bonus(team_of("studio-a"), 5)
    clear_standings()
    me = data(play.alice)["me"]
    assert me["pos"] == 106 and me["of"] == 106 and me["score"] == 5


# ---- caching ---------------------------------------------------------------------------------------------------------------------------

def test_the_json_and_the_rows_have_strong_etags_and_answer_304_and_the_tick_is_not_in_them(play):
    give("studio-a", play.ids.noodle, 0)
    first = play.alice.get(SCOREBOARD)
    tag = first.headers["ETag"]
    assert re.fullmatch(r'"s[A-Za-z0-9_-]{22}"', tag) and first.headers["Cache-Control"] == "private, no-cache" and first.headers["X-Request-Id"]
    again = play.alice.get(SCOREBOARD, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.get_data() == b"" and again.headers["ETag"] == tag
    assert play.alice.get(SCOREBOARD, headers={"If-None-Match": f'W/"nope", {tag}'}).status_code == 304
    rows = play.alice.get(ROWS)
    sig = first.get_json()["data"]["rows_sig"]
    assert rows.headers["ETag"] == f'"r{sig}"' and rows.mimetype == "text/html" and rows.headers["Cache-Control"] == "private, no-cache"
    assert play.alice.get(ROWS, headers={"If-None-Match": rows.headers["ETag"]}).status_code == 304
    from CTFd.plugins.l3mon_core.tick import tick

    tick.bump()
    assert play.alice.get(SCOREBOARD, headers={"If-None-Match": tag}).status_code == 304, "the shared tick moved: the answer did not"


def test_the_signature_changes_with_the_rows_and_differs_for_each_studio_because_each_sees_its_own_row_marked(play):
    give("studio-a", play.ids.noodle, 0)
    mine, theirs = data(play.alice)["rows_sig"], data(play.bob)["rows_sig"]
    assert mine != theirs
    give("studio-b", play.ids.steam, 1)
    assert data(play.alice)["rows_sig"] != mine


# ---- the numbers ------------------------------------------------------------------------------------------------------------------------

def test_the_answer_has_exactly_the_contracts_keys_and_the_ranked_solve_count_is_the_counted_solves_including_a_pulled_back_programme(play):
    give("studio-a", play.ids.lantern, 0)
    give("studio-a", play.ids.hush, 1)
    body = data(play.alice)
    assert list(body) == ["ver", "phase", "banner", "total", "shown", "rows_sig", "me"]
    assert list(body["phase"]) == ["state", "frozen"] and list(body["me"]) == ["name", "score", "pos", "of"]
    from board_world import withhold

    withhold(play.admin, "hush_alley")
    assert listed(play.alice.get(ROWS).get_data(as_text=True)) == [(1, "studio-a", "2 solves", 400)], "the TRP stays when a programme is pulled back, so the solve does too"


def test_the_queries_do_not_grow_with_the_number_of_studios(play):
    def count_for(rows):
        play.alice.get(SCOREBOARD)  # warm CTFd's own caches first (the standings are kept for a minute), so only our queries are counted
        play.alice.get(ROWS)
        statements = []
        engine = db.engine

        def before(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", before)
        try:
            play.alice.get(SCOREBOARD)
            play.alice.get(ROWS)
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(statements)

    give("studio-a", play.ids.noodle, 0)
    few = count_for(1)
    for n in range(1, 40):
        team = gen_team(db, name=f"many-{n}", email=f"many{n}@example.com", member_count=1)
        bonus(team, 10 + n)
    clear_standings()
    many = count_for(40)
    assert many <= few + 1, (few, many)


def test_the_signature_moves_when_only_a_solve_count_does_and_the_bar_is_never_empty(play):
    give("studio-a", play.ids.noodle, 0)
    before = data(play.alice)["rows_sig"]
    zero = fixed("Zero Point", 0, "misc", flag="zero-answer", state="visible")
    give("studio-a", zero.id, 1)  # a solve that is worth nothing: the score and the place stay, the solve count moves
    assert board_rows(play) == [(1, "studio-a", 200)] and data(play.alice)["rows_sig"] != before
    assert listed(play.alice.get(ROWS).get_data(as_text=True)) == [(1, "studio-a", "2 solves", 200)]


def test_a_bar_is_never_empty_even_for_a_tiny_share_of_the_leader(play):
    bonus(team_of("studio-a"), 1000)
    bonus(team_of("studio-b"), 1)
    clear_standings()
    assert re.findall(r"sb-bar pv-(\d+)", play.alice.get(ROWS).get_data(as_text=True)) == ["100", "1"]
