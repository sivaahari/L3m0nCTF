"""The Guide (SP3 part 3.5): `GET /api/v1/l3mon/guide`, the studio's own numbers, what each member brought in, the channel progress and the
story meter. The programme grid (`/guide/epg`) has its own file, test_epg.py.

A programme the viewer may not see is not in any count: a solve of it is not a member's, not the studio's `solves`, not a channel's `solved`
and not the story's reel, although the TRP stays in the score (the crew pulled the programme back; nobody loses points for it).

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
from CTFd.models import Teams, Unlocks, Users, db
from CTFd.plugins.l3mon_board import hooks
from CTFd.plugins.l3mon_core.models import Channel
from CTFd.utils import set_config
from board_world import T_END, T_LIVE, T_START, clock, join_client, make_app, on_air, solve, started, team_client, user_id, withhold, world
from tests.helpers import destroy_ctfd, login_as_user, register_user

GUIDE = "/api/v1/l3mon/guide"
SCORING = "/api/v1/l3mon/admin/scoring"


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
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob, cara=cara, visitor=app.test_client())
    destroy_ctfd(app)


def team_of(name):
    return Teams.query.filter_by(name=name).one()


def give(team, challenge, minutes, who):
    return solve(team_of(team).id, user_id(who), challenge, at=when(minutes))


def data(client):
    r = client.get(GUIDE)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]


def team(client):
    return data(client)["team"]


def bonus(play, studio, trp, message):
    r = play.admin.post(f"{SCORING}/bonus", json={"team_id": team_of(studio).id, "trp": trp, "message": message})
    assert r.status_code == 200, r.get_data(as_text=True)


def line(member_id, name, solves, trp, pct, captain=False, you=False):
    return {"id": member_id, "name": name, "captain": captain, "you": you, "solves": solves, "trp": trp, "pct": pct}


# ---- the gate and the shape ----------------------------------------------------------------------------------------------------------

def test_a_visitor_is_asked_to_sign_in(play):
    r = play.visitor.get(GUIDE)
    assert r.status_code == 401 and r.get_json()["error"] == "auth_required"


def test_a_player_with_no_studio_is_told_to_make_one(play):
    register_user(play.app, name="loner", email="loner@example.com")
    r = login_as_user(play.app, "loner").get(GUIDE)
    assert r.status_code == 403 and r.get_json()["error"] == "no_team"


def test_the_answer_has_exactly_the_contracts_keys_in_the_contracts_order(play):
    body = data(play.alice)
    assert list(body) == ["ver", "phase", "banner", "epg_sig", "team", "story"]
    assert list(body["team"]) == ["score", "place", "of", "solves", "hints_used", "instances_live", "members", "bonus", "notes", "by_channel"]
    assert list(body["team"]["members"][0]) == ["id", "name", "captain", "you", "solves", "trp", "pct"]
    assert list(body["team"]["by_channel"][0]) == ["channel", "name", "sponsor", "solved", "total"]
    assert list(body["story"]) == ["reels", "reels_needed", "on_air"] and list(body["phase"]) == ["state", "frozen"]


def test_the_response_is_private_and_carries_a_request_id(play):
    r = play.alice.get(GUIDE)
    assert r.headers["Cache-Control"] == "private, no-cache" and r.headers["X-Request-Id"] and r.headers["Vary"] == "Cookie"


# ---- the studio's own numbers --------------------------------------------------------------------------------------------------------------

def test_the_studios_score_place_and_solves(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    give("studio-b", play.ids.hush, 1, "bob")
    a, b = team(play.alice), team(play.bob)
    assert (a["score"], a["place"], a["of"], a["solves"], a["hints_used"], a["instances_live"]) == (100, 2, 3, 1, 0, 0)
    assert (b["score"], b["place"], b["of"], b["solves"]) == (300, 1, 3, 1)


def test_a_studio_with_nothing_yet_has_zero_and_no_place(play):
    t = team(play.cara)
    assert (t["score"], t["place"], t["solves"], t["bonus"], t["notes"]) == (0, None, 0, 0, [])


def test_a_studio_whose_net_score_is_zero_or_below_has_no_place_as_on_the_scoreboard(play):
    give("studio-a", play.ids.coffee, 0, "alice")
    give("studio-b", play.ids.lantern, 1, "bob")
    assert team(play.alice)["place"] == 2
    bonus(play, "studio-a", -50, "A correction")
    t = team(play.alice)
    assert t["score"] == 0 and t["place"] is None and t["of"] == 3
    bonus(play, "studio-a", -30, "Another correction")
    assert team(play.alice)["score"] == -30 and team(play.alice)["place"] is None


def test_hints_used_counts_the_unlocks_and_the_cost_is_in_the_score_but_in_no_members_line(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    for hint in (1, 2):
        assert play.alice.post("/api/v1/unlocks", json={"target": hint, "type": "hints"}).status_code == 200
    assert Unlocks.query.count() == 2
    t = team(play.alice)
    assert t["hints_used"] == 2 and t["score"] == 100 - 10 - 25 and t["bonus"] == 0, "a hint's award is not a bonus"
    assert [m["trp"] for m in t["members"]] == [100, 0], "hint costs are paid by the whole studio and are in nobody's line"
    assert team(play.bob)["hints_used"] == 0


def test_instances_live_counts_the_running_instances_of_live_programmes_on_air(play, monkeypatch):
    states = {play.ids.steam: "running", play.ids.lantern: "running", play.ids.dumpling: "running", play.ids.coffee: "starting"}
    monkeypatch.setattr(hooks, "instance_summary", lambda t, cid: {"state": states[cid], "expires_in": 60, "ends": 99} if cid in states else None)
    assert team(play.alice)["instances_live"] == 1, "only Steam Tunnel is live and running; Lantern Walk is not a live programme"
    for not_running in ("starting", "stopping", "expired", "error"):
        states[play.ids.steam] = not_running
        assert team(play.alice)["instances_live"] == 0, not_running
    states[play.ids.steam] = "running"
    withhold(play.admin, "steam_tunnel")
    assert team(play.alice)["instances_live"] == 0, "a programme that is off air is not counted"


# ---- what each member brought in -------------------------------------------------------------------------------------------------------------

def test_members_are_the_studios_own_the_biggest_first_and_the_viewer_is_marked(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    give("studio-a", play.ids.hush, 1, "alice")
    give("studio-a", play.ids.noodle, 2, "abe")
    alice, abe = user_id("alice"), user_id("abe")
    assert team(play.alice)["members"] == [line(alice, "alice", 2, 400, 67, captain=True, you=True), line(abe, "abe", 1, 200, 33)]
    assert team(play.abe)["members"] == [line(alice, "alice", 2, 400, 67, captain=True), line(abe, "abe", 1, 200, 33, you=True)]
    assert "bob" not in str(team(play.alice)), "another studio's players are never listed"


def test_a_tie_puts_the_captain_first_then_the_names_in_order(play):
    join_client(play.app, "aaron", "studio-a")
    names = [m["name"] for m in team(play.alice)["members"]]
    assert names == ["alice", "aaron", "abe"]
    assert [m["pct"] for m in team(play.alice)["members"]] == [0, 0, 0], "no TRP at all is 0 %, not a division by zero"


def test_a_percentage_rounds_half_up_as_the_demo_does(play):
    give("studio-a", play.ids.lantern, 0, "abe")      # 100 of 800 is 12.5
    give("studio-a", play.ids.steam, 1, "alice")      # 400
    give("studio-a", play.ids.hush, 2, "alice")       # 300
    pcts = {m["name"]: m["pct"] for m in team(play.alice)["members"]}
    assert pcts == {"alice": 88, "abe": 13}


def test_a_solve_nobody_can_be_named_for_is_one_line_earlier_solves_for_a_suspended_member_and_for_one_who_left(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    give("studio-a", play.ids.noodle, 1, "abe")
    abe = Users.query.filter_by(name="abe").one()
    abe.banned = True
    db.session.commit()
    members = team(play.alice)["members"]
    assert members == [line(0, "Earlier solves", 1, 200, 67), line(user_id("alice"), "alice", 1, 100, 33, captain=True, you=True)]
    abe.banned = False
    abe.team_id = None
    db.session.commit()
    assert [m["id"] for m in team(play.alice)["members"]] == [0, user_id("alice")], "a member who left is the same"
    assert team(play.alice)["solves"] == 2, "the studio's own solves do not change when a member is gone"


def test_a_solve_of_a_programme_pulled_back_leaves_the_members_and_the_solves_but_not_the_score(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    give("studio-a", play.ids.hush, 1, "alice")
    assert (team(play.alice)["score"], team(play.alice)["solves"]) == (400, 2)
    withhold(play.admin, "hush_alley")
    t = team(play.alice)
    assert t["score"] == 400 and t["solves"] == 1
    assert t["members"][0] == line(user_id("alice"), "alice", 1, 100, 100, captain=True, you=True)
    assert [c["solved"] for c in t["by_channel"]] == [1, 0, 0]


def test_a_solve_of_a_challenge_that_is_not_in_the_plan_is_in_no_count(play):
    from board_world import fixed

    stray = fixed("Stray", 70, "misc", flag="stray-answer", state="visible")
    give("studio-a", stray.id, 0, "alice")
    t = team(play.alice)
    assert t["score"] == 70 and t["solves"] == 0 and t["members"][0]["solves"] == 0


# ---- bonus and the studio's private lines ------------------------------------------------------------------------------------------------------

def test_the_bonus_adds_up_the_studios_bonuses_only_and_the_notes_are_the_five_newest_lines_first(play):
    bonus(play, "studio-a", 50, "For the best write-up")
    bonus(play, "studio-a", -20, "Corrected an earlier bonus")
    t = team(play.alice)
    assert t["bonus"] == 30 and t["score"] == 30
    assert [list(n) for n in t["notes"]] == [["title", "content"], ["title", "content"]]
    assert [n["content"] for n in t["notes"]] == ["Corrected an earlier bonus", "For the best write-up"]
    assert team(play.bob)["bonus"] == 0 and team(play.bob)["notes"] == [], "another studio's lines and bonuses are not here"
    assert team(play.abe)["bonus"] == 30, "the bonus belongs to the whole studio, whoever received it"
    assert all(m["trp"] == 0 for m in t["members"]), "a bonus is in no member's line"


def test_at_most_five_notes_are_sent_and_the_newest_come_first(play):
    for n in range(7):
        bonus(play, "studio-a", 1 + n, f"line {n}")
    notes = team(play.alice)["notes"]
    assert [n["content"] for n in notes] == ["line 6", "line 5", "line 4", "line 3", "line 2"]


# ---- the channels --------------------------------------------------------------------------------------------------------------------------------

def test_the_progress_of_each_channel_counts_every_cell_of_its_picture(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    give("studio-a", play.ids.coffee, 1, "alice")
    ids = {c.slug: c.id for c in Channel.query.all()}
    assert team(play.alice)["by_channel"] == [
        {"channel": ids["street"], "name": "Mighty Street", "sponsor": None, "solved": 1, "total": 3},
        {"channel": ids["snack"], "name": "Snack Square", "sponsor": None, "solved": 0, "total": 3},
        {"channel": ids["break"], "name": "Sponsored Break", "sponsor": {"name": "Acme"}, "solved": 1, "total": 1},
    ]


# ---- the story meter -------------------------------------------------------------------------------------------------------------------------------

def test_reels_are_the_studios_solves_capped_at_a_third_of_the_programmes_rounded_up(play):
    assert data(play.alice)["story"] == {"reels": 0, "reels_needed": 3, "on_air": 0}
    give("studio-a", play.ids.lantern, 0, "alice")
    assert data(play.alice)["story"]["reels"] == 1
    for n, challenge in enumerate((play.ids.hush, play.ids.noodle, play.ids.steam)):
        give("studio-a", challenge, n + 1, "alice")
    assert data(play.alice)["story"]["reels"] == 3, "four solved, three needed"


def test_on_air_is_every_studios_visible_solves_over_the_crews_target_never_above_one(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    give("studio-a", play.ids.hush, 1, "alice")
    give("studio-b", play.ids.noodle, 2, "bob")
    give("studio-b", play.ids.lantern, 3, "bob")
    assert data(play.alice)["story"]["on_air"] == 0, "no target set: the meter stays at 0"
    for target, expected in ((10, 0.4), (3, 1), (4, 1), (7, 0.57), (400, 0.01)):
        set_config("l3mon_story_air_target", target)
        assert data(play.alice)["story"]["on_air"] == expected, target
    set_config("l3mon_story_air_target", 10)
    withhold(play.admin, "hush_alley")
    assert data(play.alice)["story"]["on_air"] == 0.3, "a pulled-back programme's solves are not told"


def test_on_air_rounds_to_two_places_the_way_the_demo_does_and_a_whole_number_stays_whole(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    set_config("l3mon_story_air_target", 3)
    assert data(play.alice)["story"]["on_air"] == 0.33
    give("studio-a", play.ids.hush, 1, "alice")
    assert data(play.alice)["story"]["on_air"] == 0.67
    give("studio-a", play.ids.noodle, 2, "alice")
    got = play.alice.get(GUIDE).get_data(as_text=True)
    assert '"on_air":1}' in got, "1, not 1.0"


def test_the_meter_can_be_switched_off(play):
    set_config("l3mon_story_meter", "off")
    assert data(play.alice)["story"] is None
    set_config("l3mon_story_meter", "on")
    assert data(play.alice)["story"] is not None


# ---- phases ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_guide_stays_open_before_the_start_with_no_totals_and_no_grid(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    with clock(T_START - 3600):
        body = data(play.alice)
        assert body["phase"] == {"state": "before", "frozen": False} and body["banner"] is None and body["epg_sig"] == "none"
        assert [c["total"] for c in body["team"]["by_channel"]] == [0, 0, 0] and [c["solved"] for c in body["team"]["by_channel"]] == [0, 0, 0]
        assert body["story"] == {"reels": 0, "reels_needed": 0, "on_air": 0}
        assert body["team"]["score"] == 100, "the studio's own score is its own"


@pytest.mark.parametrize("state,banner_id", [("live", None), ("paused", "paused"), ("ended", "wrap")])
def test_the_banner_follows_the_phase(play, state, banner_id):
    if state == "paused":
        set_config("paused", True)
    with clock(T_END + 60 if state == "ended" else T_LIVE):
        body = data(play.alice)
        assert body["phase"]["state"] == state and (body["banner"] or {}).get("id") == banner_id


def test_while_frozen_the_place_is_hidden_and_the_score_is_live(play):
    give("studio-a", play.ids.noodle, 0, "alice")
    give("studio-b", play.ids.hush, 10, "bob")
    set_config("freeze", calendar.timegm(when(60).timetuple()))
    give("studio-a", play.ids.steam, 90, "alice")
    clear_standings()
    with clock(T_LIVE + 2 * 3600):
        body = data(play.alice)
        assert body["phase"] == {"state": "live", "frozen": True} and body["banner"]["id"] == "frozen"
        assert body["team"]["place"] is None and body["team"]["score"] == 600 and body["team"]["solves"] == 2


# ---- the crew ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_crew_belongs_to_no_studio_and_gets_no_team_block(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    body = data(play.admin)
    assert body["team"] is None and body["story"] == {"reels": 0, "reels_needed": 3, "on_air": 0}


# ---- what must not leak ---------------------------------------------------------------------------------------------------------------------------------

def test_nothing_about_a_programme_off_air_is_in_the_answer(play):
    text = play.alice.get(GUIDE).get_data(as_text=True).lower()
    for secret in ("moth", "dumpling", "cipher", "gate", "dumpling_gate", "moth_cipher"):
        assert secret not in text, secret
    assert "answer" not in text and "flag" not in text and "hint" not in text.replace("hints_used", "")


# ---- caching ---------------------------------------------------------------------------------------------------------------------------------------------

def test_the_guide_has_a_strong_etag_answers_304_and_the_tick_is_not_in_it(play):
    first = play.alice.get(GUIDE)
    tag = first.headers["ETag"]
    assert re.fullmatch(r'"g[A-Za-z0-9_-]{22}"', tag)
    again = play.alice.get(GUIDE, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.get_data() == b"" and again.headers["ETag"] == tag
    assert play.alice.get(GUIDE, headers={"If-None-Match": f'"x", W/{tag}'}).status_code == 304
    from CTFd.plugins.l3mon_core.tick import tick

    tick.bump()
    assert play.alice.get(GUIDE, headers={"If-None-Match": tag}).status_code == 304
    give("studio-a", play.ids.lantern, 0, "alice")
    assert play.alice.get(GUIDE, headers={"If-None-Match": tag}).status_code == 200


def test_each_studio_has_its_own_etag(play):
    assert play.alice.get(GUIDE).headers["ETag"] != play.bob.get(GUIDE).headers["ETag"]


# ---- the cost ---------------------------------------------------------------------------------------------------------------------------------------------

def test_the_queries_do_not_grow_with_the_members_the_solves_or_the_notes(play):
    def count():
        play.alice.get(GUIDE)  # warm CTFd's own caches first (the standings are kept for a minute), so only our queries are counted
        statements = []

        def before(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", before)
        try:
            assert play.alice.get(GUIDE).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", before)
        return len(statements)

    few = count()
    for n in range(4):
        join_client(play.app, f"extra{n}", "studio-a")
    for n, challenge in enumerate((play.ids.lantern, play.ids.hush, play.ids.noodle, play.ids.steam)):
        give("studio-a", challenge, n, "alice")
    for n in range(4):
        bonus(play, "studio-a", 3 + n, f"bonus {n}")
    after = count()
    assert after <= few + 1, (few, after)
