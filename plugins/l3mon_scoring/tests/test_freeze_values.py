"""The values stand still while the scoreboard is frozen (SP3 part 3.6).

CTFd's `freeze` leaves the solves after it out of the public lists, but values the pre-freeze solves at the CURRENT value of their challenge
(test_freeze_facts.py), so a value that falls after the freeze moves a frozen score and tells the public that others have solved. While the
scoreboard is frozen no stored value is written: CTFd's own two valuation functions, our recalculation, the minute check and Revoke and
Restore all leave the values where they are, so every answer (the board, the panel, the Guide, the scoreboard, the feeds, CTFd's own
pages) is made from the same still numbers. Lifting the freeze (unsetting it or moving it to a later time) puts every value right in the same request.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import datetime
import time

import pytest

from CTFd.cache import clear_standings
from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_scoring import freeze, values
from CTFd.utils import set_config
from CTFd.utils.scores import get_standings
from scoring_world import attempt, decaying, dynamic, fixed, make_app, score_of, solve, stored_value, studio, team_client
from tests.helpers import destroy_ctfd, login_as_user


def now():
    return int(time.time())


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


def public():
    clear_standings()
    return [(r.name, int(r.score)) for r in get_standings(admin=False)]


def solved_before_the_freeze(team, challenge_id):
    """A solve made two hours ago, valued as CTFd would have valued it then."""
    row = solve(team, Challenges.query.get(challenge_id))
    row.date = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
    db.session.commit()
    values.recalculate()
    db.session.commit()
    clear_standings()
    return row


def begin_the_freeze():
    set_config("freeze", now() - 60)
    clear_standings()


# ---- when it holds ---------------------------------------------------------------------------------------------------------------------

def test_the_values_are_held_from_the_freeze_time_on_and_only_while_a_freeze_is_set(app):
    assert not freeze.held()
    set_config("freeze", now() + 3600)
    assert not freeze.held(), "a freeze that has not begun holds nothing"
    set_config("freeze", now() - 5)
    assert freeze.held()
    set_config("freeze", None)
    assert not freeze.held()


def test_the_hold_can_be_switched_off_for_a_test_and_nothing_else_does(app):
    set_config("freeze", now() - 5)
    freeze.ON["hold"] = False
    try:
        assert not freeze.held()
    finally:
        freeze.ON["hold"] = True
    assert freeze.held()


# ---- CTFd's own valuation after a player's flag ------------------------------------------------------------------------------------------------

def test_a_flag_sent_before_the_freeze_values_the_challenge_as_always(app):
    dyn_id = dynamic("dyn", flag="D").id
    clients = [team_client(app, f"p{n}", f"team{n}") for n in range(3)]
    for client in clients:
        assert attempt(client, dyn_id, "D") == "correct"
    assert stored_value(dyn_id) == values.formula(values.decaying_rows([dyn_id])[0], 3)


@pytest.mark.parametrize("kind", ["dynamic", "standard"])
def test_a_flag_sent_while_frozen_leaves_the_value_where_it_was(app, kind):
    """A dynamic challenge and a standard one that carries a scoring function are both valued by CTFd after every solve."""
    make = dynamic if kind == "dynamic" else decaying
    challenge_id = make("chal", flag="F").id
    early = [studio("alpha", 1), studio("bravo", 1)]
    for team in early:
        solved_before_the_freeze(team, challenge_id)
    held_at = stored_value(challenge_id)
    assert held_at == values.formula(values.decaying_rows([challenge_id])[0], 2)
    begin_the_freeze()
    clients = [team_client(app, f"late{n}", f"late-team{n}") for n in range(3)]
    for client in clients:
        assert attempt(client, challenge_id, "F") == "correct"
    assert stored_value(challenge_id) == held_at, "five studios hold a solve now and the value is still the two-studio value"


def test_the_frozen_public_standings_do_not_move_when_others_solve_after_the_freeze(app):
    """The leak closed here: with CTFd alone this list falls as the late solves arrive (test_freeze_facts.py)."""
    dyn_id = dynamic("dyn", flag="D").id
    solved_before_the_freeze(studio("alpha", 1), dyn_id)
    begin_the_freeze()
    before = public()
    assert before == [("alpha", 500)]
    for number in range(3):
        assert attempt(team_client(app, f"late{number}", f"late-team{number}"), dyn_id, "D") == "correct"
    assert public() == before


def test_a_studios_own_score_is_live_and_made_from_the_held_values(app):
    dyn_id = dynamic("dyn", flag="D").id
    solved_before_the_freeze(studio("alpha", 1), dyn_id)
    begin_the_freeze()
    client = team_client(app, "late", "late-team")
    assert attempt(client, dyn_id, "D") == "correct"
    from scoring_world import team_of

    assert score_of(team_of("late").id) == 500, "its own solve counts at once, at the value that stands still"
    assert public() == [("alpha", 500)]


# ---- our own recalculation ---------------------------------------------------------------------------------------------------------------------

def test_recalculate_the_minute_check_and_drift_do_nothing_while_frozen_and_work_again_after(app):
    dyn_id = dynamic("dyn", flag="D").id
    solved_before_the_freeze(studio("alpha", 1), dyn_id)
    solved_before_the_freeze(studio("bravo", 1), dyn_id)
    db.session.query(Challenges).filter_by(id=dyn_id).update({"value": 123})  # a stored value that is not its formula's
    db.session.commit()
    begin_the_freeze()
    assert values.recalculate() == [] and values.heal() == [] and values.drifted() == []
    assert stored_value(dyn_id) == 123, "held on purpose: nothing is written while the scoreboard is frozen"
    set_config("freeze", None)
    assert [row[0] for row in values.drifted()] == [dyn_id]
    values.recalculate()
    db.session.commit()
    assert stored_value(dyn_id) == 499


def test_a_challenge_edited_by_the_crew_while_frozen_keeps_its_value_until_the_lift(app):
    admin = login_as_user(app, "admin")
    dyn_id = dynamic("dyn", initial=500, minimum=200, decay=15, flag="D").id
    begin_the_freeze()
    r = admin.patch(f"/api/v1/challenges/{dyn_id}", json={"initial": 400, "minimum": 100})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert stored_value(dyn_id) == 500, "CTFd's update would have valued it at once"
    set_config("freeze", None)
    values.recalculate()
    db.session.commit()
    assert stored_value(dyn_id) == 400


# ---- lifting the freeze -------------------------------------------------------------------------------------------------------------------------

def stale_world(app):
    """Two studios solved before the freeze (the value is the two-studio one), three more after it (CTFd valued nothing, as held)."""
    dyn_id = dynamic("dyn", flag="D").id
    solved_before_the_freeze(studio("alpha", 1), dyn_id)
    solved_before_the_freeze(studio("bravo", 1), dyn_id)
    begin_the_freeze()
    for number in range(3):
        assert attempt(team_client(app, f"late{number}", f"late-team{number}"), dyn_id, "D") == "correct"
    assert stored_value(dyn_id) == 499
    return dyn_id


def test_lifting_the_freeze_through_the_crews_own_call_puts_every_value_right_in_the_same_request(app):
    dyn_id = stale_world(app)
    admin = login_as_user(app, "admin")
    r = admin.patch("/api/v1/configs", json={"freeze": None})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert stored_value(dyn_id) == values.formula(values.decaying_rows([dyn_id])[0], 5), "five studios hold a solve: the value is its true one at once"
    ranked = public()
    assert len(ranked) == 5 and [name for name, _ in ranked][:2] == ["alpha", "bravo"], "the public list shows the late solvers now"


def test_moving_the_freeze_to_a_later_time_lifts_it_and_a_time_already_past_changes_nothing(app):
    dyn_id = stale_world(app)
    admin = login_as_user(app, "admin")
    r = admin.patch("/api/v1/configs", json={"freeze": now() - 30})
    assert r.status_code == 200 and freeze.held() and stored_value(dyn_id) == 499, "still frozen: nothing moves"
    r = admin.patch("/api/v1/configs", json={"freeze": now() + 3600})
    assert r.status_code == 200 and not freeze.held()
    assert stored_value(dyn_id) == values.formula(values.decaying_rows([dyn_id])[0], 5)


def test_lifting_the_freeze_needs_the_trigger_and_the_minute_check_is_the_last_resort(app):
    from CTFd.plugins.l3mon_scoring import triggers

    dyn_id = stale_world(app)
    admin = login_as_user(app, "admin")
    triggers.ON["freeze"] = False
    try:
        assert admin.patch("/api/v1/configs", json={"freeze": None}).status_code == 200
        assert stored_value(dyn_id) == 499, "without the trigger nothing recalculates until the next check"
    finally:
        triggers.ON["freeze"] = True
    assert values.heal(), "the minute check fixes it and says so"
    assert stored_value(dyn_id) == values.formula(values.decaying_rows([dyn_id])[0], 5)


# ---- the crew's page and API ----------------------------------------------------------------------------------------------------------------

def test_the_crews_recalculate_is_refused_while_frozen_and_the_view_says_why(app):
    admin = login_as_user(app, "admin")
    dyn_id = dynamic("dyn", flag="D").id
    solved_before_the_freeze(studio("alpha", 1), dyn_id)
    assert admin.get("/api/v1/l3mon/admin/scoring").get_json()["data"]["frozen"] is False
    begin_the_freeze()
    view = admin.get("/api/v1/l3mon/admin/scoring").get_json()["data"]
    assert view["frozen"] is True and view["challenges"][0]["wanted"] == 500
    r = admin.post("/api/v1/l3mon/admin/scoring/recalculate", json={})
    body = r.get_json()
    assert r.status_code == 409 and "frozen" in body["errors"], body
    set_config("freeze", None)
    assert admin.post("/api/v1/l3mon/admin/scoring/recalculate", json={}).status_code == 200


# ---- Revoke and Restore during the freeze -----------------------------------------------------------------------------------------------------------

def test_revoke_and_restore_during_the_freeze_keep_every_value_still_and_the_lift_puts_them_right(app):
    admin = login_as_user(app, "admin")
    dyn_id = dynamic("dyn", flag="D").id
    for name in ("alpha", "bravo", "charlie"):
        solved_before_the_freeze(studio(name, 1), dyn_id)
    held_at = stored_value(dyn_id)
    begin_the_freeze()
    r = admin.post("/api/v1/l3mon/admin/scoring/revoke", json={"challenge_id": dyn_id, "reason": "the checker broke"})
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()["data"]
    assert data["value_before"] == held_at and data["value_after"] == held_at, "the value stands still for the crew's action as well"
    assert stored_value(dyn_id) == held_at
    r = admin.post("/api/v1/l3mon/admin/scoring/restore", json={"challenge_id": dyn_id})
    assert r.status_code == 200 and stored_value(dyn_id) == held_at
    set_config("freeze", None)
    values.recalculate()
    db.session.commit()
    assert stored_value(dyn_id) == values.formula(values.decaying_rows([dyn_id])[0], 3)


def test_a_fixed_challenge_is_never_touched_by_any_of_this(app):
    fix_id = fixed("fix", value=100, flag="F").id
    begin_the_freeze()
    assert attempt(team_client(app, "p", "tm"), fix_id, "F") == "correct"
    assert stored_value(fix_id) == 100


def test_installing_again_wraps_nothing_twice(app):
    """The tests create many apps in one process, and each loads the plugin: a second wrap would take the lock and commit twice."""
    from CTFd.plugins import challenges as standard
    from CTFd.plugins.dynamic_challenges import DynamicValueChallenge

    freeze.install()
    freeze.install()
    assert getattr(standard.calculate_value, freeze.MARK, False) and not getattr(standard.calculate_value.original, freeze.MARK, False)
    assert getattr(DynamicValueChallenge.calculate_value, freeze.MARK, False) and not getattr(DynamicValueChallenge.calculate_value.original, freeze.MARK, False)


def test_a_held_valuation_returns_the_challenge_so_that_creating_one_while_frozen_still_works(app):
    admin = login_as_user(app, "admin")
    begin_the_freeze()
    r = admin.post("/api/v1/challenges", json={
        "name": "made during the freeze", "category": "c", "description": "d", "value": 300, "initial": 300, "minimum": 100, "decay": 10,
        "function": "logarithmic", "type": "dynamic", "state": "hidden",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["data"]["value"] == 300
