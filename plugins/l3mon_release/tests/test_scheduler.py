"""The scheduler: a drop happens on time, from the first request at or after its second, and costs nothing until then.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_release
"""
import calendar
import datetime
import logging
from unittest import mock

from freezegun import freeze_time
from sqlalchemy import event

from CTFd.cache import cache
from CTFd.models import Challenges, Notifications, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_release import scheduler
from CTFd.plugins.l3mon_release.reconcile import RECLEAR_KEY, RECLEAR_SECONDS, clear_pull_back_handlers, reconcile
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
        assert scheduler.maybe_apply(T_START + 10 + RECLEAR_SECONDS) is True, "the second clearing of the lists after the first release comes first"
        assert scheduler.maybe_apply(T_LATER - 1) is False and states()["later"] == "hidden"
        assert scheduler.maybe_apply(T_LATER) is True
        assert states()["later"] == "visible" and Notifications.query.count() == 2
        assert scheduler.maybe_apply(T_LATER + 1) is False and Notifications.query.count() == 2
        assert cache.get(scheduler.KEY)["next"] == T_LATER + RECLEAR_SECONDS, "after a change the next moment is the second clearing of the lists"
        assert scheduler.maybe_apply(T_LATER + RECLEAR_SECONDS) is True and cache.get(scheduler.KEY)["next"] == T_END, "and then what comes after"
    destroy_ctfd(app)


def test_a_change_clears_the_cached_lists_again_a_few_seconds_later_so_a_list_stored_late_cannot_stay():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 4)
        programme(ch, "later", "scheduled", at(5, 0))
        reconcile(T_START + 10)
        scheduler.refresh(T_START + 10)
        with mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_challenges") as challenges, mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_standings") as standings:
            assert scheduler.maybe_apply(T_LATER) is True
            assert (challenges.call_count, standings.call_count) == (1, 1), "the drop clears the lists"
            assert cache.get(RECLEAR_KEY) == T_LATER + RECLEAR_SECONDS
            assert scheduler.maybe_apply(T_LATER + RECLEAR_SECONDS - 1) is False and challenges.call_count == 1, "not before the moment"
            assert scheduler.maybe_apply(T_LATER + RECLEAR_SECONDS) is True
            assert (challenges.call_count, standings.call_count) == (2, 2), "and once more at the moment"
            assert scheduler.maybe_apply(T_LATER + RECLEAR_SECONDS + 1) is False and challenges.call_count == 2, "then not again"
    destroy_ctfd(app)


def test_a_pull_back_arranges_the_second_clearing_too_and_a_call_that_changes_nothing_clears_only_when_it_is_due():
    app = setup_app()
    with app.app_context():
        started()
        ch = channel("street", 4)
        row = programme(ch, "live", "released")
        with mock.patch("CTFd.plugins.l3mon_release.reconcile.clear_challenges") as challenges:
            reconcile(T_START + 10)
            assert challenges.call_count == 1, "shown: cleared"
            reconcile(T_START + 11)
            assert challenges.call_count == 1, "nothing changed and the second clearing is not due yet"
            reconcile(T_START + 10 + RECLEAR_SECONDS)
            assert challenges.call_count == 2, "the second clearing"
            Programme.query.filter_by(challenge_id=row).update({"release_state": "withheld"})
            db.session.commit()
            reconcile(T_START + 30)
            assert challenges.call_count == 3 and cache.get(RECLEAR_KEY) == T_START + 30 + RECLEAR_SECONDS, "the pull-back clears and arranges its own second clearing"
            reconcile(T_START + 30 + RECLEAR_SECONDS - 1)
            assert challenges.call_count == 3
            reconcile(T_START + 30 + RECLEAR_SECONDS)
            assert challenges.call_count == 4
    destroy_ctfd(app)


def test_the_moment_of_the_second_clearing_is_one_of_the_moments_the_scheduler_waits_for():
    app = setup_app()
    with app.app_context():
        started()
        channel("street", 4)
        cache.set(RECLEAR_KEY, T_START + 50, timeout=30)
        assert scheduler.next_event(T_START + 10) == T_START + 50
        assert scheduler.next_event(T_START + 50) == T_END, "a moment that has come is not waited for again"
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
