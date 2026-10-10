"""The reply to a flag: CTFd's own answer (it judges the flag, records the solve and values the challenge), with `l3mon` added.

correct      {value, reel, reels_needed, channel_signal, channel_complete, first_blood, solves}
incorrect    {tries_left} when the challenge limits tries
the 403 and 429 replies   {reason: processing | no_tries | ratelimited | paused | ended, retry_after?}

After the end CTFd would say "correct" to a right flag and record nothing; a guard answers the way the demo does, and CTFd never
sees the flag. Hints follow the same phase rules (the contract's `phase_closed`).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
from types import SimpleNamespace

import pytest

from CTFd.cache import cache, clear_challenges, clear_standings
from CTFd.models import Solves, Teams, Users, db
from CTFd.utils import set_config
from board_world import T_END, T_LIVE, T_START, clock, join_client, make_app, on_air, started, team_client, withhold, world
from tests.helpers import destroy_ctfd, gen_challenge, gen_flag, login_as_user

ATTEMPT = "/api/v1/challenges/attempt"


def send(client, challenge_id, flag):
    return client.post(ATTEMPT, json={"challenge_id": challenge_id, "submission": flag})


def data(response):
    return response.get_json()["data"]


@pytest.fixture()
def play():
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


def limited(play, tries=2, slug="careful_door"):
    challenge = gen_challenge(db, name="Careful Door", value=80, state="hidden", max_attempts=tries)
    gen_flag(db, challenge.id, content="careful-answer")
    cid = challenge.id
    play.admin.put("/api/v1/l3mon/admin/programmes", json={"programmes": [{"challenge_id": cid, "channel": "street", "cell": 8, "number": 108, "slug": slug}]})
    on_air(play.admin, slug)
    return cid


# ---- a correct flag ---------------------------------------------------------------------------------------------------------

def test_a_correct_flag_keeps_ctfds_answer_and_adds_the_programmes_numbers(play):
    r = send(play.alice, play.ids.lantern, "lantern-answer")
    assert r.status_code == 200 and data(r)["status"] == "correct" and data(r)["message"] == "Correct"
    assert data(r)["l3mon"] == {"value": 100, "reel": 1, "reels_needed": 3, "channel_signal": 0.33, "channel_complete": False, "first_blood": True, "solves": 1}


def test_the_second_studio_is_not_first_and_the_count_includes_it(play):
    send(play.alice, play.ids.lantern, "lantern-answer")
    block = data(send(play.bob, play.ids.lantern, "lantern-answer"))["l3mon"]
    assert block["first_blood"] is False and block["solves"] == 2


def test_the_value_in_the_reply_is_the_value_after_this_solve(play):
    assert data(send(play.alice, play.ids.moth, "moth-answer"))["l3mon"]["value"] == 500
    assert data(send(play.bob, play.ids.moth, "moth-answer"))["l3mon"]["value"] == 499, "the dynamic value had just fallen"


def test_the_reel_and_the_signal_count_only_programmes_the_studio_can_see_and_the_reels_needed_is_a_third_of_all_the_cells(play):
    send(play.alice, play.ids.lantern, "lantern-answer")
    withhold(play.admin, "lantern_walk")
    block = data(send(play.alice, play.ids.moth, "moth-answer"))["l3mon"]
    assert block["reel"] == 1, "the pulled-back programme no longer counts"
    assert block["channel_signal"] == 0.33 and block["reels_needed"] == 3, "seven cells in all, a third of them rounded up"


def test_a_channel_is_complete_when_every_cell_of_its_picture_is_clear(play):
    block = data(send(play.alice, play.ids.coffee, "coffee-answer"))["l3mon"]
    assert block["channel_signal"] == 1 and block["channel_complete"] is True
    send(play.alice, play.ids.lantern, "lantern-answer")
    block = data(send(play.alice, play.ids.moth, "moth-answer"))["l3mon"]
    assert block["channel_complete"] is False, "the street has a third cell that is not on air, so it cannot be complete"


def test_first_blood_goes_to_the_earliest_solve_among_the_studios_that_count(play):
    # a hidden studio solved it first: it does not count, so this studio is first
    ghost = team_client(play.app, "ghost", "studio-ghost")
    Teams.query.filter_by(name="studio-ghost").first().hidden = True
    db.session.commit()
    assert data(send(ghost, play.ids.lantern, "lantern-answer"))["status"] == "correct"
    assert data(send(play.alice, play.ids.lantern, "lantern-answer"))["l3mon"]["first_blood"] is True
    # a counted studio solved it first (recorded just before this request): this one is not
    bob_team = Teams.query.filter_by(name="studio-b").first()
    bob_user = Users.query.filter_by(name="bob").first()
    db.session.add(Solves(user_id=bob_user.id, team_id=bob_team.id, challenge_id=play.ids.noodle, ip="127.0.0.1", provided="x"))
    db.session.commit()
    clear_challenges()
    clear_standings()
    block = data(send(play.alice, play.ids.noodle, "noodle-answer"))["l3mon"]
    assert block["first_blood"] is False and block["solves"] == 2


def test_a_hidden_studio_is_first_when_no_counted_studio_was_ahead_of_it(play):
    ghost = team_client(play.app, "ghost", "studio-ghost")
    Teams.query.filter_by(name="studio-ghost").first().hidden = True
    db.session.commit()
    block = data(send(ghost, play.ids.coffee, "coffee-answer"))["l3mon"]
    assert block["first_blood"] is True and block["solves"] == 0, "nobody that counts has solved it, and the hidden studio's own solve is not counted"
    send(play.alice, play.ids.coffee, "coffee-answer")
    assert data(send(play.bob, play.ids.coffee, "coffee-answer"))["l3mon"]["first_blood"] is False


def test_while_frozen_the_reply_counts_the_studios_own_solve_and_tells_nothing_about_another_studios_later_solve(play):
    """CTFd dates a solve with the real clock (the column default is bound at import), so the freeze is set just before the real now: every
    solve this test makes is after it."""
    import calendar

    from freezegun.api import real_datetime

    set_config("freeze", calendar.timegm(real_datetime.utcnow().timetuple()) - 100)
    first = data(send(play.alice, play.ids.lantern, "lantern-answer"))["l3mon"]
    second = data(send(play.bob, play.ids.lantern, "lantern-answer"))["l3mon"]
    assert (first["solves"], first["first_blood"]) == (1, True), "the public count leaves the studio's own later solve out; the studio is told its own"
    assert (second["solves"], second["first_blood"]) == (1, True), "bob may not learn that alice solved it after the freeze"


def test_a_hidden_studio_is_told_the_same_count_frozen_or_not(play):
    """Its own solve is never in the public count, so freezing must not add one to what it is told (found by the independent review)."""
    import calendar

    from freezegun.api import real_datetime

    ghost = team_client(play.app, "ghost", "studio-ghost")
    Teams.query.filter_by(name="studio-ghost").first().hidden = True
    db.session.commit()
    set_config("freeze", calendar.timegm(real_datetime.utcnow().timetuple()) - 100)
    block = data(send(ghost, play.ids.coffee, "coffee-answer"))["l3mon"]
    assert block["solves"] == 0 and block["first_blood"] is True


def test_a_flag_for_a_programme_already_solved_is_ctfds_answer_without_extras(play):
    send(play.alice, play.ids.lantern, "lantern-answer")
    r = send(play.abe, play.ids.lantern, "lantern-answer")
    assert data(r)["status"] == "already_solved" and "l3mon" not in data(r)


def test_the_crews_preview_is_left_alone(play):
    r = play.admin.post(ATTEMPT + "?preview=true", json={"challenge_id": play.ids.lantern, "submission": "lantern-answer"})
    assert r.status_code == 200 and data(r)["status"] == "correct" and "l3mon" not in data(r)


# ---- a wrong flag -----------------------------------------------------------------------------------------------------------

def test_a_wrong_flag_has_no_extras_when_tries_are_not_limited_and_the_tries_left_when_they_are(play):
    assert "l3mon" not in data(send(play.alice, play.ids.lantern, "nope"))
    cid = limited(play, tries=3)
    first = data(send(play.alice, cid, "nope"))
    assert first["status"] == "incorrect" and first["l3mon"] == {"tries_left": 2} and "2 tries remaining" in first["message"]
    assert data(send(play.abe, cid, "nope"))["l3mon"] == {"tries_left": 1}, "the studio's tries are shared by its members"
    assert data(send(play.alice, cid, "nope"))["l3mon"] == {"tries_left": 0}


# ---- the refusals -----------------------------------------------------------------------------------------------------------

def test_a_studio_that_has_used_every_try_is_told_why(play):
    cid = limited(play, tries=1)
    send(play.alice, cid, "nope")
    r = send(play.alice, cid, "nope")
    assert r.status_code == 403 and data(r)["status"] == "ratelimited" and data(r)["l3mon"] == {"reason": "no_tries", "tries_left": 0}
    r = send(play.alice, cid, "careful-answer")
    assert data(r)["l3mon"]["reason"] == "no_tries", "even the right flag: the studio is locked out"


def test_too_many_wrong_flags_a_minute_is_ratelimited_with_the_seconds_to_wait_in_the_body_and_the_header(play):
    set_config("incorrect_submissions_per_min", 2)
    for _ in range(2):
        assert send(play.alice, play.ids.lantern, "nope").status_code == 200
    r = send(play.alice, play.ids.lantern, "nope")
    assert r.status_code == 429 and data(r)["status"] == "ratelimited"
    extra = data(r)["l3mon"]
    assert extra["reason"] == "ratelimited" and 1 <= extra["retry_after"] <= 60 and r.headers["Retry-After"] == str(extra["retry_after"])
    import re

    assert extra["retry_after"] == int(re.search(r"in (\d+) seconds", data(r)["message"]).group(1)), "the seconds CTFd named, not a guess"


def test_a_second_flag_while_one_is_being_checked_is_told_to_wait_a_second(play):
    cid = limited(play, tries=5)
    account = Teams.query.filter_by(name="studio-a").first().id
    assert cache.add(f"submission_lock_{account}_{cid}_lockout", 1, timeout=30)  # the lock CTFd takes for the length of one check
    r = send(play.alice, cid, "careful-answer")
    assert r.status_code == 403 and data(r)["l3mon"] == {"reason": "processing", "retry_after": 1} and r.headers["Retry-After"] == "1"
    cache.delete(f"submission_lock_{account}_{cid}_lockout")


def test_a_flag_while_paused_is_told_it_was_not_checked(play):
    set_config("paused", True)
    r = send(play.alice, play.ids.lantern, "lantern-answer")
    assert r.status_code == 403 and data(r)["status"] == "paused" and data(r)["l3mon"] == {"reason": "paused"}
    set_config("paused", False)
    assert Solves.query.count() == 0


# ---- the end and the start ---------------------------------------------------------------------------------------------------

def test_after_the_end_a_flag_is_refused_the_same_way_whether_it_is_right_or_wrong_and_nothing_is_recorded(play):
    with clock(T_END + 60):
        right = send(play.alice, play.ids.lantern, "lantern-answer")
        wrong = send(play.alice, play.ids.lantern, "nope")
    for r in (right, wrong):
        assert r.status_code == 403 and data(r)["status"] == "paused" and data(r)["l3mon"] == {"reason": "ended"}
    assert right.get_json() == wrong.get_json(), "a studio learns nothing about a flag after the end"
    assert Solves.query.count() == 0


def test_after_the_end_the_crew_can_still_preview_and_a_visitor_keeps_ctfds_own_answer(play):
    with clock(T_END + 60):
        assert play.admin.post(ATTEMPT + "?preview=true", json={"challenge_id": play.ids.lantern, "submission": "lantern-answer"}).status_code == 200
        r = play.app.test_client().post(ATTEMPT, json={"challenge_id": play.ids.lantern, "submission": "x"})
        assert r.status_code == 403 and (r.get_json(silent=True) or {}).get("data", {}).get("l3mon") is None


def test_before_the_start_a_flag_is_refused_without_looking_at_it(play):
    with clock(T_START - 60):
        r = send(play.alice, play.ids.lantern, "lantern-answer")
    assert r.status_code == 403 and r.get_json()["error"] == "phase_closed" and r.get_json()["phase"] == "before"
    assert Solves.query.count() == 0


# ---- hints follow the same phases ---------------------------------------------------------------------------------------------

def unlock(client):
    return client.post("/api/v1/unlocks", json={"target": 1, "type": "hints"})


@pytest.mark.parametrize(
    "when,configure,phase,message",
    [
        ("before", None, "before", "Hints open when the broadcast starts."),
        ("paused", "paused", "paused", "Hints are paused for a moment. No TRP was spent."),
        ("ended", None, "ended", "The broadcast has ended. Hints can no longer be unlocked."),
    ],
)
def test_a_hint_cannot_be_bought_before_the_start_while_paused_or_after_the_end_and_no_TRP_is_spent(play, when, configure, phase, message):
    send(play.alice, play.ids.lantern, "lantern-answer")
    if configure:
        set_config(configure, True)
    t = {"before": T_START - 60, "paused": T_LIVE, "ended": T_END + 60}[when]
    with clock(t):
        r = unlock(play.alice)
    assert r.status_code == 403 and r.get_json()["error"] == "phase_closed" and r.get_json()["phase"] == phase and r.get_json()["message"] == message
    set_config("paused", False)
    assert Teams.query.filter_by(name="studio-a").first().get_score(admin=True) == 100, "nothing was spent"


def test_a_hint_can_be_bought_while_the_broadcast_is_live_and_the_crew_is_not_blocked(play):
    send(play.alice, play.ids.lantern, "lantern-answer")
    r = unlock(play.alice)
    assert r.status_code == 200
    with clock(T_END + 60):
        try:
            got = (play.admin.post("/api/v1/unlocks", json={"target": 2, "type": "hints"}).get_json(silent=True) or {}).get("error")
        except AttributeError:
            got = None  # CTFd's own route has no studio to charge for a crew account; that is CTFd's, not ours
        assert got != "phase_closed", "the crew is not stopped by the studios' phase rule"
