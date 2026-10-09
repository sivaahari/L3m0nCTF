"""After anything that changes who counts, a dynamic value is put right: in the same request, and by a check every minute for what no
hook sees. Each CTFd admin action is run through its real route; each hook is switched off in turn and must be missed.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import logging
from types import SimpleNamespace
from unittest import mock

import pytest

from CTFd.cache import cache
from CTFd.models import Challenges, Solves, Teams, Users, db
from CTFd.plugins.l3mon_core.models import Audit
from CTFd.plugins.l3mon_scoring import triggers, values
from scoring_world import attempt, dynamic, make_app, stored_value, team_client
from tests.helpers import destroy_ctfd, login_as_user

FLAG = "FLAG-DYN"
NAMES = ["alice", "bob", "carol", "dave"]


@pytest.fixture()
def world():
    app = make_app()
    with app.app_context():
        admin = login_as_user(app, "admin")
        cid = dynamic("dyn", flag=FLAG).id  # the id now: a web request detaches every object this session holds
        clients = {}
        for name in NAMES:
            clients[name] = team_client(app, name, f"team-{name}")
        for name in NAMES:
            assert attempt(clients[name], cid, FLAG) == "correct"
        assert stored_value(cid) == 488  # CTFd's own recalculation after four solves
        w = SimpleNamespace(app=app, admin=admin, clients=clients, cid=cid)
        triggers.ON.update(flush=True, bulk=True, check=True)
        cache.delete(triggers.CHECK_KEY)
        yield w
        triggers.ON.update(flush=True, bulk=True, check=True)
    destroy_ctfd(app)


def counted_by_hand(cid):
    """The solves of studios that are neither banned nor hidden, counted without any of our code."""
    db.session.rollback()
    total = 0
    for solve in Solves.query.filter_by(challenge_id=cid).all():
        team = Teams.query.filter_by(id=solve.team_id).first()
        if team is not None and not team.banned and not team.hidden:
            total += 1
    return total


def wanted(cid):
    c = values.DynamicChallenge.query.filter_by(id=cid).first()
    return values.formula(c, counted_by_hand(cid))


def team_id(name):
    return Users.query.filter_by(name=name).first().team_id


def heals():
    return Audit.query.filter_by(action="scoring.heal").count()


def solve_id(name, cid):
    return Solves.query.filter_by(challenge_id=cid, team_id=team_id(name)).first().id


# -- each action, through CTFd's own route -----------------------------------------------------------------------------------------

ACTIONS = {
    "ban": lambda w: w.admin.patch(f"/api/v1/teams/{team_id('bob')}", json={"banned": True}),
    "hide": lambda w: w.admin.patch(f"/api/v1/teams/{team_id('bob')}", json={"hidden": True}),
    "delete a studio": lambda w: w.admin.delete(f"/api/v1/teams/{team_id('bob')}", json={}),
    "delete a solve": lambda w: w.admin.delete(f"/api/v1/submissions/{solve_id('bob', w.cid)}", json={}),
    "mark incorrect": lambda w: w.admin.patch(f"/api/v1/submissions/{solve_id('bob', w.cid)}", json={"type": "incorrect"}),
    "delete a user": lambda w: w.admin.delete(f"/api/v1/users/{Users.query.filter_by(name='bob').first().id}", json={}),
}


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_the_value_is_right_when_the_request_that_changed_who_counts_ends(world, action):
    before = stored_value(world.cid)
    assert before == 488
    r = ACTIONS[action](world)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert counted_by_hand(world.cid) == 3
    assert stored_value(world.cid) == wanted(world.cid) == 495, "three studios count now: 495, not the stale 488"
    assert values.drifted() == []
    assert heals() == 0, "the request put it right; the minute check had nothing to do"


def test_unbanning_and_unhiding_put_the_solve_back_into_the_count(world):
    tid = team_id("bob")
    assert world.admin.patch(f"/api/v1/teams/{tid}", json={"banned": True}).status_code == 200
    assert stored_value(world.cid) == 495
    assert world.admin.patch(f"/api/v1/teams/{tid}", json={"banned": False}).status_code == 200
    assert stored_value(world.cid) == 488
    assert world.admin.patch(f"/api/v1/teams/{tid}", json={"hidden": True}).status_code == 200
    assert stored_value(world.cid) == 495
    assert world.admin.patch(f"/api/v1/teams/{tid}", json={"hidden": False}).status_code == 200
    assert stored_value(world.cid) == 488
    assert heals() == 0


def test_changing_a_studio_that_solved_nothing_changes_no_value_and_writes_nothing(world):
    from scoring_world import studio

    quiet_id = studio("quiet").id
    other_id = dynamic("other", initial=250, minimum=100, decay=10).id
    before = {c.id: c.value for c in Challenges.query.all()}
    assert world.admin.patch(f"/api/v1/teams/{quiet_id}", json={"banned": True}).status_code == 200
    db.session.rollback()
    assert {c.id: c.value for c in Challenges.query.all()} == before
    assert stored_value(other_id) == 250
    assert heals() == 0


def test_a_failing_recalculation_never_breaks_the_request_that_asked_for_it(world, caplog):
    with mock.patch.object(values, "recalculate", side_effect=RuntimeError("boom")), caplog.at_level(logging.WARNING, logger="l3mon"):
        r = world.admin.patch(f"/api/v1/teams/{team_id('bob')}", json={"banned": True})
        assert r.status_code == 200
        r = world.admin.delete(f"/api/v1/users/{Users.query.filter_by(name='carol').first().id}", json={})
        assert r.status_code == 200
    db.session.rollback()
    assert Teams.query.filter_by(id=team_id("bob")).first().banned is True, "the ban itself went through"
    assert sum("could not be recalculated" in rec.message for rec in caplog.records) >= 2


def test_a_recalculation_is_not_recalculated_by_itself(world):
    calls = []
    real = values.recalculate
    cache.set(triggers.CHECK_KEY, 1, timeout=60)  # this minute's check is taken, so only the flush hook can call it
    with mock.patch.object(values, "recalculate", side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)):
        assert world.admin.patch(f"/api/v1/teams/{team_id('bob')}", json={"banned": True}).status_code == 200
    assert len(calls) == 1


# -- the safety net --------------------------------------------------------------------------------------------------------------

def wrong(world, value=3):
    db.session.query(Challenges).filter_by(id=world.cid).update({"value": value})
    db.session.commit()


def a_page(world):
    return world.clients["alice"].get("/api/v1/challenges")


def test_the_minute_check_puts_right_what_no_hook_saw_and_says_so(world, caplog):
    wrong(world)
    cache.delete(triggers.CHECK_KEY)
    with caplog.at_level(logging.WARNING, logger="l3mon"):
        assert a_page(world).status_code == 200
    assert stored_value(world.cid) == 488
    assert heals() == 1
    assert any("stored value" in r.message for r in caplog.records)


def test_the_minute_check_runs_once_a_minute_not_on_every_request(world):
    cache.delete(triggers.CHECK_KEY)
    a_page(world)
    wrong(world)
    a_page(world)
    assert stored_value(world.cid) == 3, "the second request inside the minute does not look"
    cache.delete(triggers.CHECK_KEY)  # a minute later
    a_page(world)
    assert stored_value(world.cid) == 488


def test_only_the_worker_that_wins_the_cache_add_runs_the_check(world):
    wrong(world)
    cache.set(triggers.CHECK_KEY, 1, timeout=60)  # another worker took this minute
    a_page(world)
    assert stored_value(world.cid) == 3 and heals() == 0


def test_the_minute_check_skips_static_files_and_does_not_use_up_the_minute(world):
    cache.delete(triggers.CHECK_KEY)
    wrong(world)
    world.clients["alice"].get("/themes/core/static/css/main.dev.css")
    assert stored_value(world.cid) == 3
    assert cache.get(triggers.CHECK_KEY) is None
    a_page(world)
    assert stored_value(world.cid) == 488


def test_a_broken_check_never_breaks_a_page(world, caplog):
    cache.delete(triggers.CHECK_KEY)
    with mock.patch.object(values, "heal", side_effect=RuntimeError("boom")), caplog.at_level(logging.WARNING, logger="l3mon"):
        assert a_page(world).status_code == 200
    assert any("check" in r.message for r in caplog.records)


# -- the switch-off proofs: with a hook off, its action is missed ----------------------------------------------------------------

@pytest.mark.parametrize("hook, action", [("flush", "ban"), ("flush", "hide"), ("flush", "delete a studio"), ("flush", "delete a solve"),
                                          ("flush", "mark incorrect"), ("bulk", "delete a user")])
def test_with_its_hook_off_an_action_leaves_the_value_stale(world, hook, action):
    triggers.ON[hook] = False
    assert ACTIONS[action](world).status_code == 200
    assert stored_value(world.cid) == 488, f"proof: without the {hook} hook, '{action}' is not noticed"


def test_with_the_check_off_nothing_heals(world):
    triggers.ON["check"] = False
    wrong(world)
    cache.delete(triggers.CHECK_KEY)
    a_page(world)
    assert stored_value(world.cid) == 3


def test_a_standard_challenge_with_a_scoring_function_follows_a_ban_too(world):
    """CTFd's editor offers Static / Linear / Logarithmic on every challenge; the independent audit found that the plugin only watched
    the dynamic type. Made the way the crew would make it, through the admin API."""
    r = world.admin.post("/api/v1/challenges", json={
        "name": "std", "category": "c", "description": "d", "value": 500, "initial": 500, "minimum": 200, "decay": 15,
        "function": "logarithmic", "type": "standard", "state": "visible"})
    assert r.status_code == 200, r.get_data(as_text=True)
    cid = r.get_json()["data"]["id"]
    from tests.helpers import gen_flag

    gen_flag(db, cid, content="F-STD")
    for name in NAMES:
        assert attempt(world.clients[name], cid, "F-STD") == "correct"
    assert stored_value(cid) == 488, "CTFd's own recalculation after four solves"
    assert world.admin.patch(f"/api/v1/teams/{team_id('bob')}", json={"banned": True}).status_code == 200
    assert stored_value(cid) == 495
    assert heals() == 0


# -- other crew actions that change who holds a solve (found by the independent audit) -------------------------------------------

def a_second_challenge(world, solved_by=("alice", "bob", "carol")):
    """dyn2, solved through the real route by the named studios; the others have not solved it."""
    r = world.admin.post("/api/v1/challenges", json={
        "name": "dyn2", "category": "c", "description": "d", "value": 500, "initial": 500, "minimum": 200, "decay": 15,
        "function": "logarithmic", "type": "dynamic", "state": "visible"})
    assert r.status_code == 200, r.get_data(as_text=True)
    cid = r.get_json()["data"]["id"]
    from tests.helpers import gen_flag

    gen_flag(db, cid, content="F-TWO")
    for name in solved_by:
        assert attempt(world.clients[name], cid, "F-TWO") == "correct"
    return cid


def test_marking_a_wrong_attempt_correct_puts_the_value_right_in_the_same_request(world):
    cid = a_second_challenge(world)
    assert stored_value(cid) == 495
    assert attempt(world.clients["dave"], cid, "wrong") == "incorrect"
    from CTFd.models import Fails

    wrong_id = Fails.query.filter_by(challenge_id=cid).first().id
    assert world.admin.patch(f"/api/v1/submissions/{wrong_id}", json={"type": "correct"}).status_code == 200
    assert Solves.query.filter_by(challenge_id=cid).count() == 4
    assert stored_value(cid) == 488, "four studios hold a solve now"
    assert heals() == 0


def test_an_admin_created_correct_submission_puts_the_value_right_in_the_same_request(world):
    cid = a_second_challenge(world)
    dave = Users.query.filter_by(name="dave").first()
    r = world.admin.post("/api/v1/submissions", json={"user_id": dave.id, "team_id": dave.team_id, "challenge_id": cid, "provided": "by hand", "type": "correct"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert stored_value(cid) == 488 and heals() == 0


def test_removing_a_member_who_solved_puts_the_value_right_in_the_same_request(world):
    """CTFd deletes the member's submissions in bulk (no flush); the solves go with them."""
    from tests.helpers import register_user

    cid = a_second_challenge(world, solved_by=("alice", "carol", "dave"))
    register_user(world.app, name="erin", email="erin@example.com")
    erin = Users.query.filter_by(name="erin").first().id
    bob_team = team_id("bob")
    assert world.admin.post(f"/api/v1/teams/{bob_team}/members", json={"user_id": erin}).status_code == 200
    erin_client = login_as_user(world.app, "erin")
    assert attempt(erin_client, cid, "F-TWO") == "correct", "erin solves it for bob's studio"
    assert stored_value(cid) == 488
    r = world.admin.delete(f"/api/v1/teams/{bob_team}/members", json={"user_id": erin})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert Solves.query.filter_by(challenge_id=cid).count() == 3
    assert stored_value(cid) == 495
    assert heals() == 0


@pytest.mark.parametrize("hook, how", [("flush", "mark correct"), ("flush", "create correct"), ("bulk", "remove member")])
def test_with_its_hook_off_these_actions_leave_the_value_stale_too(world, hook, how):
    from tests.helpers import register_user

    triggers.ON[hook] = False
    try:
        if how == "remove member":
            cid = a_second_challenge(world, solved_by=("alice", "carol", "dave"))
            register_user(world.app, name="erin", email="erin@example.com")
            erin = Users.query.filter_by(name="erin").first().id
            assert world.admin.post(f"/api/v1/teams/{team_id('bob')}/members", json={"user_id": erin}).status_code == 200
            assert attempt(login_as_user(world.app, "erin"), cid, "F-TWO") == "correct"
            assert world.admin.delete(f"/api/v1/teams/{team_id('bob')}/members", json={"user_id": erin}).status_code == 200
            assert stored_value(cid) == 488, "proof: without the bulk hook the removal is not noticed"
        else:
            cid = a_second_challenge(world)
            dave = Users.query.filter_by(name="dave").first()
            if how == "create correct":
                body = {"user_id": dave.id, "team_id": dave.team_id, "challenge_id": cid, "provided": "x", "type": "correct"}
                assert world.admin.post("/api/v1/submissions", json=body).status_code == 200
            else:
                assert attempt(world.clients["dave"], cid, "wrong") == "incorrect"
                from CTFd.models import Fails

                wrong_id = Fails.query.filter_by(challenge_id=cid).first().id
                assert world.admin.patch(f"/api/v1/submissions/{wrong_id}", json={"type": "correct"}).status_code == 200
            assert stored_value(cid) == 495, "proof: without the flush hook the new solve is not noticed"
    finally:
        triggers.ON.update(flush=True, bulk=True, check=True)
