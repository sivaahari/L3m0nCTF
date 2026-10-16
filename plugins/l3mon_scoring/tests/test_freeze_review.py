"""What the independent review of part 3.6 found, each as a test that failed before the fix (2026-10-15).

1. A freeze set to a time that has already passed (CTFd's admin page accepts any time) held whatever value was stored at that moment, which was
   made from solves the freeze hides: the public standings showed that a third studio had solved. The values are now worked out AS OF the
   freeze time when the freeze is set or moved (the count of studios that count and solved BEFORE it), in the request that does it.
2. A challenge created during the freeze with no `value` stayed at NULL until the lift.
3. A lift made outside a request (a script, the shell) was only found by the minute check.
4. CTFd's challenge ratings are public totals that only solvers can change: a solve after the freeze followed by a rating told everybody that
   somebody had solved. Nobody rates while the scoreboard is frozen.
5. The minute check took the challenge rows' locks and kept them to the end of the request when it had nothing to fix.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import datetime
import time

import pytest

from CTFd.cache import cache, clear_standings
from CTFd.models import Challenges, Ratings, db
from CTFd.plugins.l3mon_core.counting import counted_solve_counts
from CTFd.plugins.l3mon_scoring import freeze, triggers, values
from CTFd.utils import set_config
from CTFd.utils.scores import get_standings
from scoring_world import attempt, decaying, dynamic, make_app, solve, stored_value, studio, team_client
from tests.helpers import destroy_ctfd, login_as_user


def now():
    return int(time.time())


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        triggers._lift["frozen"] = None
        yield app
    destroy_ctfd(app)


def public():
    clear_standings()
    return [(r.name, int(r.score)) for r in get_standings(admin=False)]


def solved(name, challenge_id, seconds_ago):
    """A studio's solve made `seconds_ago` seconds ago (CTFd dates a solve with the real clock)."""
    row = solve(studio(name, 1), Challenges.query.get(challenge_id))
    row.date = datetime.datetime.utcnow() - datetime.timedelta(seconds=seconds_ago)
    db.session.commit()
    clear_standings()
    return row


def formula(challenge_id, count):
    return values.formula(values.decaying_rows([challenge_id])[0], count)


def three_solves(app):
    """alpha and bravo solved two hours ago, charlie ten minutes ago; the value is the three-studio one, as CTFd would have left it."""
    dyn_id = dynamic("dyn", flag="D").id
    solved("alpha", dyn_id, 7200)
    solved("bravo", dyn_id, 7100)
    solved("charlie", dyn_id, 600)
    values.recalculate()
    db.session.commit()
    clear_standings()
    assert stored_value(dyn_id) == formula(dyn_id, 3) != formula(dyn_id, 2)
    return dyn_id


# ---- 1. a freeze set to a time already past ----------------------------------------------------------------------------------------------------------

def test_the_counting_can_stop_at_a_time(app):
    dyn_id = three_solves(app)
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)
    assert counted_solve_counts([dyn_id]) == {dyn_id: 3}
    assert counted_solve_counts([dyn_id], before=cutoff) == {dyn_id: 2}, "charlie's solve is after it"
    assert counted_solve_counts([dyn_id], before=datetime.datetime.utcnow() - datetime.timedelta(hours=3)) == {dyn_id: 0}


def test_a_freeze_set_to_a_time_already_past_values_the_challenge_as_of_that_time_in_the_same_request(app):
    dyn_id = three_solves(app)
    admin = login_as_user(app, "admin")
    r = admin.patch("/api/v1/configs", json={"freeze": now() - 1800})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert freeze.held()
    assert stored_value(dyn_id) == formula(dyn_id, 2), "the value the public standings are made from is the one before charlie's solve"
    ranked = public()
    assert sorted(name for name, _ in ranked) == ["alpha", "bravo"], "charlie's solve is not on the public list"
    assert {score for _, score in ranked} == {formula(dyn_id, 2)}, "and nothing in the scores says that a third studio solved"


def test_the_same_with_a_value_made_by_a_standard_challenge_that_carries_a_scoring_function(app):
    chal_id = decaying("std", flag="D").id
    solved("alpha", chal_id, 7200)
    solved("bravo", chal_id, 7100)
    solved("charlie", chal_id, 600)
    values.recalculate()
    db.session.commit()
    assert stored_value(chal_id) == formula(chal_id, 3)
    login_as_user(app, "admin").patch("/api/v1/configs", json={"freeze": now() - 1800})
    assert stored_value(chal_id) == formula(chal_id, 2)


def test_moving_a_past_freeze_later_values_it_as_of_the_new_time_and_earlier_takes_it_back(app):
    dyn_id = three_solves(app)
    admin = login_as_user(app, "admin")
    admin.patch("/api/v1/configs", json={"freeze": now() - 1800})
    assert stored_value(dyn_id) == formula(dyn_id, 2)
    admin.patch("/api/v1/configs", json={"freeze": now() - 60})
    assert freeze.held() and stored_value(dyn_id) == formula(dyn_id, 3), "charlie's solve is before the new freeze: it counts, and the public list shows it"
    assert sorted(name for name, _ in public()) == ["alpha", "bravo", "charlie"]
    admin.patch("/api/v1/configs", json={"freeze": now() - 7150})
    assert stored_value(dyn_id) == formula(dyn_id, 1), "only alpha's solve is before this one"
    assert [name for name, _ in public()] == ["alpha"]


def test_a_freeze_that_has_not_begun_changes_nothing_and_a_lift_puts_the_whole_count_back(app):
    dyn_id = three_solves(app)
    admin = login_as_user(app, "admin")
    admin.patch("/api/v1/configs", json={"freeze": now() + 3600})
    assert not freeze.held() and stored_value(dyn_id) == formula(dyn_id, 3)
    admin.patch("/api/v1/configs", json={"freeze": now() - 1800})
    assert stored_value(dyn_id) == formula(dyn_id, 2)
    admin.patch("/api/v1/configs", json={"freeze": None})
    assert stored_value(dyn_id) == formula(dyn_id, 3), "lifting counts every solve again"


def test_setting_the_freeze_needs_the_trigger(app):
    dyn_id = three_solves(app)
    admin = login_as_user(app, "admin")
    triggers.ON["freeze"] = False
    try:
        admin.patch("/api/v1/configs", json={"freeze": now() - 1800})
        assert stored_value(dyn_id) == formula(dyn_id, 3), "without the trigger the stale value stays: the three-studio value is held"
    finally:
        triggers.ON["freeze"] = True


def test_a_ban_during_the_freeze_still_leaves_the_held_values_alone(app):
    dyn_id = three_solves(app)
    login_as_user(app, "admin").patch("/api/v1/configs", json={"freeze": now() - 1800})
    held_at = stored_value(dyn_id)
    from CTFd.models import Teams

    Teams.query.filter_by(name="alpha").one().banned = True
    db.session.commit()
    assert values.recalculate() == [] and stored_value(dyn_id) == held_at


# ---- 2. a challenge made during the freeze ------------------------------------------------------------------------------------------------------------

def test_a_standard_challenge_with_a_scoring_function_made_during_the_freeze_without_a_value_starts_at_its_start_value(app):
    admin = login_as_user(app, "admin")
    admin.patch("/api/v1/configs", json={"freeze": now() - 60})
    r = admin.post("/api/v1/challenges", json={
        "name": "made during the freeze", "category": "c", "description": "d", "type": "standard", "function": "linear",
        "initial": 500, "minimum": 100, "decay": 10, "state": "visible",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["data"]["value"] == 500, r.get_json()
    chal_id = r.get_json()["data"]["id"]
    assert stored_value(chal_id) == 500


def test_a_challenge_made_during_a_past_freeze_starts_at_the_value_for_the_solves_before_it(app):
    admin = login_as_user(app, "admin")
    admin.patch("/api/v1/configs", json={"freeze": now() - 60})
    r = admin.post("/api/v1/challenges", json={
        "name": "made during the freeze", "category": "c", "description": "d", "type": "dynamic", "initial": 300, "minimum": 100, "decay": 10,
        "function": "logarithmic", "state": "visible",
    })
    assert r.status_code == 200 and r.get_json()["data"]["value"] == 300


# ---- 3. a lift made outside a request ---------------------------------------------------------------------------------------------------------------

def test_a_lift_made_outside_a_request_is_noticed_by_the_next_request_without_waiting_for_the_minute_check(app):
    dyn_id = three_solves(app)
    client = team_client(app, "watcher", "watch-studio")
    login_as_user(app, "admin").patch("/api/v1/configs", json={"freeze": now() - 1800})
    assert stored_value(dyn_id) == formula(dyn_id, 2)
    cache.set(triggers.CHECK_KEY, 1, timeout=60)  # the minute check has just run: it is not what is tested here
    assert client.get("/api/v1/l3mon/ticks").status_code in (200, 401, 403)  # a request while frozen: this process now knows it is frozen
    set_config("freeze", None)  # a script, or the shell: no request, no flush hook
    assert stored_value(dyn_id) == formula(dyn_id, 2), "nothing has noticed yet"
    client.get("/api/v1/l3mon/ticks")
    assert stored_value(dyn_id) == formula(dyn_id, 3), "the next request found that the freeze was gone and put every value right"


def test_a_process_that_never_saw_the_freeze_has_nothing_to_lift(app):
    dyn_id = three_solves(app)
    client = team_client(app, "watcher", "watch-studio")
    cache.set(triggers.CHECK_KEY, 1, timeout=60)
    client.get("/api/v1/l3mon/ticks")
    assert triggers._lift["frozen"] is False
    assert stored_value(dyn_id) == formula(dyn_id, 3)


# ---- 4. ratings ---------------------------------------------------------------------------------------------------------------------------------------

def rate(client, challenge_id, value=1):
    return client.put(f"/api/v1/challenges/{challenge_id}/ratings", json={"value": value})


def test_nobody_rates_a_challenge_while_the_scoreboard_is_frozen_and_everyone_can_before_and_after(app):
    dyn_id = dynamic("dyn", flag="D").id
    early = team_client(app, "early", "early-studio")
    assert attempt(early, dyn_id, "D") == "correct"
    assert rate(early, dyn_id).status_code == 200, "before the freeze a solver rates as always"
    login_as_user(app, "admin").patch("/api/v1/configs", json={"freeze": now() - 60})
    late = team_client(app, "late", "late-studio")
    assert attempt(late, dyn_id, "D") == "correct"
    r = rate(late, dyn_id)
    assert r.status_code == 403 and r.get_json()["success"] is False, r.get_data(as_text=True)
    assert "frozen" in str(r.get_json()["errors"]).lower()
    assert rate(early, dyn_id, -1).status_code == 403, "the first rating cannot be changed either"
    assert Ratings.query.filter_by(challenge_id=dyn_id).count() == 1
    totals = early.get(f"/api/v1/challenges/{dyn_id}").get_json()["data"]["ratings"]
    assert totals["count"] == 1 and totals["up"] == 1, "the totals did not move: nothing says that somebody solved after the freeze"
    login_as_user(app, "admin").patch("/api/v1/configs", json={"freeze": None})
    assert rate(late, dyn_id).status_code == 200, "after the lift the late solver rates"


def test_the_crew_may_still_rate_while_frozen_and_reading_ratings_is_not_closed(app):
    dyn_id = dynamic("dyn", flag="D").id
    admin = login_as_user(app, "admin")
    admin.patch("/api/v1/configs", json={"freeze": now() - 60})
    assert rate(admin, dyn_id).status_code == 200
    assert admin.get(f"/api/v1/challenges/{dyn_id}/ratings").status_code == 200
    # a player cannot read this route at all (CTFd answers a non-admin with a redirect), so the door's exemption for GET changes nothing for one


def test_the_door_for_ratings_can_be_switched_off_for_a_test_and_nothing_else_does(app):
    dyn_id = dynamic("dyn", flag="D").id
    login_as_user(app, "admin").patch("/api/v1/configs", json={"freeze": now() - 60})
    late = team_client(app, "late", "late-studio")
    assert attempt(late, dyn_id, "D") == "correct"
    freeze.ON["ratings"] = False
    try:
        assert rate(late, dyn_id).status_code == 200
    finally:
        freeze.ON["ratings"] = True
    assert rate(late, dyn_id).status_code == 403


# ---- 5. the minute check lets go of the locks --------------------------------------------------------------------------------------------------------

def test_the_minute_check_ends_its_transaction_when_it_has_nothing_to_fix_so_no_lock_outlives_it(app):
    dyn_id = dynamic("dyn", flag="D").id
    solved("alpha", dyn_id, 7200)
    values.recalculate()
    db.session.commit()
    assert values.heal() == []
    assert not db.session().in_transaction(), "a locking read that found nothing to change left its row locks held for the rest of the request"
