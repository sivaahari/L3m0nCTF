"""What the scoring design relies on in CTFd 3.8.8, kept as tests. If a CTFd upgrade changes one of these, the design (not only a test)
needs a second look; the facts were measured with plugins/l3mon_scoring/tests/probe_facts.py (docs/superpowers/plans/2026-10-09-sp3-3-scoring.md).

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import pytest

from CTFd.models import Awards, Challenges, Solves, Submissions, Teams, Users, db
from CTFd.plugins.l3mon_scoring import triggers, values, wording
from scoring_world import attempt, dynamic, fixed, make_app, score_of, solve, standings, stored_value, studio, team_client
from tests.helpers import destroy_ctfd, login_as_user


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


def test_mark_incorrect_deletes_the_solve_and_the_submission_so_nothing_remains_of_how_it_was_solved(app):
    """Why Revoke is our own action: CTFd's 'mark incorrect' leaves no record at all."""
    admin = login_as_user(app, "admin")
    cid = fixed("fix", value=100).id
    team_id = studio("alpha").id
    team = Teams.query.filter_by(id=team_id).first()
    sid = solve(team, Challenges.query.get(cid)).id
    r = admin.patch(f"/api/v1/submissions/{sid}", json={"type": "incorrect"})
    assert r.status_code == 200
    db.session.rollback()
    assert Submissions.query.filter_by(id=sid).count() == 0 and Solves.query.filter_by(id=sid).count() == 0


def test_mark_incorrect_leaves_a_dynamic_value_stale_unless_we_recalculate(app):
    admin = login_as_user(app, "admin")
    dyn = dynamic("dyn", flag="F")
    cid = dyn.id
    clients = [team_client(app, f"p{i}", f"team{i}") for i in range(3)]
    for client in clients:
        assert attempt(client, cid, "F") == "correct"
    assert stored_value(cid) == 495
    triggers.ON.update(flush=False, bulk=False, check=False)
    try:
        sid = Solves.query.filter_by(challenge_id=cid).first().id
        assert admin.patch(f"/api/v1/submissions/{sid}", json={"type": "incorrect"}).status_code == 200
        assert stored_value(cid) == 495, "CTFd itself does not recalculate: two studios hold a solve and the value is still the three-studio value"
    finally:
        triggers.ON.update(flush=True, bulk=True, check=True)
    values.recalculate()
    db.session.commit()
    assert stored_value(cid) == 499


def test_the_revoke_trick_delete_the_solves_row_and_turn_the_submission_into_a_discard(app):
    """What Revoke relies on: the score, the standings and the solve count drop at once; the submission stays and loads as a discard;
    the studio may solve the challenge again."""
    team_id = studio("alpha").id
    cid = fixed("fix", value=100, flag="F").id
    row = solve(Teams.query.filter_by(id=team_id).first(), Challenges.query.get(cid))
    sid = row.id
    assert standings() == [("alpha", 100)]
    db.session.execute(Solves.__table__.delete().where(Solves.__table__.c.id == sid))
    db.session.execute(Submissions.__table__.update().where(Submissions.__table__.c.id == sid).values(type="discard"))
    db.session.commit()
    assert standings() == [] and score_of(team_id) == 0
    db.session.expunge_all()  # a web request starts with an empty session; one that still holds the old Solves object would keep its class
    assert [type(s).__name__ for s in Submissions.query.filter_by(id=sid).all()] == ["Discards"]
    assert Solves.query.filter_by(challenge_id=cid).count() == 0
    solve(Teams.query.filter_by(id=team_id).first(), Challenges.query.get(cid), minutes=9)  # nothing in the database stops a second solve
    assert standings() == [("alpha", 100)]


def test_an_award_counts_in_the_standings_by_team_but_in_the_teams_own_score_only_through_a_member(app):
    """Why a team-wide bonus is attached to the captain: CTFd's team score sums its members' awards by user; the standings sum by team."""
    a, b = studio("alpha"), studio("beta")
    outsider = sorted(b.members, key=lambda u: u.id)[0].id
    db.session.add(Awards(user_id=outsider, team_id=a.id, name="stray", value=50, description="", category="x"))
    db.session.commit()
    assert standings() == [("alpha", 50)], "the standings count it for the team it names"
    assert score_of(a.id) == 0, "the team's own score does not: the user is not one of its members"


def test_the_hint_sentence_we_rewrite_is_the_one_ctfd_has(app):
    import CTFd.api.v1.unlocks as unlocks

    assert wording.STOCK in open(unlocks.__file__, encoding="utf-8").read()


def test_a_ban_leaves_the_stored_value_alone_unless_a_hook_acts(app):
    """The stock behaviour the flush hook exists for."""
    admin = login_as_user(app, "admin")
    cid = dynamic("dyn", flag="F").id
    clients = [team_client(app, f"p{i}", f"team{i}") for i in range(3)]
    for client in clients:
        assert attempt(client, cid, "F") == "correct"
    team_id = Users.query.filter_by(name="p1").first().team_id
    triggers.ON.update(flush=False, bulk=False, check=False)
    try:
        assert admin.patch(f"/api/v1/teams/{team_id}", json={"banned": True}).status_code == 200
        assert stored_value(cid) == 495
    finally:
        triggers.ON.update(flush=True, bulk=True, check=True)
