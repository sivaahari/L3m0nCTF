"""Bonus: TRP for a studio with a private message. Counts everywhere TRP counts; only its title is public.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import datetime

import pytest
from freezegun import freeze_time

from CTFd.models import Awards, Teams, db
from CTFd.plugins.l3mon_core.models import Audit, Bonus, Note
from CTFd.plugins.l3mon_core.tick import tick
from CTFd.plugins.l3mon_scoring import bonus
from CTFd.plugins.l3mon_scoring.errors import Refused
from scoring_world import fixed, make_app, score_of, solve, standings, studio
from tests.helpers import destroy_ctfd, login_as_user

T = datetime.datetime(2026, 11, 28, 6, 0, 0)


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


def counts():
    db.session.rollback()
    return (Awards.query.count(), Bonus.query.count(), Note.query.count(), Audit.query.count())


def refused(**kw):
    with pytest.raises(Refused) as caught:
        bonus.give(**kw)
    return caught.value


def test_a_team_wide_bonus_counts_for_the_studio_and_belongs_to_the_captain(app):
    team = studio("alpha", members=3)
    solve(team, fixed("f", value=100))
    captain_id = team.captain_id
    before = score_of(team.id)
    got = bonus.give(team.id, trp=50, message="Found a bug in the lobby")
    assert score_of(team.id) == before + 50 == 150
    assert standings() == [("alpha", 150)]
    award = Awards.query.one()
    assert (award.user_id, award.team_id, award.value, award.name) == (captain_id, team.id, 50, "Bonus +50 TRP")
    row = Bonus.query.one()
    assert (row.scope, row.user_id, row.team_id, row.award_id) == ("team", captain_id, team.id, award.id)
    assert got == {"award_id": award.id, "team_id": team.id, "user_id": captain_id, "scope": "team", "trp": 50, "title": "Bonus +50 TRP"}


def test_a_member_bonus_is_on_that_member_and_counts_for_the_studio_too(app):
    team = studio("alpha", members=3)
    member = sorted(team.members, key=lambda u: u.id)[2]
    bonus.give(team.id, user_id=member.id, trp=20, message="Helped another studio")
    award = Awards.query.one()
    assert award.user_id == member.id and Bonus.query.one().scope == "member"
    assert score_of(team.id) == 20 and standings() == [("alpha", 20)]
    db.session.expire_all()
    assert member.get_score(admin=True) == 20


def test_a_negative_amount_is_an_adjustment(app):
    team = studio("alpha")
    solve(team, fixed("f", value=100))
    got = bonus.give(team.id, trp=-30, message="Shared a hint outside the studio")
    assert got["title"] == "Adjustment -30 TRP" and Awards.query.one().name == "Adjustment -30 TRP"
    assert score_of(team.id) == 70


def test_the_message_is_private_only_the_title_is_in_the_award(app):
    team = studio("alpha")
    bonus.give(team.id, trp=50, message="SECRET-NOTE found a bug in the lobby")
    award = Awards.query.one()
    assert not award.description and "SECRET" not in (award.name + (award.category or "") + (award.icon or ""))
    assert Bonus.query.one().message == "SECRET-NOTE found a bug in the lobby"
    note = Note.query.one()
    assert (note.team_id, note.title, note.text) == (team.id, "Bonus +50 TRP", "SECRET-NOTE found a bug in the lobby")


def test_one_audit_line_names_who_gave_it_and_why(app):
    from CTFd.models import Users

    team_id = studio("alpha").id
    boss = Users.query.filter_by(name="admin").first()
    boss_id = boss.id
    bonus.give(team_id, trp=50, message="a bug in the lobby", actor=boss)
    line = Audit.query.filter_by(action="scoring.bonus").one()
    assert line.actor_name == "admin" and line.target == "alpha"
    assert "Bonus +50 TRP" in line.detail and "a bug in the lobby" in line.detail
    assert Bonus.query.one().given_by == boss_id


@pytest.mark.parametrize(
    "kw, field",
    [
        ({"trp": 0, "message": "ok"}, "trp"),
        ({"trp": 1001, "message": "ok"}, "trp"),
        ({"trp": -1001, "message": "ok"}, "trp"),
        ({"trp": 1.5, "message": "ok"}, "trp"),
        ({"trp": "50", "message": "ok"}, "trp"),
        ({"trp": True, "message": "ok"}, "trp"),
        ({"trp": None, "message": "ok"}, "trp"),
        ({"trp": 5, "message": ""}, "message"),
        ({"trp": 5, "message": "   "}, "message"),
        ({"trp": 5, "message": "x" * 201}, "message"),
        ({"trp": 5, "message": "<b>bold</b>"}, "message"),
        ({"trp": 5, "message": "two\nlines"}, "message"),
        ({"trp": 5, "message": None}, "message"),
    ],
)
def test_every_refusal_names_its_field_and_changes_nothing(app, kw, field):
    team = studio("alpha")
    before = counts()
    error = refused(team_id=team.id, **kw)
    assert field in error.problems and error.status == 400
    assert counts() == before
    assert score_of(team.id) == 0


def test_an_unknown_studio_a_stranger_as_member_and_a_studio_without_a_captain_are_refused(app):
    team, other = studio("alpha"), studio("beta")
    outsider = sorted(other.members, key=lambda u: u.id)[0]
    before = counts()
    assert refused(team_id=99999, trp=5, message="ok").status == 404
    assert "user_id" in refused(team_id=team.id, user_id=outsider.id, trp=5, message="ok").problems
    assert "user_id" in refused(team_id=team.id, user_id=99999, trp=5, message="ok").problems
    assert "team_id" in refused(team_id="1", trp=5, message="ok").problems
    team.captain_id = None
    db.session.commit()
    assert "user_id" in refused(team_id=team.id, trp=5, message="ok").problems
    assert counts() == before


def test_the_same_bonus_twice_in_a_minute_is_refused_as_a_double_click_and_a_different_one_is_not(app):
    team = studio("alpha")
    with freeze_time(T):
        bonus.give(team.id, trp=50, message="a bug")
    with freeze_time(T + datetime.timedelta(seconds=30)):
        error = refused(team_id=team.id, trp=50, message="a bug")
        assert error.status == 409
        bonus.give(team.id, trp=50, message="another bug")  # a different message
        bonus.give(team.id, trp=60, message="a bug")  # a different amount
    with freeze_time(T + datetime.timedelta(seconds=61)):
        bonus.give(team.id, trp=50, message="a bug")  # a minute later it is a new decision
    assert Awards.query.count() == 4


def test_a_bonus_moves_the_tie_break_to_the_date_of_the_award(app):
    """Two studios on equal TRP: the one whose score last changed earlier ranks higher (CTFd's rule, pinned here with our action)."""
    a, b = studio("alpha"), studio("beta")
    f = fixed("f", value=100)
    solve(a, f, minutes=0)
    solve(b, f, minutes=1)
    assert standings() == [("alpha", 100), ("beta", 100)]  # alpha got there first
    # CTFd stamps an award with the real clock (its column default is bound at import, so a frozen clock does not reach it): date them
    first = bonus.give(b.id, trp=10, message="one")
    second = bonus.give(a.id, trp=10, message="two")
    Awards.query.filter_by(id=first["award_id"]).update({"date": T})
    Awards.query.filter_by(id=second["award_id"]).update({"date": T + datetime.timedelta(minutes=5)})
    db.session.commit()
    assert standings() == [("beta", 110), ("alpha", 110)], "beta's score reached 110 first, so beta ranks first now"


def test_the_standings_are_cleared_by_the_action(app):
    team = studio("alpha")
    assert standings() == []
    from CTFd.utils.scores import get_standings

    get_standings(admin=True)  # cache a view with nothing in it
    bonus.give(team.id, trp=5, message="ok")
    assert [(r.name, int(r.score)) for r in get_standings(admin=True)] == [("alpha", 5)]


def test_the_tick_moves_once(app):
    team = studio("alpha")
    before = tick.value()
    bonus.give(team.id, trp=5, message="ok")
    assert tick.value() == before + 1


def test_ctfds_own_award_delete_removes_the_bonus_and_leaves_the_note(app):
    team_id = studio("alpha").id
    bonus.give(team_id, trp=5, message="ok")
    award_id = Awards.query.one().id
    admin = login_as_user(app, "admin")
    assert admin.delete(f"/api/v1/awards/{award_id}", json={}).status_code == 200
    db.session.rollback()
    assert Awards.query.count() == 0 and Bonus.query.count() == 0
    assert Note.query.count() == 1, "what the studio was told stays"
    assert score_of(team_id) == 0


def test_user_mode_is_refused(app):
    from CTFd.utils import set_config

    team = studio("alpha")
    set_config("user_mode", "users")
    before = counts()
    error = refused(team_id=team.id, trp=5, message="ok")
    assert "team_id" in error.problems and counts() == before
    set_config("user_mode", "teams")
