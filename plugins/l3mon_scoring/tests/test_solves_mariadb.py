"""Eight players send the right flag for the same challenge at the same instant, on a real MariaDB, through the real routes: all eight
solves are recorded and none of them is a 500.

Why this test exists. A first version of the "a new solve counts at once" fix recalculated the value inside the solve's own transaction.
That transaction has already inserted a row that points at the challenge (InnoDB takes a shared lock on the challenge row for it) and
would then update the value (an exclusive lock): two players doing that at once deadlock, and the loser is told 500 and loses a correct
flag. The independent audit measured 10 of 24 recorded. CTFd itself avoids it by valuing the challenge in a second transaction after the
solve is committed, and so does the plugin now: it notes which challenges got a solve and recalculates them at the end of the request,
in a fresh transaction that holds no lock.

    tools/run-migration-test.sh -- /l3mon_tests/l3mon_scoring/test_solves_mariadb.py
"""
import datetime
import os
import threading
import time

import pytest

from CTFd.models import Challenges, Solves, db
from CTFd.plugins.l3mon_scoring import values
from scoring_world import dynamic, make_app, stored_value, studio, team_client
from tests.helpers import destroy_ctfd

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

PLAYERS = 8
ROUNDS = 3
FLAG = "F-RACE"


def test_simultaneous_correct_flags_are_all_recorded_and_the_value_ends_right():
    app = make_app()
    with app.app_context():
        clients = [team_client(app, f"racer{i}", f"studio{i}") for i in range(PLAYERS)]
        for round_number in range(ROUNDS):
            cid = dynamic(f"dyn{round_number}", flag=FLAG).id
            barrier, outcomes, errors = threading.Barrier(PLAYERS), [], []

            def worker(client):
                try:
                    with app.app_context():
                        barrier.wait()
                        r = client.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": FLAG})
                        outcomes.append((r.status_code, (r.get_json() or {}).get("data", {}).get("status")))
                except Exception as error:  # noqa: BLE001
                    errors.append(repr(error))

            threads = [threading.Thread(target=worker, args=(c,)) for c in clients]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"round {round_number}: {errors}"
            assert sorted(outcomes) == [(200, "correct")] * PLAYERS, f"round {round_number}: {outcomes}"
            db.session.rollback()
            assert Solves.query.filter_by(challenge_id=cid).count() == PLAYERS, f"round {round_number}: every correct flag was recorded"
            values.heal()  # CTFd's own two-step valuation can finish a solve behind when solves overlap; the minute check closes that
            assert stored_value(cid) == values.formula(values.decaying_rows([cid])[0], PLAYERS)
    destroy_ctfd(app)


def test_a_solve_does_not_touch_the_challenge_row_before_it_commits():
    """The deterministic form of the race above (the route-level test overlaps too little to fail reliably in one process): a
    transaction that has inserted a solve holds InnoDB's shared lock on the challenge row. Another transaction asks for the exclusive
    lock (CTFd's own value update does) and waits. If the plugin now also asked for the exclusive lock inside the solve's transaction,
    that is a deadlock and one of the two fails. It must simply commit."""
    app = make_app()
    with app.app_context():
        cid = dynamic("dyn", flag=FLAG).id
        team = studio("alpha")
        user_id, team_id = team.captain_id, team.id
        db.session.query(Challenges).filter_by(id=cid).update({"value": 7})  # stale on purpose: a recalculation would want to change it
        db.session.commit()
        a_holds_the_shared_lock, b_is_waiting = threading.Event(), threading.Event()
        box = {}

        def worker_a():
            with app.test_request_context("/api/v1/challenges/attempt-like"):  # the plugin acts inside a request
                try:
                    db.session.add(Solves(user_id=user_id, team_id=team_id, challenge_id=cid, ip="127.0.0.1", provided=FLAG, date=datetime.datetime(2026, 11, 28, 4, 0)))
                    db.session.flush()
                    a_holds_the_shared_lock.set()
                    b_is_waiting.wait(10)
                    time.sleep(1.0)  # B's exclusive request is queued behind our shared lock now
                    db.session.commit()
                    box["a"] = "ok"
                except Exception as error:  # noqa: BLE001
                    db.session.rollback()
                    box["a"] = repr(error)[:300]

        def worker_b():
            with app.app_context():
                a_holds_the_shared_lock.wait(10)
                connection = db.engine.connect()
                transaction = connection.begin()
                b_is_waiting.set()
                try:
                    connection.execute(db.text("UPDATE challenges SET value = value WHERE id = :i"), {"i": cid})
                    transaction.commit()
                    box["b"] = "ok"
                except Exception as error:  # noqa: BLE001
                    transaction.rollback()
                    box["b"] = repr(error)[:300]
                finally:
                    connection.close()

        threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(70)
        assert box == {"a": "ok", "b": "ok"}, f"a solve and an update of the same challenge must both succeed: {box}"
        db.session.rollback()
        assert Solves.query.filter_by(challenge_id=cid).count() == 1
    destroy_ctfd(app)
