"""The board: what a signed-in studio sees of the channels and the programmes on air, and its own score and place.

The world is made up (board_world.py): three channels (one sponsored), seven programmes of both kinds, one with a prerequisite, one
live, studios of two players. Everything is loaded and released through the crew's real calls, and solves, hints and bans go through
the real routes, so the board is tested against what the platform really holds.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import datetime
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Notifications, Submissions, Teams, db
from CTFd.plugins.l3mon_core.tick import tick
from CTFd.utils import set_config
from board_world import (
    BOARD, T_END, T_LIVE, T_START, clock, join_client, make_app, on_air, started, team_client, withhold, world,
)
from tests.helpers import destroy_ctfd, login_as_user


def attempt(client, challenge_id, flag):
    r = client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "submission": flag})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["status"]


def board(client, **kw):
    r = client.get(BOARD, **kw)
    assert r.status_code in (200, 304), r.get_data(as_text=True)
    return r.get_json()["data"] if r.status_code == 200 else None


def by_slug(data):
    return {p["slug"]: p for p in data["programmes"]}


def by_channel_slug(data):
    return {c["slug"]: c for c in data["channels"]}


@pytest.fixture()
def play():
    """The day of the round at 10:00 IST: the world loaded, the street and the sandwich shop on air except one programme each, the
    sponsored break on air, two studios (alice and abe; bob), and the crew."""
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "moth_cipher", "noodle_ledger", "dumpling_gate", "coffee_break")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob)
    destroy_ctfd(app)


def test_the_board_lists_the_channels_in_order_and_what_is_on_air_with_every_field(play):
    data = board(play.alice)
    assert data["phase"] == {"state": "live", "frozen": False}
    assert [c["slug"] for c in data["channels"]] == ["street", "snack", "break"], "in the crew's order"
    street, snack, brk = data["channels"]
    assert (street["no"], street["name"], street["accent"], street["picture"], street["synopsis"]) == (1, "Mighty Street", "ch1", "street", "A loud street.")
    assert street["sponsor"] is None and snack["sponsor"] is None
    assert brk["sponsor"] == {"name": "Acme", "logo": "acme.svg"} and brk["no"] == 7
    assert [p["slug"] for p in data["programmes"]] == ["lantern_walk", "moth_cipher", "noodle_ledger", "coffee_break"], "channel by channel, cell by cell; the dumpling needs the lantern first"
    lantern = by_slug(data)["lantern_walk"]
    assert lantern == {
        "id": play.ids.lantern, "slug": "lantern_walk", "number": 101, "channel": street["id"], "order": 0, "cell": 0, "name": "Lantern Walk",
        "category": "web", "difficulty": "warmup", "value": 100, "solves": 0, "solved_by_me": False, "first_blood_open": True, "live": False, "instance": None,
    }
    moth = by_slug(data)["moth_cipher"]
    assert (moth["value"], moth["difficulty"], moth["category"], moth["cell"]) == (500, "easy", "crypto", 1)


def test_a_programme_that_is_not_on_air_is_absent_not_hidden_and_the_channel_only_counts_it(play):
    data = board(play.alice)
    text = json.dumps(data)
    for word in ("Hush Alley", "hush_alley", "forensics", "hush-answer", "Steam Tunnel", "steam_tunnel", "steam-answer", "insane", "Dumpling"):
        assert word not in text, word
    street, snack, brk = data["channels"]
    assert (street["total"], street["on_air"], street["coming"]) == (3, 2, 1), "the picture keeps its size: one cell for each programme"
    assert (snack["total"], snack["on_air"], snack["coming"]) == (3, 1, 2), "the steam tunnel is held back and the dumpling waits for the lantern"
    assert (brk["total"], brk["on_air"], brk["coming"]) == (1, 1, 0)


def test_a_cell_keeps_its_place_when_the_programme_before_it_is_not_on_air(play):
    on_air(play.admin, "steam_tunnel")  # arrives later, in its own cell
    data = board(play.alice)
    snack = [p for p in data["programmes"] if p["channel"] == by_channel_slug(data)["snack"]["id"]]
    assert [(p["slug"], p["cell"], p["order"]) for p in snack] == [("noodle_ledger", 0, 0), ("steam_tunnel", 1, 1)]
    withhold(play.admin, "noodle_ledger")
    data = board(play.alice)
    snack = [p for p in data["programmes"] if p["channel"] == by_channel_slug(data)["snack"]["id"]]
    assert [(p["slug"], p["cell"]) for p in snack] == [("steam_tunnel", 1)], "the steam tunnel did not move up into the empty cell"


def test_a_live_programme_says_so_and_has_no_instance_until_instances_exist(play):
    on_air(play.admin, "steam_tunnel")
    steam = by_slug(board(play.alice))["steam_tunnel"]
    assert steam["live"] is True and steam["instance"] is None and steam["difficulty"] == "insane"


def test_the_storyline_is_told_only_once_the_channel_has_something_on_air(play):
    data = board(play.alice)
    assert by_channel_slug(data)["snack"]["synopsis"] == "A hungry square."
    withhold(play.admin, "noodle_ledger", "dumpling_gate")
    data = board(play.alice)
    snack = by_channel_slug(data)["snack"]
    assert snack["on_air"] == 0 and snack["synopsis"] == "" and snack["coming"] == 3
    assert by_channel_slug(data)["street"]["synopsis"] == "A loud street."


def test_a_programme_with_a_prerequisite_appears_when_the_studio_has_solved_it_and_only_for_that_studio(play):
    assert "dumpling_gate" not in by_slug(board(play.alice))
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    alice, bob = board(play.alice), board(play.bob)
    assert "dumpling_gate" in by_slug(alice) and "dumpling_gate" not in by_slug(bob)
    assert by_channel_slug(alice)["snack"]["on_air"] == 2 and by_channel_slug(bob)["snack"]["on_air"] == 1


def test_a_solve_shows_for_the_studio_its_members_and_the_counts_but_not_for_another_studio(play):
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    for client in (play.alice, play.abe):  # a studio's solve belongs to every member
        lantern = by_slug(board(client))["lantern_walk"]
        assert lantern["solved_by_me"] is True and lantern["solves"] == 1 and lantern["first_blood_open"] is False
    lantern = by_slug(board(play.bob))["lantern_walk"]
    assert lantern["solved_by_me"] is False and lantern["solves"] == 1 and lantern["first_blood_open"] is False
    street = by_channel_slug(board(play.alice))["street"]
    assert (street["solved"], street["signal"]) == (1, 0.33), "one clear of three cells"
    assert by_channel_slug(board(play.bob))["street"]["solved"] == 0


def test_a_hidden_or_banned_studio_does_not_move_the_solve_count_or_the_value(play):
    assert attempt(play.alice, play.ids.moth, "moth-answer") == "correct"
    assert attempt(play.bob, play.ids.moth, "moth-answer") == "correct"
    before = by_slug(board(play.alice))["moth_cipher"]
    assert (before["solves"], before["value"]) == (2, 499)
    team_id = Teams.query.filter_by(name="studio-b").first().id
    assert play.admin.patch(f"/api/v1/teams/{team_id}", json={"hidden": True}).status_code == 200  # a real request: the value is put right when it ends
    after = by_slug(board(play.alice))["moth_cipher"]
    assert (after["solves"], after["value"]) == (1, 500), "the count and the value fall back when a studio stops counting"


def test_the_dynamic_value_on_the_board_is_what_the_programme_is_worth_now(play):
    for client in (play.alice, play.bob):
        assert attempt(client, play.ids.moth, "moth-answer") == "correct"
    moth = by_slug(board(play.bob))["moth_cipher"]
    assert moth["value"] == 499 and moth["solves"] == 2
    assert board(play.bob)["team"]["score"] == 499, "and it is what the studio holds"


def test_the_studios_own_numbers_score_place_and_how_many_studios_count(play):
    data = board(play.alice)
    assert data["team"] == {"id": Teams.query.filter_by(name="studio-a").first().id, "name": "studio-a", "score": 0, "place": None, "of": 2}
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    assert attempt(play.bob, play.ids.coffee, "coffee-answer") == "correct"
    alice, bob = board(play.alice)["team"], board(play.bob)["team"]
    assert (alice["score"], alice["place"]) == (100, 1) and (bob["score"], bob["place"]) == (50, 2)
    # a hint costs TRP and shows in the score at once
    r = play.alice.post("/api/v1/unlocks", json={"target": 1, "type": "hints"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert board(play.alice)["team"]["score"] == 90
    assert board(play.abe)["team"]["score"] == 90, "the same studio, the same numbers for every member"


def test_a_banned_studio_is_not_counted_in_how_many_studios_there_are(play):
    assert board(play.bob)["team"]["of"] == 2
    team_id = Teams.query.filter_by(name="studio-a").first().id
    assert play.admin.patch(f"/api/v1/teams/{team_id}", json={"banned": True}).status_code == 200
    assert board(play.bob)["team"]["of"] == 1


def test_pulling_a_solved_programme_back_removes_its_tile_and_its_clear_but_keeps_the_points(play):
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    assert by_channel_slug(board(play.alice))["street"]["solved"] == 1
    withhold(play.admin, "lantern_walk")
    data = board(play.alice)
    assert "lantern_walk" not in by_slug(data)
    street = by_channel_slug(data)["street"]
    assert (street["solved"], street["signal"], street["on_air"], street["coming"]) == (0, 0, 1, 2)
    assert data["team"]["score"] == 100, "a pull-back never takes TRP away"


def test_the_crew_sees_what_players_see_with_nothing_solved_and_no_studio(play):
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    data = board(play.admin)
    assert data["team"] is None
    assert "dumpling_gate" not in by_slug(data), "the crew previews a withheld programme in the admin panel, not here"
    assert all(p["solved_by_me"] is False for p in data["programmes"]) and by_slug(data)["lantern_walk"]["solves"] == 1


def test_before_the_start_there_are_no_programmes_and_every_total_is_zero(play):
    with clock(T_START - 3600):
        data = board(play.alice)
    assert data["phase"] == {"state": "before", "frozen": False}
    assert data["programmes"] == []
    assert all((c["total"], c["on_air"], c["coming"], c["solved"], c["signal"], c["synopsis"]) == (0, 0, 0, 0, 0, "") for c in data["channels"])
    assert len(data["channels"]) == 3 and data["team"]["score"] == 0


def test_while_paused_the_board_still_lists_the_programmes(play):
    set_config("paused", True)
    data = board(play.alice)
    assert data["phase"] == {"state": "paused", "frozen": False}
    assert len(data["programmes"]) == 4


def test_after_the_end_every_programme_the_plan_lists_is_on_air_again(play):
    with clock(T_END + 60):
        play.alice.get("/api/v1/l3mon/ticks")  # a request after the end lets the scheduler apply the plan's rule
        data = board(play.alice)
    assert data["phase"]["state"] == "ended"
    assert {p["slug"] for p in data["programmes"]} == {"lantern_walk", "moth_cipher", "hush_alley", "noodle_ledger", "steam_tunnel", "coffee_break"}, "the dumpling still waits for the lantern: CTFd keeps a prerequisite after the end"
    assert all(c["coming"] == c["total"] - c["on_air"] for c in data["channels"])


def test_while_the_scoreboard_is_frozen_the_place_is_null_the_counts_are_the_frozen_ones_and_the_own_score_is_live(play):
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    set_config("freeze", T_LIVE + 60)
    with clock(T_LIVE + 120):
        assert attempt(play.bob, play.ids.lantern, "lantern-answer") == "correct"
        # CTFd dates a solve with the real clock (the column default is bound at import), so the two are dated here: one before the freeze, one after
        for team, when in (("studio-a", T_LIVE), ("studio-b", T_LIVE + 120)):
            Submissions.query.filter_by(team_id=Teams.query.filter_by(name=team).first().id, type="correct").update({"date": datetime.datetime.utcfromtimestamp(when)})
        db.session.commit()
        clear_challenges()
        clear_standings()
        alice, bob = board(play.alice), board(play.bob)
    assert alice["phase"] == {"state": "live", "frozen": True}
    assert alice["team"]["place"] is None and bob["team"]["place"] is None, "nobody is told a place while the scoreboard is frozen"
    assert alice["team"]["score"] == 100 and bob["team"]["score"] == 100, "a studio's own score is live"
    assert by_slug(alice)["lantern_walk"]["solves"] == 1, "bob's later solve is not counted for the public"


def test_the_coming_up_count_can_be_switched_off(play):
    assert board(play.alice)["show_coming"] is True
    set_config("l3mon_show_coming_count", "off")
    assert board(play.alice)["show_coming"] is False


def test_the_tick_in_the_board_is_the_platforms_tick(play):
    from CTFd.plugins.l3mon_core.tick import signature

    data = board(play.alice)
    assert data["ver"] == signature() and isinstance(data["ver"], str)


def test_the_news_numbers_follow_the_public_notifications(play):
    base = board(play.alice)
    db.session.add(Notifications(title="Hello", content="world"))
    db.session.commit()
    after = board(play.alice)
    assert after["notif_id"] > base["notif_id"] and after["notif_ver"] != base["notif_ver"]


# ---- the ETag ---------------------------------------------------------------------------------------------------------------

def test_an_unchanged_board_answers_304_with_no_body_and_a_changed_one_does_not(play):
    first = play.alice.get(BOARD)
    etag = first.headers["ETag"]
    assert etag.startswith('"b') and first.headers["Cache-Control"] == "private, no-cache"
    again = play.alice.get(BOARD, headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.get_data() == b"" and again.headers["ETag"] == etag
    assert play.alice.get(BOARD, headers={"If-None-Match": f"W/{etag}"}).status_code == 304
    assert play.alice.get(BOARD, headers={"If-None-Match": "*"}).status_code == 304
    assert play.alice.get(BOARD, headers={"If-None-Match": '"b-nothing"'}).status_code == 200
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    changed = play.alice.get(BOARD, headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["ETag"] != etag


def test_the_tick_alone_does_not_change_the_etag_but_the_news_does(play):
    first = play.alice.get(BOARD)
    etag, ver = first.headers["ETag"], first.get_json()["data"]["ver"]
    tick.bump()  # something changed somewhere (another studio's news, a hidden edit): the pages must ask again, but the board is the same
    second = play.alice.get(BOARD)
    assert second.headers["ETag"] == etag and second.get_json()["data"]["ver"] != ver
    assert play.alice.get(BOARD, headers={"If-None-Match": etag}).status_code == 304
    db.session.add(Notifications(title="Hello", content="world"))
    db.session.commit()
    assert play.alice.get(BOARD).headers["ETag"] != etag, "the bell changed, so the board must be drawn again"


def test_taking_an_older_line_of_news_away_changes_the_etag_too(play):
    for title in ("one", "two"):
        db.session.add(Notifications(title=title, content="x"))
    db.session.commit()
    etag = play.alice.get(BOARD).headers["ETag"]
    older = Notifications.query.order_by(Notifications.id).first().id
    assert play.admin.delete(f"/api/v1/notifications/{older}", json={}).status_code == 200
    assert play.alice.get(BOARD).headers["ETag"] != etag, "the newest id is the same; the bell count is not"


def test_two_studios_get_their_own_etag_when_their_boards_differ(play):
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    a, b = play.alice.get(BOARD).headers["ETag"], play.bob.get(BOARD).headers["ETag"]
    assert a != b
    assert play.alice.get(BOARD, headers={"If-None-Match": b}).status_code == 200, "another studio's tag never answers 304"


# ---- how much it costs --------------------------------------------------------------------------------------------------------

def count_statements(fn):
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return len(seen)


def test_the_board_costs_the_same_handful_of_queries_whatever_the_number_of_programmes(play):
    from tests.helpers import gen_challenge

    play.alice.get(BOARD)  # warm CTFd's own caches
    few = count_statements(lambda: play.alice.get(BOARD))
    extra = [gen_challenge(db, name=f"extra {i}", state="hidden", value=10).id for i in range(40)]
    plan = {"programmes": [{"challenge_id": cid, "channel": "street", "cell": 100 + i, "number": 900 + i, "slug": f"extra_{i}"} for i, cid in enumerate(extra)]}
    assert play.admin.put("/api/v1/l3mon/admin/programmes", json=plan).status_code == 200
    on_air(play.admin, *[f"extra_{i}" for i in range(40)])
    play.alice.get(BOARD)
    many = count_statements(lambda: play.alice.get(BOARD))
    assert len(board(play.alice)["programmes"]) == 44
    assert many <= few + 1, f"{few} queries for 4 programmes, {many} for 44: it must not grow with the number of programmes"
    assert many <= 25, f"{many} queries is more than a handful"
