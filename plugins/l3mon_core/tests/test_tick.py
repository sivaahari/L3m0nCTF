"""The tick: one number that changes whenever something a player can see may have changed.

Clients only compare it for difference, so what matters is that it never repeats a number a client may still hold (hence the
random start) and that every change a player can see makes it different, including a change of phase by the clock alone.
Real Redis, through the same Flask-Caching class CTFd uses, is tested when L3MON_TEST_REDIS_URL is set
(tools/run-migration-test.sh does that).

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import calendar
import os
import threading

import pytest
from cachelib import SimpleCache
from freezegun import freeze_time

from CTFd.models import Challenges, Fails, Notifications, Solves, Teams, Tracking, Users, db
from CTFd.plugins.l3mon_core import tick as tick_module
from CTFd.plugins.l3mon_core.models import Channel
from CTFd.plugins.l3mon_core.tick import KEY, Tick, add_signature_part, install, signature, tick
from CTFd.utils import get_config, set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_fail, gen_solve, gen_team, gen_user, login_as_user

START = calendar.timegm((2026, 11, 28, 3, 30, 0))
END = START + 24 * 3600


# ---- the counter itself, on any cachelib backend ----

def test_the_first_value_is_a_random_big_number_and_it_stays_until_a_bump():
    backend = SimpleCache(default_timeout=0)
    counter = Tick(lambda: backend)
    first = counter.value()
    assert first >= 10**9
    assert counter.value() == first == counter.value()
    assert counter.bump() == first + 1
    assert counter.value() == first + 1


def test_two_counters_do_not_start_at_the_same_number():
    starts = {Tick(lambda b=SimpleCache(default_timeout=0): b).value() for _ in range(20)}
    assert len(starts) > 15


def test_a_flushed_cache_gives_a_fresh_start_not_a_repeat_of_the_old_number():
    backend = SimpleCache(default_timeout=0)
    counter = Tick(lambda: backend)
    seen = {counter.bump() for _ in range(5)}
    backend.clear()
    again = counter.value()
    assert again not in seen and again != max(seen) + 1, "a client holding any old number would see a change"


def test_a_missing_key_is_created_by_bump_too():
    backend = SimpleCache(default_timeout=0)
    counter = Tick(lambda: backend)
    assert counter.bump() == counter.value()
    assert counter.value() >= 10**9 + 1


class FlushOnce(SimpleCache):
    """A cache that loses the key between value() and the increment, once (a flush, an eviction, an expiry)."""

    armed = False

    def inc(self, key, delta=1):
        if self.armed:
            self.armed = False
            self.delete(key)
        return super().inc(key, delta)


def test_a_key_lost_between_the_read_and_the_increment_never_brings_back_a_small_number():
    backend = FlushOnce(default_timeout=0)
    counter = Tick(lambda: backend)
    before = counter.value()
    backend.armed = True
    moved = counter.bump()
    assert moved >= 10**9, "an INCR on a missing key makes a 1, which a client could already hold"
    assert moved != before and counter.value() == moved


class PoisonOnce(SimpleCache):
    """A cache whose first increment fails, as Redis does when something stored a pickle under the key."""

    armed = False

    def inc(self, key, delta=1):
        if self.armed:
            self.armed = False
            raise ValueError("value is not an integer or out of range")
        return super().inc(key, delta)


def test_a_key_the_cache_cannot_increment_is_thrown_away_and_started_again():
    backend = PoisonOnce(default_timeout=0)
    counter = Tick(lambda: backend)
    before = counter.value()
    backend.armed = True
    moved = counter.bump()
    assert moved >= 10**9 and moved != before, "it repaired itself instead of failing for ever"
    assert counter.bump() == moved + 1


# ---- the real Redis, through the Flask-Caching class CTFd uses, with its key prefix ----

def real_backend():
    from flask_caching.backends.rediscache import RedisCache
    from redis import Redis

    client = Redis.from_url(os.environ["L3MON_TEST_REDIS_URL"])
    client.flushdb()
    return client, RedisCache(host=client, key_prefix="flask_cache_", default_timeout=300)


needs_redis = pytest.mark.skipif(not os.getenv("L3MON_TEST_REDIS_URL"), reason="needs a real Redis (tools/run-migration-test.sh)")


@needs_redis
def test_fifty_threads_on_a_real_redis_add_exactly_fifty_and_the_key_never_expires():
    client, backend = real_backend()
    counter = Tick(lambda: backend)
    start = counter.value()
    threads = [threading.Thread(target=counter.bump) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter.value() == start + 50
    assert client.ttl("flask_cache_" + KEY) == -1, "no expiry: a quiet five minutes must not look like a change"


@needs_redis
def test_a_flush_on_a_real_redis_gives_a_fresh_random_start():
    client, backend = real_backend()
    counter = Tick(lambda: backend)
    old = counter.bump()
    backend.clear()  # what CTFd does on an import or a reset
    assert counter.value() != old and counter.value() >= 10**9


@needs_redis
def test_many_workers_meeting_a_missing_key_all_get_a_big_number_and_agree_afterwards():
    client, backend = real_backend()
    counter = Tick(lambda: backend)
    seen, barrier = [], threading.Barrier(20)

    def worker():
        barrier.wait()
        seen.append(counter.value())

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 20 and min(seen) >= 10**9
    assert len({counter.value() for _ in range(5)}) == 1


@needs_redis
def test_a_pickle_stored_under_the_key_on_a_real_redis_is_repaired_by_the_next_bump():
    client, backend = real_backend()
    counter = Tick(lambda: backend)
    backend.set(KEY, 5)  # cachelib 0.9 stores this as a pickle, which INCR cannot touch
    moved = counter.bump()
    assert moved >= 10**9
    assert counter.bump() == moved + 1


# ---- the listeners: one bump for each committed change a player can see ----

def with_app(fn, user_mode="teams"):
    app = create_ctfd(enable_plugins=True, user_mode=user_mode)
    try:
        with app.app_context():
            fn(app)
    finally:
        destroy_ctfd(app)


def test_a_solve_a_challenge_and_a_setting_each_move_the_tick_once():
    def run(app):
        v = tick.value()
        chal = gen_challenge(db)
        assert tick.value() == v + 1, "a new challenge"
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        v = tick.value()
        gen_solve(db, user_id=team.members[0].id, team_id=team.id, challenge_id=chal.id)
        assert tick.value() == v + 1, "a solve"
        set_config("start", "1795836600")
        assert tick.value() == v + 2, "a setting (the start, the end, paused, the freeze and ours all live here)"

    with_app(run)


def test_what_players_see_beyond_the_scores_moves_it_too_the_bell_and_our_own_tables():
    def run(app):
        v = tick.value()
        db.session.add(Notifications(title="New on air", content="CH 4 has 1 new programme."))
        db.session.commit()
        assert tick.value() == v + 1, "a public notification (the bell)"
        db.session.add(Channel(slug="street", name="Street"))
        db.session.commit()
        assert tick.value() == v + 2, "a channel (our own table; a programme, a void, a bonus and a note do the same)"

    with_app(run)


def test_two_changes_in_one_commit_move_it_once():
    def run(app):
        first = gen_challenge(db, name="first")
        second = gen_challenge(db, name="second")
        user = gen_user(db, name="u", email="u@e.com")
        v = tick.value()
        db.session.add(Solves(user_id=user.id, team_id=None, challenge_id=first.id, ip="127.0.0.1", provided="x"))
        db.session.add(Solves(user_id=user.id, team_id=None, challenge_id=second.id, ip="127.0.0.1", provided="y"))
        db.session.commit()
        assert tick.value() == v + 1

    with_app(run)


def test_a_rollback_a_read_a_wrong_flag_and_a_tracking_row_move_nothing():
    def run(app):
        chal = gen_challenge(db)
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        chal_id, team_id, user_id = chal.id, team.id, team.members[0].id  # a request ends the session, so keep plain numbers
        v = tick.value()
        db.session.add(Solves(user_id=user_id, team_id=team_id, challenge_id=chal_id, ip="127.0.0.1", provided="x"))
        db.session.flush()
        db.session.rollback()
        assert tick.value() == v, "a rollback"
        Teams.query.all(), db.session.query(Solves).count()
        with app.test_client() as client:
            client.get("/login")
        assert tick.value() == v, "reads"
        gen_fail(db, user_id=user_id, team_id=team_id, challenge_id=chal_id)
        assert Fails.query.count() == 1 and tick.value() == v, "a wrong flag changes nothing a player sees"
        db.session.add(Tracking(ip="127.0.0.1", user_id=user_id))
        db.session.commit()
        assert tick.value() == v, "a tracking row"
        # and after all that, a rollback must not leave the flag behind for the next commit to pick up
        db.session.add(Solves(user_id=user_id, team_id=team_id, challenge_id=chal_id, ip="127.0.0.1", provided="z"))
        db.session.flush()
        db.session.rollback()
        gen_fail(db, user_id=user_id, team_id=team_id, challenge_id=chal_id)
        assert tick.value() == v, "the rolled-back solve left nothing behind"

    with_app(run)


def test_a_rolled_back_savepoint_does_not_wipe_the_note_of_an_earlier_change_in_the_same_commit():
    def run(app):
        chal = gen_challenge(db)
        user = gen_user(db, name="u", email="u@e.com")
        chal_id, user_id = chal.id, user.id
        v = tick.value()
        db.session.add(Solves(user_id=user_id, team_id=None, challenge_id=chal_id, ip="127.0.0.1", provided="x"))
        db.session.flush()
        savepoint = db.session.begin_nested()
        db.session.add(Tracking(ip="127.0.0.1", user_id=user_id))
        db.session.flush()
        savepoint.rollback()
        db.session.commit()
        assert Solves.query.count() == 1
        assert tick.value() == v + 1, "the solve was committed, so the tick moves"

    with_app(run)


def test_deletes_and_updates_that_skip_the_orm_events_move_the_tick_too():
    def run(app):
        chal = gen_challenge(db)
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        chal_id, team_id = chal.id, team.id
        v = tick.value()
        Teams.query.filter_by(id=team_id).update({"banned": True})  # a bulk update: no flush event
        db.session.commit()
        assert tick.value() == v + 1, "a bulk update of a team (a ban)"
        Challenges.query.filter_by(id=chal_id).delete()  # a bulk delete
        db.session.commit()
        assert tick.value() == v + 2, "a bulk delete of a challenge"
        Tracking.query.delete()
        db.session.commit()
        assert tick.value() == v + 2, "a bulk delete of something no player sees changes nothing"

    with_app(run)


def test_an_admins_delete_of_a_challenge_or_a_user_through_the_api_moves_the_tick():
    def run(app):
        chal = gen_challenge(db)
        user = gen_user(db, name="victim", email="v@e.com")
        gen_solve(db, user_id=user.id, team_id=None, challenge_id=chal.id)
        chal_id, user_id = chal.id, user.id
        v = tick.value()
        with login_as_user(app, name="admin") as admin:
            assert admin.delete(f"/api/v1/challenges/{chal_id}", json="").status_code == 200
        assert tick.value() != v, "the challenge is gone for every player (the tick moves when the request ends)"
        v = tick.value()
        with login_as_user(app, name="admin") as admin:
            assert admin.delete(f"/api/v1/users/{user_id}", json="").status_code == 200
        assert tick.value() != v, "the user, and with them their solves, are gone"
        assert Challenges.query.filter_by(id=chal_id).count() == 0 and Users.query.filter_by(id=user_id).count() == 0

    with_app(run, user_mode="users")


def test_inside_a_request_the_tick_moves_when_the_request_ends_after_ctfd_has_cleared_its_own_caches(monkeypatch):
    def run(app):
        saw = []
        real = tick.bump

        def spy():
            saw.append(get_config("start"))  # what a board request would read the moment the tick moves
            return real()

        monkeypatch.setattr(tick_module.tick, "bump", spy)
        with login_as_user(app, name="admin") as admin:
            r = admin.patch("/api/v1/configs", json={"start": "2000"})
            assert r.status_code == 200
        assert saw and all(value == 2000 for value in saw), f"the tick moved while CTFd still served the old setting: {saw}"

    with_app(run)


def test_outside_a_request_it_moves_at_once():
    def run(app):
        v = tick.value()
        gen_challenge(db)
        assert tick.value() == v + 1

    with_app(run)


def test_installing_again_and_creating_more_apps_never_doubles_the_bump():
    def run(app):
        install(app)
        install(app)
        other = create_ctfd(enable_plugins=True, user_mode="teams")
        try:
            with other.app_context():
                v = tick.value()
                gen_challenge(db, name="in the other app")
                assert tick.value() == v + 1
        finally:
            destroy_ctfd(other)

    with_app(run)


def test_a_counter_that_fails_never_breaks_the_commit(monkeypatch):
    def run(app):
        def boom():
            raise RuntimeError("the cache is down")

        monkeypatch.setattr(tick_module.tick, "bump", boom)
        chal = gen_challenge(db)  # commits through the listener
        assert db.session.get(type(chal), chal.id) is not None, "the challenge is saved although the tick could not move"

    with_app(run)


# ---- the number players poll: the counter and the phase, so a change of phase by the clock alone is a change ----

def test_the_signature_changes_when_the_phase_changes_by_the_clock_alone():
    def run(app):
        set_config("start", str(START))
        set_config("end", str(END))
        set_config("freeze", str(END - 3600))
        instants = [START - 1, START + 1, END - 3601, END - 3600, END]  # before, live, live (a second before the freeze), live and frozen, ended and frozen
        seen = [signature(t) for t in instants]
        assert len({s.split(".")[0] for s in seen}) == 1, "nothing was committed between these instants, so the counter did not move"
        assert [s.split(".")[1] for s in seen] == ["bn", "ln", "ln", "lf", "ef"]
        assert len(set(seen)) == 4, "four different numbers for the four different pictures (the second and third are the same picture)"
        assert signature(START - 1) == seen[0], "and the same instant gives the same signature"
        with freeze_time("2026-11-28 03:30:01"):
            assert signature().split(".")[1] == "ln", "now is read from the clock when no second is given"

    with_app(run)


def test_a_signature_part_from_a_later_plugin_changes_it_and_a_commit_changes_it():
    def run(app):
        state = {"n": 1}
        part = lambda: state["n"]  # noqa: E731
        base = signature()
        add_signature_part(part)
        add_signature_part(part)  # twice is once
        with_part = signature()
        assert with_part == base + ".1" and with_part != base
        state["n"] = 2
        assert signature() == base + ".2"
        gen_challenge(db)
        assert signature().split(".")[0] != base.split(".")[0]
        tick_module._parts.remove(part)

    with_app(run)
