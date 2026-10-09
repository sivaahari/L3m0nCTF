"""Revoke and Restore on a real MariaDB: several workers at once, and a view of the database that is older than the other worker's commit.

SQLite (the default test database) lets one writer in at a time, so it can never show these races. On MariaDB every step is a
conditional statement whose row count decides who acted, and both actions take the plan lock first.

    tools/run-migration-test.sh -- /l3mon_tests/l3mon_scoring/test_voids_mariadb.py
"""
import os
import threading

import pytest

from CTFd.models import Challenges, Solves, Submissions, db
from CTFd.plugins.l3mon_core.models import Audit, Channel, Note, Void
from CTFd.plugins.l3mon_scoring import values, voids
from CTFd.plugins.l3mon_scoring.errors import Refused
from scoring_world import dynamic, make_app, solve, stored_value, studio
from tests.helpers import destroy_ctfd

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

WORKERS = 8
REASON = "The checker accepted a wrong answer"


def race(app, calls):
    """Run every call in its own thread (its own application context, session and connection), all released at once.
    -> (results, refusals, errors)."""
    barrier = threading.Barrier(len(calls))
    results, refusals, errors = [], [], []

    def worker(call):
        try:
            with app.app_context():
                barrier.wait()
                results.append(call())
        except Refused as refused:
            refusals.append(refused)
        except Exception as error:  # noqa: BLE001
            errors.append(repr(error))

    threads = [threading.Thread(target=worker, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, refusals, errors


def three_studios_solved(with_channel):
    dyn = dynamic("dyn")
    teams = [studio(f"t{i}") for i in range(3)]
    for minute, team in enumerate(teams):
        solve(team, dyn, minutes=minute)
    values.recalculate()
    db.session.commit()
    if with_channel:  # production always has channels: the plan lock is a row of the lowest one
        db.session.add(Channel(slug="street", name="Street", position=1, release_state="withheld"))
        db.session.commit()
    return dyn.id, [t.id for t in teams]


@pytest.mark.parametrize("with_channel", [True, False], ids=["with the plan lock row", "claims only, no channel"])
def test_eight_workers_revoking_one_challenge_set_each_solve_aside_once_and_none_fails(with_channel):
    app = make_app()
    with app.app_context():
        cid, team_ids = three_studios_solved(with_channel)
        for round_number in range(4):
            results, refusals, errors = race(app, [lambda: voids.revoke(cid, REASON)] * WORKERS)
            assert not errors, f"round {round_number}: {errors}"
            assert len(results) == 1 and results[0]["voided"] == 3, f"round {round_number}: exactly one worker did it ({results})"
            assert len(refusals) == WORKERS - 1 and {r.status for r in refusals} == {409}
            db.session.rollback()
            assert Solves.query.filter_by(challenge_id=cid).count() == 0
            assert Void.query.filter_by(challenge_id=cid, outcome="open").count() == 3, f"round {round_number}: each solve once"
            assert Note.query.filter_by(title="Solve voided").count() == 3 * (round_number + 1)
            assert Audit.query.filter_by(action="scoring.revoke").count() == round_number + 1
            assert stored_value(cid) == 500
            voids.restore(cid)  # for the next round
            assert Void.query.filter_by(challenge_id=cid, outcome="open").count() == 0
            assert stored_value(cid) == 495
    destroy_ctfd(app)


@pytest.mark.parametrize("with_channel", [True, False], ids=["with the plan lock row", "claims only, no channel"])
def test_eight_workers_restoring_put_each_solve_back_once_and_none_fails(with_channel):
    app = make_app()
    with app.app_context():
        cid, team_ids = three_studios_solved(with_channel)
        for round_number in range(4):
            voids.revoke(cid, REASON)
            results, refusals, errors = race(app, [lambda: voids.restore(cid)] * WORKERS)
            assert not errors, f"round {round_number}: {errors}"
            assert len(results) == 1 and results[0]["restored"] == 3 and len(refusals) == WORKERS - 1
            db.session.rollback()
            assert Solves.query.filter_by(challenge_id=cid).count() == 3
            assert Void.query.filter_by(challenge_id=cid, outcome="restored").count() == 3 * (round_number + 1)
            assert Note.query.filter_by(title="Solve restored").count() == 3 * (round_number + 1)
            assert stored_value(cid) == 495
    destroy_ctfd(app)


def set_aside_by_hand(challenge_id, team_id):
    """What Revoke does for one studio, done directly: the test needs a studio with an open record while the others hold solves."""
    row = db.session.query(Solves.id, Solves.user_id, Solves.date).filter_by(challenge_id=challenge_id, team_id=team_id).one()
    db.session.execute(Solves.__table__.delete().where(Solves.__table__.c.id == row.id))
    db.session.execute(Submissions.__table__.update().where(Submissions.__table__.c.id == row.id).values(type="discard"))
    db.session.add(Void(challenge_id=challenge_id, team_id=team_id, user_id=row.user_id, submission_id=row.id, solved_at=row.date, reason="earlier", outcome="open"))
    db.session.commit()


@pytest.mark.parametrize("with_channel", [True, False], ids=["with the plan lock row", "claims only, no channel"])
def test_a_revoke_and_a_restore_started_together_end_in_one_consistent_state(with_channel):
    """Studios X and Y hold solves; Z has an open record. Revoke sets X and Y aside; Restore puts Z back. Whichever runs first, at the
    end every studio either holds its solve or has it set aside, never both and never neither, and the stored value follows."""
    app = make_app()
    with app.app_context():
        teams = [studio(f"t{i}") for i in range(3)]
        if with_channel:
            db.session.add(Channel(slug="street", name="Street", position=1, release_state="withheld"))
            db.session.commit()
        for round_number in range(6):
            dyn = dynamic(f"dyn{round_number}")
            cid = dyn.id
            for minute, team in enumerate(teams):
                solve(team, dyn, minutes=minute)
            values.recalculate([cid])
            db.session.commit()
            set_aside_by_hand(cid, teams[2].id)
            results, refusals, errors = race(app, [lambda: voids.revoke(cid, REASON), lambda: voids.restore(cid)])
            assert not errors, f"round {round_number}: {errors}"
            assert len(results) == 2 and not refusals, f"round {round_number}: both have work to do whichever goes first"
            db.session.rollback()
            holders = {s.team_id for s in Solves.query.filter_by(challenge_id=cid).all()}
            opens = {v.team_id for v in Void.query.filter_by(challenge_id=cid, outcome="open").all()}
            for team in teams:
                assert (team.id in holders) != (team.id in opens), f"round {round_number}: studio {team.id} holds {team.id in holders}, has an open record {team.id in opens}"
            assert stored_value(cid) == values.formula(Challenges.query.get(cid), len(holders))
    destroy_ctfd(app)


def test_a_worker_with_an_older_view_of_the_database_still_acts_on_current_data():
    """MariaDB's default isolation (REPEATABLE READ) gives a transaction the view it had at its first read. Worker A starts a view,
    worker B revokes and commits, then A restores: without the lock's fresh view A saw no open record and refused."""
    app = make_app()
    with app.app_context():
        cid, _ = three_studios_solved(True)
        a_has_a_view, b_is_done = threading.Event(), threading.Event()
        box = {}

        def worker_a():
            with app.app_context():
                Challenges.query.filter_by(id=cid).first()  # the first plain read: from here on A sees the world as it is now
                a_has_a_view.set()
                b_is_done.wait(30)
                try:
                    box["a"] = voids.restore(cid)
                except Exception as error:  # noqa: BLE001
                    box["a"] = repr(error)

        def worker_b():
            with app.app_context():
                a_has_a_view.wait(30)
                box["b"] = voids.revoke(cid, REASON)
                b_is_done.set()

        threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert box["b"]["voided"] == 3
        assert isinstance(box["a"], dict) and box["a"]["restored"] == 3, f"A acted on stale data: {box['a']}"
    destroy_ctfd(app)


def test_banning_a_studio_corrects_the_value_in_the_same_commit_on_mariadb():
    app = make_app()
    with app.app_context():
        cid, team_ids = three_studios_solved(True)
        assert stored_value(cid) == 495
        from CTFd.models import Teams

        Teams.query.filter_by(id=team_ids[1]).first().banned = True
        db.session.commit()
        assert stored_value(cid) == 499, "two studios count: the value was put right by the commit that banned the third"
    destroy_ctfd(app)
