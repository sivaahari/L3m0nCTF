"""The scheduler: a drop happens on time, from the first request at or after its second, and costs nothing until then.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_release
"""
import calendar
import datetime
import logging
import time
from unittest import mock

from freezegun import freeze_time
from sqlalchemy import event

from CTFd.cache import cache
from CTFd.models import Challenges, Notifications, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_release import scheduler
from CTFd.plugins.l3mon_release.reconcile import RECLEAR_KEY, RECLEAR_SECONDS, clear_pull_back_handlers, reclear, reconcile
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user, register_user


def at(hour, minute=0):
    return datetime.datetime(2026, 11, 28, hour, minute, 0)


def epoch(moment):
    return calendar.timegm(moment.timetuple())


T_START = epoch(at(3, 30))
T_LATER = epoch(at(5, 0))
T_END = T_START + 24 * 3600


def clock(t):
    return freeze_time(datetime.datetime.utcfromtimestamp(t))


def channel(slug, position, state="released", when=None):
    row = Channel(slug=slug, name=slug.title(), position=position, release_state=state, release_at=when)
    db.session.add(row)
    db.session.commit()
    return row


def programme(ch, name, state="released", when=None):
    chal = gen_challenge(db, name=name, state="hidden")
    n = Programme.query.count()
    db.session.add(Programme(challenge_id=chal.id, channel_id=ch.id, cell=n, number=n + 1, slug=name.lower(), release_state=state, release_at=when))
    db.session.commit()
    return chal.id


def states():
    return {c.name: c.state for c in Challenges.query.order_by(Challenges.id)}


def started():
    set_config("start", T_START)
    set_config("end", T_END)


def setup_app():
    clear_pull_back_handlers()
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        cache.delete(scheduler.KEY)
    return app


def test_the_next_moment_is_the_earliest_scheduled_second_still_to_come_or_the_end():
    app = setup_app()
    with app.app_context():
        assert scheduler.next_event(T_START) == 0, "no plan, nothing to wait for"
        started()
        late = channel("late", 2, state="scheduled", when=at(7, 0))
        early = channel("early", 1)
        programme(early, "soon", "scheduled", at(5, 0))
        programme(early, "sooner", "scheduled", at(4, 0))
        programme(early, "withheld-with-a-time", "withheld", at(3, 45))  # a withheld entry never opens by itself
        programme(early, "released-with-a-time", "released", at(3, 50))  # a released one is already on
        programme(late, "other", "released")
        assert scheduler.next_event(T_START) == epoch(at(4, 0))
        assert scheduler.next_event(epoch(at(4, 0))) == epoch(at(5, 0)), "a second that has come is no longer to come"
        assert scheduler.next_event(epoch(at(5, 0))) == epoch(at(7, 0))
        assert scheduler.next_event(epoch(at(7, 0))) == T_END, "after the last drop only the end is left"
        assert scheduler.next_event(T_END) == 0
    destroy_ctfd(app)


def test_the_record_is_kept_for_a_short_time_only_so_a_stale_one_mends_itself_quickly():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        with mock.patch.object(scheduler.cache, "set") as spy:
            scheduler.refresh(T_START)
        assert spy.call_args.kwargs["timeout"] == scheduler.RECORD_SECONDS <= 30
    destroy_ctfd(app)


def test_refresh_remembers_the_next_moment_and_the_end_it_was_worked_out_for():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        scheduler.refresh(T_START)
        assert cache.get(scheduler.KEY) == {"next": epoch(at(5, 0)), "end": T_END}
    destroy_ctfd(app)


def test_nothing_touches_the_database_until_a_moment_has_come():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        scheduler.maybe_apply(T_START + 10)  # the first look works the cache out
        statements = []

        def count(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", count)
        try:
            for t in (T_START + 20, T_START + 600, T_LATER - 1):
                assert scheduler.maybe_apply(t) is False
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
        assert statements == [], statements
    destroy_ctfd(app)


def test_the_first_request_at_or_after_the_second_shows_the_programme_announces_it_once_and_the_next_does_nothing():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 4)
        programme(ch, "later", "scheduled", at(5, 0))
        programme(ch, "now", "released")
        reconcile(T_START + 10)
        assert states() == {"later": "hidden", "now": "visible"}
        assert Notifications.query.count() == 1, "the programme that was released at the start was announced then"
        scheduler.refresh(T_START + 10)
        assert scheduler.maybe_apply(T_LATER - 1) is False and states()["later"] == "hidden"
        assert scheduler.maybe_apply(T_LATER) is True
        assert states()["later"] == "visible" and Notifications.query.count() == 2
        assert scheduler.maybe_apply(T_LATER + 1) is False and Notifications.query.count() == 2
        assert cache.get(scheduler.KEY)["next"] == T_END, "and it already knows what comes after"
    destroy_ctfd(app)


def test_a_change_clears_the_cached_lists_again_two_seconds_later_so_a_list_stored_late_cannot_stay():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 4)
        programme(ch, "later", "scheduled", at(5, 0))
        reconcile(T_START + 10)
        scheduler.refresh(T_START + 10)
        cache.delete(RECLEAR_KEY)
        with mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_challenges") as challenges, mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_standings") as standings:
            assert scheduler.maybe_apply(T_LATER) is True
            assert (challenges.call_count, standings.call_count) == (1, 1), "the drop clears the lists"
            assert RECLEAR_SECONDS == 2 and cache.get(RECLEAR_KEY) == T_LATER + 2, "two seconds after the change, as a whole second"
            assert scheduler.maybe_apply(T_LATER + RECLEAR_SECONDS - 0.01) is False and challenges.call_count == 1, "not before the moment"
            assert scheduler.maybe_apply(T_LATER + RECLEAR_SECONDS) is False, "no reconcile is needed for it"
            assert (challenges.call_count, standings.call_count) == (2, 2), "and once more at the moment"
            assert cache.get(RECLEAR_KEY) is None
            assert scheduler.maybe_apply(T_LATER + RECLEAR_SECONDS + 1) is False and challenges.call_count == 2, "then not again"
    destroy_ctfd(app)


def test_the_moment_is_a_whole_second_so_no_request_before_it_does_any_work():
    """A fractional second used to be stored as it was and published as its whole part, so every request in the last second before it
    found the moment due, took the plan's lock and ran a reconcile that could not clear anything yet (found by the independent review)."""
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 4)
        programme(ch, "now", "released")
        with mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_challenges"), mock.patch.object(scheduler, "reconcile", wraps=reconcile) as ran:
            reconcile(T_START + 10.9)
            assert cache.get(RECLEAR_KEY) == T_START + 11 + RECLEAR_SECONDS, "rounded up to a whole second"
            scheduler.refresh(T_START + 10.9)
            ran.reset_mock()
            for step in range(20):
                assert scheduler.maybe_apply(T_START + 11 + RECLEAR_SECONDS - 1 + step * 0.04) is False
            assert ran.call_count == 0, "nothing in the last second before the moment takes the lock"
    destroy_ctfd(app)


def test_workers_that_reach_the_moment_together_clear_once():
    """Two workers that both read the key before either deleted it: only the one whose delete finds it does the clearing."""
    app = setup_app()
    with app.app_context():
        started()
        with mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_challenges") as challenges:
            cache.set(RECLEAR_KEY, T_START + 50, timeout=30)
            assert reclear() is True and reclear() is False
            assert challenges.call_count == 1
            cache.set(RECLEAR_KEY, T_START + 50, timeout=30)
            scheduler.maybe_apply(T_START + 50)
            scheduler.maybe_apply(T_START + 50)
            scheduler.maybe_apply(T_START + 50.2)
            assert challenges.call_count == 2
    destroy_ctfd(app)


def test_a_pull_back_arranges_the_second_clearing_too_and_a_call_that_changes_nothing_does_not():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 4)
        row = programme(ch, "live", "released")
        reconcile(T_START + 10)
        cache.delete(RECLEAR_KEY)
        reconcile(T_START + 20)
        assert cache.get(RECLEAR_KEY) is None, "nothing changed, nothing arranged"
        Programme.query.filter_by(challenge_id=row).update({"release_state": "withheld"})
        db.session.commit()
        reconcile(T_START + 30)
        assert cache.get(RECLEAR_KEY) == T_START + 30 + RECLEAR_SECONDS, "the pull-back clears and arranges its own second clearing"
    destroy_ctfd(app)


def test_a_real_request_after_the_second_already_sees_the_programme():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        with clock(T_START + 30):
            register_user(app)
            player = login_as_user(app)
            reconcile(T_START + 30)
            scheduler.refresh(T_START + 30)
            assert [c["name"] for c in player.get("/api/v1/challenges").get_json()["data"]] == []
        with clock(T_LATER + 2):
            names = [c["name"] for c in player.get("/api/v1/challenges").get_json()["data"]]
        assert names == ["later"], "the request that finds the drop due applies it before it answers"
        assert states() == {"later": "visible"}
    destroy_ctfd(app)


def test_a_drop_found_by_a_players_request_is_recorded_as_the_systems_never_as_the_players():
    from CTFd.plugins.l3mon_core.models import Audit

    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        with clock(T_START + 30):
            register_user(app, name="curious", email="curious@example.com")
            player = login_as_user(app, "curious")
            reconcile(T_START + 30)
            scheduler.refresh(T_START + 30)
        with clock(T_LATER + 2):
            assert player.get("/api/v1/challenges").status_code == 200  # this request finds the drop due
        line = Audit.query.filter_by(action="release.drop").one()
        assert line.actor_name == "system" and line.actor_id is None, "the clock made the drop; the player only happened to ask first"
    destroy_ctfd(app)


def test_after_a_restart_the_first_request_works_out_what_is_due_and_applies_it():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "on", "released")
        programme(ch, "later", "scheduled", at(5, 0))
        assert states() == {"on": "hidden", "later": "hidden"}, "nobody has reconciled yet"
        cache.delete(scheduler.KEY)
        assert scheduler.maybe_apply(T_LATER + 5) is True
        assert states() == {"on": "visible", "later": "visible"}
        assert cache.get(scheduler.KEY) is not None
    destroy_ctfd(app)


def test_moving_the_end_is_noticed_without_any_scheduled_drop():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "on", "released")
        programme(ch, "never", "withheld")
        reconcile(T_END - 5)
        scheduler.refresh(T_END - 5)
        assert scheduler.maybe_apply(T_END) is True and states()["never"] == "visible", "the end second shows everything"
        set_config("end", T_END + 7200)
        assert scheduler.maybe_apply(T_END + 10) is True and states()["never"] == "hidden", "a later end is noticed on the next request"
    destroy_ctfd(app)


def test_the_health_routes_and_the_static_files_never_trigger_it_and_a_normal_page_does():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        scheduler.refresh(T_START + 10)
        with mock.patch("CTFd.plugins.l3mon_release.scheduler.reconcile") as called, clock(T_LATER + 5):
            client = app.test_client()
            for path in ("/healthcheck", "/l3mon/healthz", "/themes/core/static/css/main.dev.css", "/plugins/challenges/assets/view.js"):
                client.get(path)
            assert called.call_count == 0, "the page that must answer when everything else struggles never waits on the plan"
            client.get("/login")
            assert called.call_count == 1
    destroy_ctfd(app)


def test_a_failing_reconcile_never_breaks_the_page_and_is_tried_again_in_a_few_seconds():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        scheduler.refresh(T_START + 10)
        seen = []

        class Lines(logging.Handler):
            def emit(self, record):
                seen.append(self.format(record))

        logging.getLogger("l3mon").addHandler(Lines(logging.WARNING))
        with mock.patch("CTFd.plugins.l3mon_release.scheduler.reconcile", side_effect=RuntimeError("the database blinked")), clock(T_LATER + 5):
            r = app.test_client().get("/login")
            assert r.status_code == 200, "the page still answers"
        assert any("the database blinked" in line for line in seen)
        retry = cache.get(scheduler.KEY)
        assert T_LATER + 5 < retry["next"] <= T_LATER + 20, "it tries again in a few seconds, not on every request"
    destroy_ctfd(app)


# ---- a crowd at the moment (SP3 part 3.7: found by the load probe) ------------------------------------------------------------------------------------
# The record lasts 30 seconds. When it ends (or a drop is due) every request that arrives meanwhile finds no record, and each of them used to run a whole
# reconcile one after the other behind the plan's row lock: on the load probe 100 players waited up to 4.4 seconds for it, every 30 seconds. One request
# does the work; the others carry on at once (the pages decide what is on air from the plan and the clock, so they do not need the result).

def test_a_request_that_arrives_while_another_is_applying_what_is_due_carries_on_and_does_no_second_reconcile():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        scheduler.refresh(T_START + 10)
        real = scheduler.reconcile
        calls, nested = [], []

        def slow(t, system=False, locked=False):
            calls.append(t)
            if len(calls) == 1:  # a second request reaches the scheduler while the first one is still inside its reconcile
                nested.append(scheduler.maybe_apply(t))
            return real(t, system=system, locked=locked)

        with mock.patch("CTFd.plugins.l3mon_release.scheduler.reconcile", side_effect=slow):
            assert scheduler.maybe_apply(T_LATER) is True
        assert nested == [False], "the second request did not wait for the first and did not run its own"
        assert len(calls) == 1
        assert states()["later"] == "visible"
    destroy_ctfd(app)


def test_the_flag_that_keeps_the_others_away_is_let_go_afterwards_and_after_a_failure_too():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        programme(ch, "last", "scheduled", at(6, 0))
        scheduler.refresh(T_START + 10)
        with mock.patch("CTFd.plugins.l3mon_release.scheduler.reconcile", side_effect=RuntimeError("the database blinked")):
            assert scheduler.maybe_apply(T_LATER) is False
        assert cache.get(scheduler.RUN_KEY) is None, "a failure lets go of it"
        cache.delete(scheduler.KEY)
        assert scheduler.maybe_apply(T_LATER) is True
        assert cache.get(scheduler.RUN_KEY) is None, "and so does a success"
        assert scheduler.maybe_apply(epoch(at(6, 0))) is True and states()["last"] == "visible", "the next moment is applied as well"
    destroy_ctfd(app)


def test_a_flag_left_behind_by_a_worker_that_died_ends_by_itself_within_a_few_seconds():
    assert 0 < scheduler.RUN_SECONDS <= scheduler.RETRY_SECONDS, "a crashed worker must not hold the others off for long"
    app = setup_app()
    with app.app_context():
        with mock.patch.object(scheduler.cache, "add", wraps=scheduler.cache.add) as spy:
            started()
            ch = channel("street", 1)
            programme(ch, "later", "scheduled", at(5, 0))
            scheduler.refresh(T_START + 10)
            scheduler.maybe_apply(T_LATER)
        runs = [c for c in spy.call_args_list if c.args and c.args[0] == scheduler.RUN_KEY]
        assert len(runs) == 1 and runs[0].kwargs["timeout"] == scheduler.RUN_SECONDS
    destroy_ctfd(app)


def test_a_running_flag_that_lost_its_lifetime_does_not_stop_the_scheduler_for_good():
    """The audit of 3.7 reproduced it: cachelib's Redis `add` is SETNX then EXPIRE, and a flag left without a lifetime held every later drop off for ever
    (and with Redis's volatile-lru policy it is not even evicted). The flag holds the time it was taken; one older than its lifetime is free."""
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        scheduler.refresh(T_START + 10)
        cache.set(scheduler.RUN_KEY, time.time() - 3600, timeout=0)
        assert scheduler.maybe_apply(T_LATER) is True and states()["later"] == "visible"
        assert cache.get(scheduler.RUN_KEY) is None
    destroy_ctfd(app)


def test_a_cache_that_fails_right_after_the_commit_does_not_lose_the_announcement_the_tick_or_the_record():
    """Found by the audit of 3.7: the first clearing of the cached lists ran outside any try, so a cache error there skipped the tick, the pull-back
    handlers, the announcement and the record, and the next reconcile found nothing left to claim: the announcement was lost for good."""
    from CTFd.plugins.l3mon_core.tick import signature

    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "later", "scheduled", at(5, 0))
        scheduler.refresh(T_START + 10)
        before = signature()
        with mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_challenges", side_effect=RuntimeError("the cache blinked")):
            result = reconcile(T_LATER, system=True)
        assert result.shown, "the change itself was made"
        assert states()["later"] == "visible"
        assert Notifications.query.count() == 1, "and announced"
        assert signature() != before, "and the tick moved"
        assert cache.get(scheduler.KEY) is not None, "and the scheduler's record was refreshed"
    destroy_ctfd(app)
