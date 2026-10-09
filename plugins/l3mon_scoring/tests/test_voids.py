"""Revoke and Restore: a broken challenge's solves are set aside, nothing is deleted, and putting them back returns the exact totals.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import datetime

import pytest

from CTFd.models import Challenges, Solves, Submissions, Users, db
from CTFd.plugins.l3mon_core.models import Audit, Note, Void
from CTFd.plugins.l3mon_core.tick import tick
from CTFd.plugins.l3mon_scoring import values, voids
from CTFd.plugins.l3mon_scoring.errors import Refused
from scoring_world import dynamic, fixed, make_app, score_of, solve, standings, stored_value, studio
from tests.helpers import destroy_ctfd

REASON = "The checker accepted a wrong answer"


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


class World:
    """Four studios; the dynamic challenge solved by three of them (alpha, beta, gamma), a fixed one by alpha and delta."""

    def __init__(self):
        dyn, fix = dynamic("dyn"), fixed("fix", value=100)
        self.dyn, self.fix = dyn.id, fix.id
        self.teams = {n: studio(n) for n in ("alpha", "beta", "gamma", "delta")}
        self.ids = {n: t.id for n, t in self.teams.items()}
        self.solves = {}
        for minute, name in enumerate(("alpha", "beta", "gamma")):
            self.solves[name] = solve(self.teams[name], dyn, minutes=minute).id
        solve(self.teams["alpha"], fix, minutes=5)
        solve(self.teams["delta"], fix, minutes=6)
        values.recalculate()
        db.session.commit()

    def snapshot(self):
        return {
            "standings": standings(),
            "scores": {n: score_of(i) for n, i in self.ids.items()},
            "value": stored_value(self.dyn),
            "solves": sorted((s.id, s.team_id, s.challenge_id, s.date) for s in Solves.query.all()),
        }


@pytest.fixture()
def world(app):
    return World()


@pytest.mark.parametrize("which", ["dyn", "fix"])
def test_revoke_then_restore_returns_every_total_place_and_date_exactly(world, which):
    cid = getattr(world, which)
    before = world.snapshot()
    assert before["value"] == 495  # three studios have solved the dynamic challenge

    gone = voids.revoke(cid, REASON)
    during = world.snapshot()
    assert gone["voided"] == (3 if which == "dyn" else 2)
    assert Solves.query.filter_by(challenge_id=cid).count() == 0
    assert during["scores"] != before["scores"]
    if which == "dyn":
        assert (gone["value_before"], gone["value_after"]) == (495, 500), "nobody has solved it now, so it is worth its start value"
        assert during["value"] == 500

    back = voids.restore(cid)
    after = world.snapshot()
    assert back["restored"] == gone["voided"] and back["skipped"] == 0 and back["superseded"] == 0
    assert after == before, "the same standings, scores, value, solve ids and solve dates as before"


def test_nothing_is_deleted_the_record_of_how_it_was_solved_stays(world):
    originals = {s.id: (s.team_id, s.user_id, s.ip, s.provided, s.date) for s in Submissions.query.filter(Submissions.id.in_(world.solves.values())).all()}
    voids.revoke(world.dyn, REASON)
    db.session.rollback()
    rows = {s.id: s for s in Submissions.query.filter(Submissions.id.in_(world.solves.values())).all()}
    assert set(rows) == set(originals), "every submission is still there"
    assert all(r.type == "discard" for r in rows.values())
    assert {i: (r.team_id, r.user_id, r.ip, r.provided, r.date) for i, r in rows.items()} == originals, "who, from where, what they sent, when"
    records = Void.query.filter_by(challenge_id=world.dyn).order_by(Void.id).all()
    assert len(records) == 3
    for rec in records:
        assert rec.outcome == "open" and rec.reason == REASON and rec.restored_at is None and rec.voided_at is not None
        team_id, user_id, _, _, when = originals[rec.submission_id]
        assert (rec.team_id, rec.user_id, rec.solved_at) == (team_id, user_id, when)
    voids.restore(world.dyn)
    db.session.rollback()
    assert {s.id: s.type for s in Submissions.query.filter(Submissions.id.in_(world.solves.values())).all()} == {i: "correct" for i in world.solves.values()}
    assert {s.id: s.date for s in Solves.query.filter_by(challenge_id=world.dyn).all()} == {i: originals[i][4] for i in world.solves.values()}


def test_the_studios_are_told_with_the_reason_and_again_when_it_comes_back(world):
    voids.revoke(world.dyn, REASON)
    lines = Note.query.order_by(Note.id).all()
    assert sorted(n.team_id for n in lines) == sorted(world.ids[n] for n in ("alpha", "beta", "gamma"))
    assert all((n.title, n.text) == ("Solve voided", REASON) for n in lines)
    assert Note.query.filter_by(team_id=world.ids["delta"]).count() == 0, "a studio that never solved it is told nothing"
    voids.restore(world.dyn, "The checker is fixed")
    back = Note.query.filter(Note.title == "Solve restored").all()
    assert sorted(n.team_id for n in back) == sorted(world.ids[n] for n in ("alpha", "beta", "gamma"))
    assert all("The checker is fixed" in n.text for n in back)
    voids.revoke(world.dyn, "again")
    voids.restore(world.dyn)
    assert any(n.text for n in Note.query.filter(Note.title == "Solve restored").all())  # without a reason the studio still gets a sentence
    assert all(n.text.strip() for n in Note.query.all())


def test_a_studio_that_solved_it_again_keeps_that_one_and_its_old_record_is_skipped(world):
    voids.revoke(world.dyn, REASON)
    again = solve(world.teams["alpha"], Challenges.query.get(world.dyn), minutes=30)
    new_id = again.id
    values.recalculate()
    db.session.commit()
    assert stored_value(world.dyn) == 500 and score_of(world.ids["alpha"]) == 600  # 100 for the fixed one, 500 for the new solve
    out = voids.restore(world.dyn)
    assert (out["restored"], out["skipped"], out["superseded"]) == (2, 1, 0)
    assert Solves.query.filter_by(challenge_id=world.dyn, team_id=world.ids["alpha"]).count() == 1
    assert Solves.query.filter_by(challenge_id=world.dyn, team_id=world.ids["alpha"]).one().id == new_id
    skipped = Void.query.filter_by(challenge_id=world.dyn, team_id=world.ids["alpha"]).one()
    assert skipped.outcome == "skipped" and skipped.restored_at is None and skipped.restored_by is None
    assert Submissions.query.filter_by(id=world.solves["alpha"]).one().type == "discard", "the old submission stays set aside"
    assert stored_value(world.dyn) == 495, "three studios hold a solve again: alpha's new one, beta's and gamma's"
    assert score_of(world.ids["alpha"]) == 595, "alpha's old solve did not come back as a second one: it has 100 + 495"


def test_void_solve_again_void_again_then_restore_gives_back_the_earliest_and_supersedes_the_later(world):
    voids.revoke(world.dyn, "first time")
    solve(world.teams["alpha"], Challenges.query.get(world.dyn), minutes=40)
    db.session.commit()
    voids.revoke(world.dyn, "second time")
    records = Void.query.filter_by(challenge_id=world.dyn, team_id=world.ids["alpha"]).order_by(Void.id).all()
    assert len(records) == 2
    first_submission = records[0].submission_id
    assert first_submission == world.solves["alpha"]
    out = voids.restore(world.dyn)
    assert (out["restored"], out["skipped"], out["superseded"]) == (3, 0, 1)
    db.session.rollback()
    records = Void.query.filter_by(challenge_id=world.dyn, team_id=world.ids["alpha"]).order_by(Void.id).all()
    assert [r.outcome for r in records] == ["restored", "superseded"]
    assert Solves.query.filter_by(challenge_id=world.dyn, team_id=world.ids["alpha"]).one().id == first_submission
    assert Submissions.query.filter_by(id=records[1].submission_id).one().type == "discard"
    assert [r.restored_at is not None for r in records] == [True, False]


@pytest.mark.parametrize(
    "call, why",
    [
        (lambda w: voids.revoke(w.dyn, ""), "reason"),
        (lambda w: voids.revoke(w.dyn, "   "), "reason"),
        (lambda w: voids.revoke(w.dyn, "<b>x</b>"), "reason"),
        (lambda w: voids.revoke(w.dyn, "x" * 501), "reason"),
        (lambda w: voids.revoke(w.dyn, None), "reason"),
        (lambda w: voids.revoke("1", REASON), "challenge_id"),
        (lambda w: voids.revoke(None, REASON), "challenge_id"),
        (lambda w: voids.revoke(True, REASON), "challenge_id"),
        (lambda w: voids.restore("1"), "challenge_id"),
        (lambda w: voids.restore(w.dyn, "<b>"), "reason"),
        (lambda w: voids.restore(w.dyn, "x" * 501), "reason"),
    ],
)
def test_bad_requests_are_named_and_change_nothing(world, call, why):
    before = world.snapshot()
    notes_before, audit_before, voids_before = Note.query.count(), Audit.query.count(), Void.query.count()
    with pytest.raises(Refused) as caught:
        call(world)
    assert why in caught.value.problems and caught.value.status == 400
    assert world.snapshot() == before
    assert (Note.query.count(), Audit.query.count(), Void.query.count()) == (notes_before, audit_before, voids_before)


def test_nothing_to_set_aside_nothing_to_restore_and_an_unknown_challenge(world):
    unsolved = fixed("unsolved", value=10).id
    before = world.snapshot()
    with pytest.raises(Refused) as caught:
        voids.revoke(unsolved, REASON)
    assert caught.value.status == 409 and "challenge_id" in caught.value.problems
    with pytest.raises(Refused) as caught:
        voids.restore(world.dyn)
    assert caught.value.status == 409
    with pytest.raises(Refused) as caught:
        voids.revoke(99999, REASON)
    assert caught.value.status == 404
    with pytest.raises(Refused) as caught:
        voids.restore(99999)
    assert caught.value.status == 404
    assert world.snapshot() == before and Void.query.count() == 0 and Note.query.count() == 0


def test_a_second_revoke_finds_nothing_and_a_second_restore_finds_nothing(world):
    voids.revoke(world.dyn, REASON)
    with pytest.raises(Refused) as caught:
        voids.revoke(world.dyn, REASON)
    assert caught.value.status == 409
    assert Void.query.filter_by(challenge_id=world.dyn).count() == 3, "each solve was set aside once"
    voids.restore(world.dyn)
    with pytest.raises(Refused) as caught:
        voids.restore(world.dyn)
    assert caught.value.status == 409


def test_studios_that_are_banned_or_hidden_are_revoked_and_restored_like_any_other(world):
    world.teams["beta"].banned = True
    world.teams["gamma"].hidden = True
    db.session.commit()
    values.recalculate()
    db.session.commit()
    out = voids.revoke(world.dyn, REASON)
    assert out["voided"] == 3
    assert Solves.query.filter_by(challenge_id=world.dyn).count() == 0
    back = voids.restore(world.dyn)
    assert back["restored"] == 3
    assert Solves.query.filter_by(challenge_id=world.dyn).count() == 3


def test_a_deleted_member_is_skipped_not_an_error_and_the_others_still_come_back(world):
    voids.revoke(world.dyn, REASON)
    user_id = Void.query.filter_by(team_id=world.ids["beta"]).one().user_id
    Users.query.filter_by(id=user_id).delete()
    db.session.commit()
    out = voids.restore(world.dyn)
    assert (out["restored"], out["skipped"]) == (2, 1)
    assert Void.query.filter_by(team_id=world.ids["beta"]).one().outcome == "skipped"


def test_a_submission_the_crew_changed_by_hand_is_not_put_back(world):
    voids.revoke(world.dyn, REASON)
    Submissions.query.filter_by(id=world.solves["gamma"]).update({"type": "incorrect"})
    db.session.commit()
    out = voids.restore(world.dyn)
    assert (out["restored"], out["skipped"]) == (2, 1)
    assert Void.query.filter_by(team_id=world.ids["gamma"]).one().outcome == "skipped"
    assert Submissions.query.filter_by(id=world.solves["gamma"]).one().type == "incorrect", "restore never touches what it did not set aside"


def test_a_deleted_submission_is_skipped(world):
    voids.revoke(world.dyn, REASON)
    Submissions.query.filter_by(id=world.solves["alpha"]).delete()
    db.session.commit()
    assert Void.query.filter_by(team_id=world.ids["alpha"]).one().submission_id is None
    out = voids.restore(world.dyn)
    assert (out["restored"], out["skipped"]) == (2, 1)


def test_the_audit_trail_says_who_what_how_many_and_why(world):
    boss = Users.query.filter_by(name="admin").first()
    voids.revoke(world.dyn, REASON, actor=boss)
    voids.restore(world.dyn, "fixed now", actor=boss)
    lines = Audit.query.filter(Audit.action.in_(["scoring.revoke", "scoring.restore"])).order_by(Audit.id).all()
    assert [l.action for l in lines] == ["scoring.revoke", "scoring.restore"]
    assert all(l.actor_name == "admin" and l.target == "dyn" for l in lines)
    assert "3 solve" in lines[0].detail and REASON in lines[0].detail and "495" in lines[0].detail and "500" in lines[0].detail
    assert "3 restored" in lines[1].detail and "fixed now" in lines[1].detail
    assert Void.query.first().voided_by == boss.id
    restored = Void.query.filter_by(outcome="restored").first()
    assert restored.restored_by == boss.id and restored.restored_at is not None


def test_the_tick_moves_and_ctfds_own_numbers_agree_after_each_step(world):
    from CTFd.utils.scores import get_standings

    t0 = tick.value()
    voids.revoke(world.dyn, REASON)
    assert tick.value() > t0
    t1 = tick.value()
    assert score_of(world.ids["beta"]) == 0  # CTFd's memoized score was not left behind
    assert [(r.name, int(r.score)) for r in get_standings(admin=True)] == [("alpha", 100), ("delta", 100)]
    voids.restore(world.dyn)
    assert tick.value() > t1
    assert score_of(world.ids["beta"]) == 495
    assert [(r.name, int(r.score)) for r in get_standings(admin=True)] == [("alpha", 595), ("beta", 495), ("gamma", 495), ("delta", 100)]


def test_dynamic_values_are_recalculated_for_the_challenge_asked_about_only(world):
    other = dynamic("other", initial=250, minimum=100, decay=10)
    solve(world.teams["delta"], other)
    db.session.commit()
    db.session.query(Challenges).filter_by(id=other.id).update({"value": 7})
    db.session.commit()
    voids.revoke(world.dyn, REASON)
    assert stored_value(other.id) == 7, "another challenge's stale value is the minute check's business, not Revoke's"


def test_a_member_who_solved_it_again_for_another_studio_is_skipped_and_nothing_is_half_done(world):
    """The database allows one solve per member per challenge. If the member moved to another studio and solved the challenge there,
    the old solve cannot come back: that record is skipped, and the savepoint leaves the submission set aside, not half turned."""
    voids.revoke(world.dyn, REASON)
    record = Void.query.filter_by(team_id=world.ids["alpha"]).one()
    user_id, old_submission = record.user_id, record.submission_id
    Users.query.filter_by(id=user_id).update({"team_id": world.ids["delta"]})
    solve_row = Solves(user_id=user_id, team_id=world.ids["delta"], challenge_id=world.dyn, ip="127.0.0.1", provided="again", date=datetime.datetime(2026, 11, 28, 6, 0))
    db.session.add(solve_row)
    db.session.commit()
    out = voids.restore(world.dyn)
    assert (out["restored"], out["skipped"]) == (2, 1)
    db.session.rollback()
    assert Void.query.filter_by(team_id=world.ids["alpha"]).one().outcome == "skipped"
    assert Submissions.query.filter_by(id=old_submission).one().type == "discard", "the failed attempt was undone completely"
    assert Solves.query.filter_by(id=old_submission).count() == 0
