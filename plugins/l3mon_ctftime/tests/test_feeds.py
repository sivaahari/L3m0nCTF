"""The CTFtime feeds, inside a real CTFd app with real standings.

Run through tools/run-ctfd-tests.sh, which provides CTFd's own test helpers:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_ctftime
"""
import json
import time
from datetime import datetime, timedelta

from CTFd.cache import clear_standings
from CTFd.models import Solves, Teams, Users, db
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_award, gen_challenge, gen_solve, gen_team, gen_user

LIVE = "/ctftime/standings.json"
FINAL = "/ctftime/final-standings.json"


def feed(client, path=LIVE):
    r = client.get(path)
    return r, (json.loads(r.get_data(as_text=True)) if r.status_code == 200 else None)


def move_solve(solve_id, by):
    """Put a solve earlier or later, so the tests control the freeze and the tie-break without sleeping."""
    solve = db.session.get(Solves, solve_id)
    solve.date = datetime.utcnow() + by
    db.session.commit()
    clear_standings()


def test_an_empty_event_gives_an_empty_list_and_the_documented_headers():
    app = create_ctfd(enable_plugins=True)
    with app.app_context(), app.test_client() as client:
        r, data = feed(client)
        assert r.status_code == 200 and data == {"standings": []}
        assert r.mimetype == "application/json"
        assert r.headers["Cache-Control"] == "public, max-age=15"
    destroy_ctfd(app)


def test_the_route_does_not_exist_when_plugins_are_off():
    app = create_ctfd(enable_plugins=False)
    with app.app_context(), app.test_client() as client:
        assert client.get(LIVE).status_code == 404
    destroy_ctfd(app)


def test_rows_are_ranked_by_score_then_by_who_got_there_first_with_exactly_three_keys():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=100)
        other = gen_challenge(db, name="second", value=50)
        a = gen_team(db, name="Alpha", email="a@example.com", member_count=1)
        b = gen_team(db, name="Bravo", email="b@example.com", member_count=1)
        c = gen_team(db, name="Charlie", email="c@example.com", member_count=1)
        gen_solve(db, user_id=c.members[0].id, team_id=c.id, challenge_id=chal.id)  # 100, first
        sb = gen_solve(db, user_id=b.members[0].id, team_id=b.id, challenge_id=chal.id)  # 100, later
        move_solve(sb.id, timedelta(minutes=5))
        gen_solve(db, user_id=a.members[0].id, team_id=a.id, challenge_id=chal.id)
        sa2 = gen_solve(db, user_id=a.members[0].id, team_id=a.id, challenge_id=other.id)  # 150 in all
        move_solve(sa2.id, timedelta(minutes=10))
        r, data = feed(client)
        assert [row["team"] for row in data["standings"]] == ["Alpha", "Charlie", "Bravo"]
        assert [row["pos"] for row in data["standings"]] == [1, 2, 3]
        assert [row["score"] for row in data["standings"]] == [150, 100, 100]
        for row in data["standings"]:
            assert set(row) == {"pos", "team", "score"}
            assert isinstance(row["score"], int)
    destroy_ctfd(app)


def test_users_mode_lists_users():
    app = create_ctfd(enable_plugins=True, user_mode="users")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=300)
        u = gen_user(db, name="solo", email="solo@example.com")
        gen_solve(db, user_id=u.id, challenge_id=chal.id)
        _, data = feed(client)
        assert data == {"standings": [{"pos": 1, "team": "solo", "score": 300}]}
    destroy_ctfd(app)


def test_hidden_banned_and_non_positive_accounts_never_appear_and_positions_have_no_gaps():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=100)
        names = ["Banned one", "Hidden one", "Negative", "Visible one", "Visible two"]
        teams = {n: gen_team(db, name=n, email=f"{i}@example.com", member_count=1) for i, n in enumerate(names)}
        for n in ("Banned one", "Hidden one", "Visible one", "Visible two"):
            t = teams[n]
            gen_solve(db, user_id=t.members[0].id, team_id=t.id, challenge_id=chal.id)
        teams["Banned one"].banned = True
        teams["Hidden one"].hidden = True
        gen_award(db, user_id=teams["Negative"].members[0].id, team_id=teams["Negative"].id, value=-50)
        db.session.commit()
        clear_standings()
        _, data = feed(client)
        assert [r["team"] for r in data["standings"]] == ["Visible one", "Visible two"]
        assert [r["pos"] for r in data["standings"]] == [1, 2]
    destroy_ctfd(app)


def test_names_with_any_characters_round_trip_and_the_bytes_are_plain_ascii():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=10)
        tricky = ['Ünï "quoted" \\ back', "emoji 🍋 team", "עברית", "tab\tname"]
        for i, name in enumerate(tricky):
            t = gen_team(db, name=name, email=f"t{i}@example.com", member_count=1)
            gen_solve(db, user_id=t.members[0].id, team_id=t.id, challenge_id=chal.id)
        r = client.get(LIVE)
        r.get_data().decode("ascii")  # raises if any byte is above 0x7F
        got = {row["team"] for row in json.loads(r.get_data(as_text=True))["standings"]}
        assert got == set(tricky)
    destroy_ctfd(app)


def test_no_email_or_other_personal_data_is_in_the_body():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=10)
        t = gen_team(db, name="Privacy", email="captain.secret@example.com", member_count=2)
        gen_solve(db, user_id=t.members[0].id, team_id=t.id, challenge_id=chal.id)
        body = client.get(LIVE).get_data(as_text=True)
        assert "example.com" not in body and "captain" not in body
        assert t.members[0].name not in body
    destroy_ctfd(app)


def test_the_live_feed_is_frozen_with_the_scoreboard_and_the_final_one_is_not():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=100)
        late = gen_challenge(db, name="after the freeze", value=500)
        a = gen_team(db, name="Early bird", email="e@example.com", member_count=1)
        b = gen_team(db, name="Late finisher", email="l@example.com", member_count=1)
        gen_solve(db, user_id=a.members[0].id, team_id=a.id, challenge_id=chal.id)
        sl = gen_solve(db, user_id=b.members[0].id, team_id=b.id, challenge_id=late.id)
        move_solve(sl.id, timedelta(hours=2))
        set_config("freeze", int(time.time()) + 3600)  # the freeze moment is an hour away; the late solve is two hours away
        clear_standings()
        _, data = feed(client)
        assert [r["team"] for r in data["standings"]] == ["Early bird"]
        # the event has ended and the standings are published: the freeze no longer hides anything
        set_config("end", int(time.time()) - 10)
        set_config("l3mon_final_standings_published", True)
        clear_standings()
        _, data = feed(client, FINAL)
        assert [(r["team"], r["score"]) for r in data["standings"]] == [("Late finisher", 500), ("Early bird", 100)]
    destroy_ctfd(app)


def test_the_final_feed_needs_both_the_end_of_the_event_and_the_organisers_go_ahead():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=100)
        t = gen_team(db, name="Winner", email="w@example.com", member_count=1)
        gen_solve(db, user_id=t.members[0].id, team_id=t.id, challenge_id=chal.id)
        # running, not published
        assert client.get(FINAL).status_code == 404
        # published too early (the event has not ended): still nothing
        set_config("l3mon_final_standings_published", True)
        set_config("end", int(time.time()) + 3600)
        assert client.get(FINAL).status_code == 404
        # ended but the go-ahead is withdrawn (a late cheating case): nothing
        set_config("end", int(time.time()) - 10)
        set_config("l3mon_final_standings_published", False)
        r = client.get(FINAL)
        assert r.status_code == 404 and r.headers["Cache-Control"] == "no-store"
        assert json.loads(r.get_data(as_text=True))["error"] == "not_found"
        # both conditions: served
        set_config("l3mon_final_standings_published", True)
        r, data = feed(client, FINAL)
        assert r.status_code == 200 and data == {"standings": [{"pos": 1, "team": "Winner", "score": 100}]}
    destroy_ctfd(app)


def test_the_final_feed_leaves_out_accounts_banned_after_the_freeze():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=100)
        ok = gen_team(db, name="Clean", email="c@example.com", member_count=1)
        cheat = gen_team(db, name="Cheater", email="x@example.com", member_count=1)
        gen_solve(db, user_id=ok.members[0].id, team_id=ok.id, challenge_id=chal.id)
        gen_solve(db, user_id=cheat.members[0].id, team_id=cheat.id, challenge_id=chal.id)
        db.session.get(Teams, cheat.id).banned = True
        db.session.get(Teams, cheat.id).hidden = False
        db.session.commit()
        set_config("end", int(time.time()) - 10)
        set_config("l3mon_final_standings_published", True)
        clear_standings()
        _, data = feed(client, FINAL)
        assert [r["team"] for r in data["standings"]] == ["Clean"]
    destroy_ctfd(app)


def test_both_feeds_work_without_signing_in_and_ignore_the_cookie():
    app = create_ctfd(enable_plugins=True, user_mode="users")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=10)
        u = db.session.get(Users, gen_user(db, name="someone", email="s@example.com").id)
        gen_solve(db, user_id=u.id, challenge_id=chal.id)
        anonymous = client.get(LIVE).get_data()
        with_cookie = client.get(LIVE, headers={"Cookie": "session=not-a-real-session"}).get_data()
        assert anonymous == with_cookie
    destroy_ctfd(app)


def test_before_the_start_the_live_feed_is_empty_whatever_was_given_to_a_studio_as_the_scoreboard_is_and_the_start_opens_it():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context(), app.test_client() as client:
        chal = gen_challenge(db, value=100)
        t = gen_team(db, name="Early", email="e@example.com", member_count=1)
        gen_solve(db, user_id=t.members[0].id, team_id=t.id, challenge_id=chal.id)
        gen_award(db, user_id=t.members[0].id, team_id=t.id, value=40, name="a gift before the start")
        set_config("start", int(time.time()) + 3600)
        clear_standings()
        r, data = feed(client)
        assert r.status_code == 200 and data == {"standings": []}, "nothing is ranked before the broadcast starts"
        set_config("start", int(time.time()) - 60)
        clear_standings()
        _, data = feed(client)
        assert [(row["team"], row["score"]) for row in data["standings"]] == [("Early", 140)]
    destroy_ctfd(app)
