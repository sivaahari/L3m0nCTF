"""What a programme is worth: our formula against CTFd's own, and the recalculation.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import logging
from types import SimpleNamespace
from unittest import mock

import pytest

from CTFd.models import Challenges, db
from CTFd.plugins.dynamic_challenges import decay as ctfd_decay
from CTFd.plugins.l3mon_core.models import Audit
from CTFd.plugins.l3mon_scoring import values
from scoring_world import decaying, dynamic, fixed, make_app, solve, stored_value, studio
from tests.helpers import destroy_ctfd


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


def shaped(initial, minimum, decay, function):
    return SimpleNamespace(initial=initial, minimum=minimum, decay=decay, function=function)


@pytest.mark.parametrize("function", ["linear", "logarithmic"])
@pytest.mark.parametrize("initial, minimum", [(100, 40), (250, 100), (400, 160), (500, 200), (500, 500), (300, 0)])
@pytest.mark.parametrize("decay", [0, 1, 15, 50])
def test_the_formula_is_ctfds_for_every_count(function, initial, minimum, decay):
    """The whole point of owning a formula is that it can never disagree with CTFd's: every count from 0 to 80, both curves."""
    for n in range(0, 81):
        with mock.patch.object(ctfd_decay, "get_solve_count", lambda challenge, n=n: n):
            theirs = ctfd_decay.DECAY_FUNCTIONS[function](shaped(initial, minimum, decay, function))
        ours = values.formula(shaped(initial, minimum, decay, function), n)
        assert ours == theirs, (function, initial, minimum, decay, n)


def test_an_unknown_curve_is_read_as_logarithmic_like_ctfd_and_a_missing_decay_never_raises():
    for n in range(0, 20):
        with mock.patch.object(ctfd_decay, "get_solve_count", lambda challenge, n=n: n):
            theirs = ctfd_decay.DECAY_FUNCTIONS.get("mystery", ctfd_decay.logarithmic)(shaped(500, 200, 15, "mystery"))
        assert values.formula(shaped(500, 200, 15, "mystery"), n) == theirs
    assert values.formula(shaped(500, 200, None, "logarithmic"), 3) == values.formula(shaped(500, 200, 1, "logarithmic"), 3)


def test_the_curve_the_event_uses(app):
    """Start 500, floor 200, 50 solves to reach it: 500 for the first, then falling, 200 for the 51st and every later one."""
    c = SimpleNamespace(initial=500, minimum=200, decay=50, function="logarithmic")
    got = [values.formula(c, n) for n in range(0, 53)]
    assert got[0] == 500 and got[1] == 500  # nobody yet, and the first solver, are both at the start value
    assert got[2] == 500 and got[3] == 500  # the first couple of extra solvers move it by less than one TRP, and values are whole numbers
    assert got[6] == 497  # 500 - 300 * (5/50)^2 = 497
    assert got[51] == 200 and got[52] == 200
    assert all(a >= b for a, b in zip(got, got[1:])), "it never rises as studios solve"


def test_recalculate_sets_every_dynamic_value_to_its_formula_and_only_those(app):
    dyn = dynamic("dyn", flag="F-DYN")
    other = dynamic("other", initial=250, minimum=100, decay=10)
    fix = fixed("fix", value=100)
    teams = [studio(f"team{i}") for i in range(4)]
    for i, team in enumerate(teams):
        solve(team, dyn, minutes=i)
    solve(teams[0], fix, minutes=9)
    db.session.query(Challenges).filter_by(id=dyn.id).update({"value": 1})  # something wrong, on purpose
    db.session.commit()

    changes = values.recalculate()

    assert changes == [(dyn.id, 1, 488)]  # only the wrong one changed (the other had no solves and was right)
    db.session.commit()
    assert stored_value(dyn.id) == 488
    assert stored_value(other.id) == 250
    assert stored_value(fix.id) == 100  # a fixed challenge is never touched
    assert values.recalculate() == []  # repeatable: nothing left to change


def test_recalculate_can_be_limited_to_some_challenges(app):
    a, b = dynamic("a"), dynamic("b")
    team = studio("t")
    solve(team, a)
    solve(team, b)
    db.session.query(Challenges).filter(Challenges.id.in_([a.id, b.id])).update({"value": 7}, synchronize_session=False)
    db.session.commit()
    assert values.recalculate([a.id]) == [(a.id, 7, 500)]
    db.session.commit()
    assert stored_value(a.id) == 500 and stored_value(b.id) == 7


def test_only_studios_that_count_move_a_value(app):
    dyn = dynamic("dyn")
    teams = [studio(f"team{i}") for i in range(4)]
    for i, team in enumerate(teams):
        solve(team, dyn, minutes=i)
    teams[1].banned = True
    teams[2].hidden = True
    db.session.commit()
    values.recalculate()
    db.session.commit()
    assert stored_value(dyn.id) == 499  # two studios count: 500 - ceil(300/225 * 1) -> 499 (the same as CTFd with two solves)


def test_drifted_reads_without_writing(app):
    dyn = dynamic("dyn")
    team = studio("t")
    solve(team, dyn)
    solve(studio("u"), dyn, minutes=1)
    db.session.query(Challenges).filter_by(id=dyn.id).update({"value": 3})
    db.session.commit()
    assert values.drifted() == [(dyn.id, 3, 499)]
    assert stored_value(dyn.id) == 3  # looking changed nothing
    values.recalculate()
    db.session.commit()
    assert values.drifted() == []


def test_heal_audits_and_warns_once_and_is_silent_when_nothing_was_wrong(app, caplog):
    dyn = dynamic("dyn")
    solve(studio("t"), dyn)
    db.session.query(Challenges).filter_by(id=dyn.id).update({"value": 9})
    db.session.commit()
    with caplog.at_level(logging.WARNING, logger="l3mon"):
        fixed_ones = values.heal()
    assert fixed_ones == [(dyn.id, 9, 500)]
    assert stored_value(dyn.id) == 500
    lines = Audit.query.filter_by(action="scoring.heal").all()
    assert len(lines) == 1 and lines[0].actor_name == "system" and "dyn" in lines[0].detail and "9" in lines[0].detail and "500" in lines[0].detail
    assert any("stored value" in r.message for r in caplog.records)
    caplog.clear()
    assert values.heal() == []
    assert Audit.query.filter_by(action="scoring.heal").count() == 1
    assert not caplog.records


def test_a_challenge_with_missing_numbers_is_skipped_not_fatal(app, caplog):
    broken = dynamic("broken")
    db.session.execute(db.text("update dynamic_challenge set dynamic_initial = NULL where id = :i"), {"i": broken.id})
    good = dynamic("good")
    db.session.query(Challenges).filter_by(id=good.id).update({"value": 2})
    db.session.commit()
    with caplog.at_level(logging.WARNING, logger="l3mon"):
        assert values.recalculate() == [(good.id, 2, 500)]
    assert any("broken" in r.message for r in caplog.records), "the skipped challenge is named in the log"


# ---- a standard challenge with a scoring function (found by the independent audit) ------------------------------------------------

@pytest.mark.parametrize("function, four", [("logarithmic", 488), ("linear", 455)])  # linear: 500 - 15 x (4 - 1)
def test_a_standard_challenge_that_carries_a_scoring_function_is_valued_like_a_dynamic_one(app, function, four):
    std = decaying("std", function=function)
    teams = [studio(f"team{i}") for i in range(4)]
    for i, team in enumerate(teams):
        solve(team, std, minutes=i)
    db.session.query(Challenges).filter_by(id=std.id).update({"value": 1})
    db.session.commit()
    assert values.drifted() == [(std.id, 1, four)], "the minute check sees it"
    assert values.recalculate() == [(std.id, 1, four)]
    db.session.commit()
    assert stored_value(std.id) == four
    assert values.recalculate() == []


def test_a_standard_challenge_with_the_static_function_or_none_is_left_alone(app):
    static = decaying("static", function="static")
    nothing = fixed("nothing", value=100)
    for team in (studio("a"), studio("b")):
        solve(team, static)
        solve(team, nothing)
    db.session.query(Challenges).filter(Challenges.id.in_([static.id, nothing.id])).update({"value": 7}, synchronize_session=False)
    db.session.commit()
    assert values.drifted() == [] and values.recalculate() == []
    assert stored_value(static.id) == 7 and stored_value(nothing.id) == 7


def test_the_two_kinds_are_valued_together_in_id_order_and_a_limited_call_touches_only_what_it_names(app):
    std, dyn = decaying("std"), dynamic("dyn")
    for team in (studio("a"), studio("b")):
        solve(team, std)
        solve(team, dyn)
    db.session.query(Challenges).filter(Challenges.id.in_([std.id, dyn.id])).update({"value": 7}, synchronize_session=False)
    db.session.commit()
    assert values.recalculate([dyn.id]) == [(dyn.id, 7, 499)]
    db.session.commit()
    assert stored_value(std.id) == 7
    assert values.recalculate() == [(std.id, 7, 499)]


def test_a_standard_challenge_with_a_function_but_no_start_value_is_skipped_not_fatal(app, caplog):
    broken = decaying("broken")
    db.session.query(Challenges).filter_by(id=broken.id).update({"initial": None})
    db.session.commit()
    with caplog.at_level(logging.WARNING, logger="l3mon"):
        assert values.recalculate() == []
    assert any("broken" in r.message for r in caplog.records)


@pytest.mark.parametrize("spelling", ["Linear", "LOGARITHMIC", "linear ", "Logarithmic "])
def test_a_function_name_that_only_a_case_insensitive_database_would_match_is_a_fixed_value(app, spelling):
    """CTFd compares the name exactly, so 'Linear' or 'linear ' is not a scoring function there. The database's collation ignores case
    and trailing spaces, so the query alone would value it by a curve (found by the independent audit on MariaDB)."""
    odd = decaying("odd", function="static")
    db.session.query(Challenges).filter_by(id=odd.id).update({"function": spelling, "value": 100})
    db.session.commit()
    solve(studio("a"), odd)
    assert values.decaying_rows() == []
    assert values.recalculate() == [] and stored_value(odd.id) == 100
