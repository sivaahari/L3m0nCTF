"""What the freeze (SP3 part 3.6) relies on in CTFd 3.8.8, kept as tests. If a CTFd upgrade changes one of these, the design needs a second look.

CTFd's `freeze` is a single time. From that time on, the public lists leave out every solve and award made at or after it. It does NOT keep
a dynamic challenge's value still: the public standings are made from the challenges' CURRENT stored values, so a value that falls after the
freeze moves a frozen score and tells the public that other studios solved. Part 3.6 therefore keeps the stored values still (the wrapper in
`plugins/l3mon_scoring/freeze.py`), and these tests show why it has to.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import calendar
import datetime
import time
from unittest import mock

import pytest

from CTFd.cache import clear_standings
from CTFd.models import Awards, Challenges, Teams, db
from CTFd.utils import set_config
from CTFd.utils.dates import ctf_ended
from CTFd.utils.scores import get_standings
from scoring_world import T0, attempt, decaying, dynamic, fixed, make_app, solve, studio, team_client
from tests.helpers import destroy_ctfd


def epoch(minutes):
    return calendar.timegm((T0 + datetime.timedelta(minutes=minutes)).timetuple())


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


def public():
    clear_standings()
    return [(r.name, int(r.score)) for r in get_standings(admin=False)]


def test_the_public_standings_leave_out_what_is_dated_at_or_after_the_freeze(app):
    dyn = fixed("fix", value=100)
    a, b = studio("alpha"), studio("bravo")
    solve(a, dyn, minutes=0)
    solve(b, dyn, minutes=60)
    set_config("freeze", epoch(30))
    assert public() == [("alpha", 100)], "bravo solved after the freeze: the public does not see it"
    db.session.add(Awards(user_id=a.members[0].id, team_id=a.id, name="Bonus +50 TRP", value=50, category="bonus", date=T0 + datetime.timedelta(minutes=45)))
    db.session.commit()
    assert public() == [("alpha", 100)], "an award after the freeze is left out as well"
    set_config("freeze", None)
    assert public() == [("alpha", 150), ("bravo", 100)]


def test_the_frozen_standings_are_made_from_the_current_stored_values_so_a_falling_value_moves_a_frozen_score(app):
    """The leak part 3.6 closes: nothing about the solves after the freeze is shown, yet the pre-freeze score changes when the value does."""
    dyn = dynamic("dyn", initial=500, minimum=200, decay=15)
    a = studio("alpha")
    solve(a, dyn, minutes=0)
    set_config("freeze", epoch(30))
    assert dyn.value == 500 and public() == [("alpha", 500)]
    db.session.query(Challenges).filter_by(id=dyn.id).update({"value": 450})  # what CTFd does after a solve made once the scoreboard is frozen
    db.session.commit()
    assert public() == [("alpha", 450)], "the public sees the score of a pre-freeze solve fall: others have solved since"


def test_the_owner_and_the_crew_see_everything_whatever_the_freeze(app):
    dyn = fixed("fix", value=100)
    a = studio("alpha")
    solve(a, dyn, minutes=60)
    set_config("freeze", epoch(30))
    assert Teams.query.get(a.id).get_score(admin=True) == 100, "a studio's own score is live (admin=True ignores the freeze)"
    assert [(r.name, int(r.score)) for r in get_standings(admin=True)] == [("alpha", 100)]
    assert Teams.query.get(a.id).get_score(admin=False) == 0, "the public view of the same score is frozen"


def test_the_public_solve_counts_stop_at_the_freeze(app):
    from CTFd.utils.challenges import get_solve_counts_for_challenges  # needs the application (it registers a hook when imported)

    dyn = fixed("fix", value=100)
    a, b = studio("alpha"), studio("bravo")
    solve(a, dyn, minutes=0)
    solve(b, dyn, minutes=60)
    set_config("freeze", epoch(30))
    assert get_solve_counts_for_challenges(admin=False).get(dyn.id) == 1
    assert get_solve_counts_for_challenges(admin=True).get(dyn.id) == 2


def test_the_freeze_stays_applied_after_the_end_until_somebody_unsets_it(app):
    dyn = fixed("fix", value=100)
    a, b = studio("alpha"), studio("bravo")
    now = int(time.time())
    first, second = solve(a, dyn), solve(b, dyn)
    first.date, second.date = datetime.datetime.utcfromtimestamp(now - 7200), datetime.datetime.utcfromtimestamp(now - 100)
    db.session.commit()
    set_config("freeze", now - 3600)
    set_config("end", now - 10)
    clear_standings()
    assert ctf_ended()
    assert public() == [("alpha", 100)], "the event is over and the scoreboard is still frozen"
    set_config("freeze", None)
    assert [name for name, _ in public()] == ["alpha", "bravo"]


def test_ctfd_values_a_challenge_after_every_solve_in_two_places_and_does_not_look_at_the_freeze(app):
    """The two functions every value goes through after a player's flag: the module function (the base class calls it for every challenge
    that carries a scoring function, the dynamic type included) and the dynamic type's own classmethod. Part 3.6 puts its wrapper on exactly
    these two; here it is switched off to show CTFd alone."""
    from CTFd.plugins import challenges as standard
    from CTFd.plugins.dynamic_challenges import DynamicValueChallenge
    from CTFd.plugins.l3mon_scoring import freeze

    dyn_id, std_id = dynamic("dyn", flag="D").id, decaying("std", flag="S").id
    set_config("freeze", int(time.time()) - 3600)  # the scoreboard has been frozen for an hour
    clients = [team_client(app, f"p{n}", f"team{n}") for n in range(2)]
    freeze.ON["hold"] = False
    try:
        with mock.patch.object(standard, "calculate_value", wraps=standard.calculate_value) as module_level, mock.patch.object(
            DynamicValueChallenge, "calculate_value", wraps=DynamicValueChallenge.calculate_value
        ) as classmethod_level:
            for client in clients:
                assert attempt(client, dyn_id, "D") == "correct"
            assert classmethod_level.call_count == 2 and module_level.call_count == 2, (
                "a dynamic challenge is valued twice for each solve: by the base class (the module function) and by its own classmethod"
            )
            for client in clients:
                assert attempt(client, std_id, "S") == "correct"
            assert module_level.call_count == 4 and classmethod_level.call_count == 2, "a standard challenge with a scoring function: the module function only"
        assert db.session.query(Challenges.value).filter_by(id=dyn_id).scalar() == 499, "valued after the second solve although the board is frozen"
        assert db.session.query(Challenges.value).filter_by(id=std_id).scalar() == 499
    finally:
        freeze.ON["hold"] = True
