"""Eight workers meeting one scheduled drop at the same instant, on a real MariaDB.

SQLite (the default test database) lets one writer in at a time, so it can never show the race. On MariaDB the claim is one
conditional UPDATE per challenge: whichever worker's update changes the row owns the change. The programmes must be shown once,
announced once, written to the audit trail once and reported once, and no worker may deadlock or fail.

    tools/run-migration-test.sh -- /l3mon_tests/l3mon_release/test_scheduler_mariadb.py
"""
import calendar
import datetime
import os
import threading
from unittest import mock

import pytest
from freezegun import freeze_time

from CTFd.cache import cache
from CTFd.models import Challenges, Notifications, db
from CTFd.plugins.l3mon_core.models import Audit, Channel, Programme
from CTFd.plugins.l3mon_release import scheduler
from CTFd.plugins.l3mon_release.reconcile import clear_pull_back_handlers, reconcile
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

DUE = datetime.datetime(2026, 11, 28, 5, 0, 0)
T_DUE = calendar.timegm(DUE.timetuple())
T_START = calendar.timegm(datetime.datetime(2026, 11, 28, 3, 30, 0).timetuple())
WORKERS = 8
PROGRAMMES = 6


def build_plan():
    set_config("start", T_START)
    set_config("end", T_START + 24 * 3600)
    ch = Channel(slug="street", name="Street", position=1, release_state="released")
    db.session.add(ch)
    db.session.commit()
    for n in range(PROGRAMMES):
        chal = gen_challenge(db, name=f"p{n}", state="hidden")
        db.session.add(Programme(challenge_id=chal.id, channel_id=ch.id, cell=n, number=n + 1, slug=f"p{n}", release_state="scheduled", release_at=DUE))
    db.session.commit()


def put_back_before_the_drop():
    Challenges.query.update({"state": "hidden"})
    Notifications.query.delete()
    Audit.query.delete()
    db.session.commit()
    cache.delete(scheduler.KEY)


def race(app, call):
    barrier, outcomes, errors = threading.Barrier(WORKERS), [], []

    def worker():
        try:
            with app.app_context():  # a worker of its own: its own application context, session and connection
                barrier.wait()
                outcomes.append(call())
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes, errors


@pytest.mark.parametrize("through", ["reconcile", "the_scheduler"])
def test_eight_workers_racing_one_drop_show_it_once_announce_it_once_and_none_fails(through):
    clear_pull_back_handlers()
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        build_plan()
        for round_number in range(5):
            put_back_before_the_drop()
            if through == "reconcile":
                outcomes, errors = race(app, lambda: reconcile(T_DUE))
                owners = [o for o in outcomes if o.shown]
                assert len(owners) == 1 and len(owners[0].shown) == PROGRAMMES, f"round {round_number}: exactly one worker owns the drop ({outcomes})"
            else:
                outcomes, errors = race(app, lambda: scheduler.maybe_apply(T_DUE))
            assert not errors, f"round {round_number}: {errors}"
            db.session.expire_all()
            assert {c.state for c in Challenges.query.all()} == {"visible"}, f"round {round_number}"
            assert Notifications.query.count() == 1, f"round {round_number}: announced once"
            assert Audit.query.filter_by(action="release.drop").count() == 1, f"round {round_number}: written to the audit trail once"
            assert "CH 1 · Street has 6 new programmes." == Notifications.query.one().content
    destroy_ctfd(app)


def test_a_worker_that_loses_the_claim_owns_nothing_and_says_nothing():
    clear_pull_back_handlers()
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        build_plan()
        first = reconcile(T_DUE)
        assert len(first.shown) == PROGRAMMES
        late, errors = race(app, lambda: reconcile(T_DUE + 1))
        assert not errors and all(not o.shown and not o.hidden for o in late)
        assert Notifications.query.count() == 1
    destroy_ctfd(app)


def test_a_worker_whose_view_of_the_database_is_older_than_a_drop_still_ends_consistent():
    """MariaDB's default isolation (REPEATABLE READ) gives a transaction the view it had at its first read. The crew's request
    starts that view (CTFd looks up the user), then waits; another worker reconciles the due drop and commits; then the crew's
    request withholds the same programme. Without serialising, its reconcile judged the programme 'hidden already' from the old
    view and skipped it, leaving CTFd showing a programme the plan says is withheld (found by the independent review)."""
    clear_pull_back_handlers()
    app = create_ctfd(enable_plugins=True)
    app.permanent_session_lifetime = datetime.timedelta(days=3650)
    with app.app_context(), freeze_time(datetime.datetime.utcfromtimestamp(T_DUE - 60)) as frozen:
        build_plan()
        admin = login_as_user(app, "admin")  # a minute before the drop: nothing is due yet
        frozen.move_to(datetime.datetime.utcfromtimestamp(T_DUE))
        target = Programme.query.filter_by(slug="p0").one()
        target_id, challenge_id = target.id, target.challenge_id
        cache.set(scheduler.KEY, {"next": T_DUE + 1000, "end": T_START + 24 * 3600})  # this worker has not noticed the drop is due
        a_waiting, b_done, outcome = threading.Event(), threading.Event(), {}

        def waits_for_the_other_worker():
            a_waiting.set()
            assert b_done.wait(60)
            return real_serialize()

        from CTFd.plugins.l3mon_release import reconcile as reconcile_module

        real_serialize = reconcile_module.serialize

        def crew_request():
            with app.app_context():
                outcome["a"] = admin.put("/api/v1/l3mon/admin/release", json={"changes": [{"kind": "programme", "id": target_id, "mode": "withhold"}], "reason": "stop it"})

        def other_worker():
            with app.app_context():
                assert a_waiting.wait(60)
                outcome["b"] = reconcile(T_DUE, system=True)
                b_done.set()

        with mock.patch("CTFd.plugins.l3mon_release.api.serialize", waits_for_the_other_worker):
            threads = [threading.Thread(target=crew_request), threading.Thread(target=other_worker)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(120)
        assert outcome["a"].status_code == 200, outcome["a"].get_data(as_text=True)
        assert len(outcome["b"].shown) == PROGRAMMES, "the other worker did put the due drop on air"
        db.session.rollback()  # end this thread's own old view of the database before looking
        assert Programme.query.get(target_id).release_state == "withheld"
        assert Challenges.query.get(challenge_id).state == "hidden", "CTFd must not show a programme the plan says is withheld"
        assert sum(1 for c in Challenges.query.all() if c.state == "visible") == PROGRAMMES - 1
    destroy_ctfd(app)

