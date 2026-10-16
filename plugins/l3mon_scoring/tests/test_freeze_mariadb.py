"""CTFd's own valuation after a player's flag, beside a crew action, on a real MariaDB (SP3 part 3.6).

Part 3.3's audit found that CTFd counts before it waits for the challenge row: if a Revoke holds the row and has not committed, CTFd's count
is out of date by the time its UPDATE gets through, and it overwrites the Revoke's number. The wrapper in `freeze.py` takes the row's lock
first, so nothing is counted until the crew action has committed. The crew action is played with plain statements so that the moment is exact.

    tools/run-migration-test.sh -- /l3mon_tests/l3mon_scoring/test_freeze_mariadb.py
"""
import os
import threading
import time

import pytest

from CTFd.models import Solves, db
from CTFd.plugins.dynamic_challenges import DynamicChallenge, DynamicValueChallenge
from CTFd.plugins.l3mon_scoring import freeze, values
from scoring_world import dynamic, make_app, solve, stored_value, studio
from tests.helpers import destroy_ctfd

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")


@pytest.mark.parametrize("lock", [True, False], ids=["locked (the plugin's way)", "unlocked (proof that the race is real)"])
def test_ctfds_own_valuation_waits_for_a_crew_action_and_then_counts_fresh(lock):
    app = make_app()
    with app.app_context():
        dyn = dynamic("dyn")
        for minute in range(2):
            solve(studio(f"t{minute}"), dyn, minutes=minute)
        cid = dyn.id
        values.recalculate()
        db.session.commit()
        assert stored_value(cid) == 499, "two studios hold a solve"
        solve(studio("t2"), dyn, minutes=2)  # the player's flag, committed; CTFd is about to value the challenge (the stored value is still 499)
        a_holds_it = threading.Event()
        box = {}

        def crew_action():
            with app.app_context():
                connection = db.engine.connect()
                transaction = connection.begin()
                try:
                    connection.execute(db.text("SELECT id FROM challenges WHERE id = :c FOR UPDATE"), {"c": cid})
                    connection.execute(db.text("DELETE FROM solves WHERE challenge_id = :c"), {"c": cid})
                    connection.execute(db.text("UPDATE challenges SET value = 500 WHERE id = :c"), {"c": cid})
                    a_holds_it.set()
                    time.sleep(2)
                    transaction.commit()
                    box["a"] = "ok"
                except Exception as error:  # noqa: BLE001
                    transaction.rollback()
                    box["a"] = repr(error)[:300]
                finally:
                    connection.close()

        def a_players_flag_has_just_been_recorded():
            with app.app_context():
                try:
                    freeze.ON["lock"] = lock
                    challenge = DynamicChallenge.query.get(cid)
                    a_holds_it.wait(10)
                    DynamicValueChallenge.calculate_value(challenge)  # what CTFd runs right after a solve commits (wrapped by the plugin)
                    box["b"] = "ok"
                except Exception as error:  # noqa: BLE001
                    db.session.rollback()
                    box["b"] = repr(error)[:300]
                finally:
                    freeze.ON["lock"] = True

        threads = [threading.Thread(target=crew_action), threading.Thread(target=a_players_flag_has_just_been_recorded)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(70)
        assert box == {"a": "ok", "b": "ok"}, box
        db.session.rollback()
        assert Solves.query.filter_by(challenge_id=cid).count() == 0
        if lock:
            assert stored_value(cid) == 500, "nothing was counted until the crew action had committed: nobody holds a solve"
        else:
            assert stored_value(cid) == 495, "without the lock CTFd wrote the three-studio value it had counted before the Revoke committed"
    destroy_ctfd(app)
