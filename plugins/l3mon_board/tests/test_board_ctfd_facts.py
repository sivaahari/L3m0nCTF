"""What the board part relies on in CTFd 3.8.8, kept as tests (measured by probe_facts.py on 2026-10-09). If a CTFd upgrade changes one of
these, this file fails first and says which assumption moved.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import datetime
from types import SimpleNamespace

import pytest

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Awards, Notifications, Solves, Submissions, Teams, Unlocks, Users, db
from CTFd.utils import set_config
from board_world import T_END, T_LIVE, T_START, clock, make_app, started, team_client
from tests.helpers import destroy_ctfd, gen_challenge, gen_flag, gen_hint

ATTEMPT = "/api/v1/challenges/attempt"


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            chal = gen_challenge(db, name="probe", value=100, state="visible")
            limited = gen_challenge(db, name="limited", value=100, state="visible", max_attempts=2)
            gen_flag(db, chal.id, content="F")
            gen_flag(db, limited.id, content="F")
            hint = gen_hint(db, chal.id, content="h", cost=5)
            ids = SimpleNamespace(chal=chal.id, limited=limited.id, hint=hint.id)
            alice = team_client(app, "alice", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            clear_challenges()
            yield SimpleNamespace(app=app, ids=ids, alice=alice, bob=bob)
    destroy_ctfd(app)


@pytest.fixture(autouse=True)
def ctfd_alone(play):
    """Everything in this file is about CTFd by itself, so our decorations and guards are switched off for it."""
    from CTFd.plugins.l3mon_board import notices, panel, replies

    kept = dict(panel.ON), dict(replies.ON), dict(notices.ON)
    for switches in (panel.ON, replies.ON, notices.ON):
        switches.update({key: False for key in switches})
    yield
    panel.ON.update(kept[0])
    replies.ON.update(kept[1])
    notices.ON.update(kept[2])


def send(client, cid, flag):
    return client.post(ATTEMPT, json={"challenge_id": cid, "submission": flag})


def test_before_the_start_every_challenge_route_answers_403_with_one_bare_sentence_for_a_real_and_a_missing_id(play):
    with clock(T_START - 3600):
        answers = [
            play.alice.get(f"/api/v1/challenges/{play.ids.chal}"), play.alice.get("/api/v1/challenges/99999"),
            play.alice.get("/api/v1/challenges"), send(play.alice, play.ids.chal, "F"),
        ]
    for r in answers:
        assert r.status_code == 403 and r.get_json() == {"message": "CTFd has not started yet"}


def test_after_the_end_with_view_after_ctf_off_every_challenge_route_answers_403_has_ended(play):
    set_config("view_after_ctf", False)
    with clock(T_END + 60):
        for r in (play.alice.get(f"/api/v1/challenges/{play.ids.chal}"), play.alice.get("/api/v1/challenges"), send(play.alice, play.ids.chal, "F")):
            assert r.status_code == 403 and r.get_json() == {"message": "CTFd has ended"}


def test_after_the_end_with_view_after_ctf_on_a_right_flag_is_called_correct_and_nothing_is_recorded(play):
    set_config("view_after_ctf", True)
    with clock(T_END + 60):
        right = send(play.alice, play.ids.chal, "F")
        wrong = send(play.alice, play.ids.chal, "nope")
    assert right.status_code == 200 and right.get_json()["data"]["status"] == "correct", "this is why the guard exists: a studio could still test its flags"
    assert wrong.get_json()["data"]["status"] == "incorrect"
    assert Solves.query.count() == 0


def test_a_flag_while_paused_is_403_with_status_paused(play):
    set_config("paused", True)
    r = send(play.alice, play.ids.chal, "F")
    assert r.status_code == 403 and r.get_json() == {"success": True, "data": {"status": "paused", "message": "CTFd is paused"}}


def test_the_wording_of_the_tries_messages_the_reasons_are_read_from(play):
    cid = play.ids.limited
    first, second = send(play.alice, cid, "nope"), send(play.alice, cid, "nope")
    assert first.get_json()["data"]["message"] == "Incorrect. You have 1 try remaining."
    assert second.get_json()["data"]["message"] == "Incorrect. You have 0 tries remaining."
    locked = send(play.alice, cid, "nope")
    assert locked.status_code == 403 and locked.get_json()["data"] == {"status": "ratelimited", "message": "Not accepted. You have 0 tries remaining"}


def test_the_wording_of_the_too_fast_message_and_the_lock_while_one_flag_is_checked(play):
    from CTFd.cache import cache

    set_config("incorrect_submissions_per_min", 1)
    send(play.alice, play.ids.chal, "nope")
    fast = send(play.alice, play.ids.chal, "nope")
    message = fast.get_json()["data"]["message"]
    assert fast.status_code == 429 and message.startswith("You're submitting flags too fast. Try again in ") and message.endswith(" seconds.")
    account = Teams.query.filter_by(name="studio-b").first().id
    cache.add(f"submission_lock_{account}_{play.ids.limited}_lockout", 1, timeout=30)
    busy = send(play.bob, play.ids.limited, "F")
    assert busy.status_code == 403 and busy.get_json()["data"]["message"].startswith("Another submission is already being processed")


def test_a_studio_with_no_solve_award_or_hint_has_no_place_and_the_standings_hold_only_studios_that_scored(play):
    from CTFd.utils.scores import get_standings

    clear_standings()
    studio = Teams.query.filter_by(name="studio-a").first()
    assert studio.get_place() is None and studio.get_score() == 0 and list(get_standings()) == []
    assert send(play.alice, play.ids.chal, "F").get_json()["data"]["status"] == "correct"
    clear_standings()
    assert Teams.query.filter_by(name="studio-a").first().get_place(numeric=True) == 1
    assert Teams.query.filter_by(name="studio-b").first().get_place() is None


def test_under_a_freeze_ctfd_shows_even_the_owner_a_frozen_score_and_admin_true_is_the_live_one(play):
    from CTFd.utils.challenges import get_solve_counts_for_challenges

    set_config("freeze", T_LIVE - 3600)
    assert send(play.alice, play.ids.chal, "F").get_json()["data"]["status"] == "correct"
    Submissions.query.filter_by(type="correct").update({"date": datetime.datetime.utcfromtimestamp(T_LIVE)})
    db.session.commit()
    clear_standings()
    clear_challenges()
    studio = Teams.query.filter_by(name="studio-a").first()
    assert studio.get_score() == 0 and studio.get_place() is None, "frozen, for everybody"
    assert studio.get_score(admin=True) == 100, "this is what the board's own score uses"
    assert get_solve_counts_for_challenges(admin=False) == {} and get_solve_counts_for_challenges(admin=True) == {play.ids.chal: 1}


def test_the_solve_counts_leave_out_hidden_and_banned_studios(play):
    from CTFd.utils.challenges import get_solve_counts_for_challenges

    send(play.alice, play.ids.chal, "F")
    send(play.bob, play.ids.chal, "F")
    clear_challenges()
    assert get_solve_counts_for_challenges(admin=False) == {play.ids.chal: 2}
    Teams.query.filter_by(name="studio-b").first().hidden = True
    db.session.commit()
    clear_challenges()
    assert get_solve_counts_for_challenges(admin=False) == {play.ids.chal: 1}


@pytest.mark.parametrize("state", ["paused", "ended"])
def test_ctfd_alone_lets_a_studio_spend_TRP_on_a_hint_while_paused_or_after_the_end(play, state):
    """The reason for the hint guard: nothing in CTFd stops it, and a purchase after the end would change the final standings."""
    send(play.alice, play.ids.chal, "F")
    set_config("view_after_ctf", True)
    if state == "paused":
        set_config("paused", True)
    with clock(T_LIVE if state == "paused" else T_END + 60):
        r = play.alice.post("/api/v1/unlocks", json={"target": play.ids.hint, "type": "hints"})
    assert r.status_code == 200, (state, r.status_code, r.get_data(as_text=True))


# ---- part 3.5: the bell, the standings, a hint's rows (measured 2026-10-10)

def test_the_stock_notifications_routes_are_open_to_a_visitor_and_hand_out_team_addressed_lines_with_dates(play):
    team_b = Teams.query.filter_by(name="studio-b").one().id
    db.session.add(Notifications(title="for everyone", content="public"))
    db.session.add(Notifications(title="for studio b", content="addressed", team_id=team_b))
    db.session.commit()
    visitor = play.app.test_client()
    listing = visitor.get("/api/v1/notifications")
    assert listing.status_code == 200, "no sign-in is asked for"
    rows = {row["title"]: row for row in listing.get_json()["data"]}
    assert set(rows) == {"for everyone", "for studio b"} and rows["for studio b"]["team_id"] == team_b and rows["for studio b"]["date"]
    assert sorted(rows["for everyone"]) == ["content", "date", "html", "id", "team", "team_id", "title", "user", "user_id"]
    assert visitor.get(f"/api/v1/notifications/{rows['for studio b']['id']}").get_json()["data"]["content"] == "addressed", "the detail route too"
    assert visitor.head("/api/v1/notifications").headers["Result-Count"] == "2"
    assert [r["title"] for r in visitor.get(f"/api/v1/notifications?team_id={team_b}").get_json()["data"]] == ["for studio b"], "and anybody may ask for one studio's lines"
    page = play.alice.get("/notifications")
    assert page.status_code == 200 and "for studio b" in page.get_data(as_text=True), "the stock page lists them for any signed-in player"


def test_a_since_id_that_is_not_a_number_is_ctfds_own_400(play):
    r = play.alice.get("/api/v1/notifications?since_id=abc")
    assert r.status_code == 400 and r.get_json()["errors"] == {"since_id": "value is not a valid integer"}


def test_the_standings_rows_have_an_account_a_name_and_a_score_and_no_date_or_solve_count(play):
    from CTFd.utils.scores import get_standings

    assert send(play.alice, play.ids.chal, "F").get_json()["data"]["status"] == "correct"
    clear_standings()
    rows = get_standings(admin=False)
    assert sorted(rows[0]._fields) == ["account_id", "bracket_id", "bracket_name", "name", "oauth_id", "score"]
    assert [(r.name, int(r.score)) for r in rows] == [("studio-a", 100)], "a studio with no score is not in the list"


def test_a_hint_purchase_makes_an_unlock_and_an_award_named_hint_with_the_negative_cost(play):
    send(play.alice, play.ids.chal, "F")  # a studio can buy only what its score covers
    r = play.alice.post("/api/v1/unlocks", json={"target": play.ids.hint, "type": "hints"})
    assert r.status_code == 200 and sorted(r.get_json()["data"]) == ["date", "id", "target", "team_id", "type", "user_id"]
    team = Teams.query.filter_by(name="studio-a").one()
    award = Awards.query.filter_by(team_id=team.id).one()
    assert (award.category, award.value, award.name.startswith("Hint")) == ("hints", -5, True)
    assert Unlocks.query.filter_by(team_id=team.id, type="hints").count() == 1


def test_a_solve_remembers_the_member_who_sent_the_flag_and_the_captain_is_the_first_member(play):
    send(play.alice, play.ids.chal, "F")
    team = Teams.query.filter_by(name="studio-a").one()
    solve = Solves.query.filter_by(team_id=team.id).one()
    assert solve.user_id == Users.query.filter_by(name="alice").one().id == team.captain_id

