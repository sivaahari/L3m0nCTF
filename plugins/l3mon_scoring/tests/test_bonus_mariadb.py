"""Eight clicks on "Give bonus" at the same instant, on a real MariaDB: one award, whether or not a channel exists yet.

The repeat check only works if the crew actions are serialised; the lock is a row of the lowest channel, and before any channel exists
it falls back to a settings row (found by the independent audit: with an empty channel table the lock was a gap lock, which does not
block another gap lock, and every one of eight identical bonuses was given).

    tools/run-migration-test.sh -- /l3mon_tests/l3mon_scoring/test_bonus_mariadb.py
"""
import os
import threading

import pytest

from CTFd.models import Awards, db
from CTFd.plugins.l3mon_core.models import Bonus, Channel, Note
from CTFd.plugins.l3mon_scoring import bonus
from CTFd.plugins.l3mon_scoring.errors import Refused
from scoring_world import make_app, studio
from tests.helpers import destroy_ctfd

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

WORKERS = 8


@pytest.mark.parametrize("with_channel", [False, True], ids=["no channel yet", "with a channel"])
def test_eight_identical_bonuses_at_once_give_one(with_channel):
    app = make_app()
    with app.app_context():
        team_id = studio("alpha").id
        if with_channel:
            db.session.add(Channel(slug="street", name="Street", position=1, release_state="withheld"))
            db.session.commit()
        for round_number in range(3):
            barrier, results, refusals, errors = threading.Barrier(WORKERS), [], [], []

            def worker():
                try:
                    with app.app_context():
                        barrier.wait()
                        results.append(bonus.give(team_id, trp=50 + round_number, message="Found a bug in the lobby"))
                except Refused as refused:
                    refusals.append(refused)
                except Exception as error:  # noqa: BLE001
                    errors.append(repr(error))

            threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"round {round_number}: {errors}"
            assert len(results) == 1 and len(refusals) == WORKERS - 1 and {r.status for r in refusals} == {409}, f"round {round_number}: {len(results)} given"
            db.session.rollback()
            assert Awards.query.count() == round_number + 1 and Bonus.query.count() == round_number + 1 and Note.query.count() == round_number + 1
    destroy_ctfd(app)
