"""Tests for the automatic opening time of the finale (SP6 / Stream E, E3).

The setting `l3mon_finale_at` holds a whole number of UTC epoch seconds.
Rules verified:
- Defensive parsing: unset, booleans, invalid strings, negative numbers, pre-2020, and absurdly large values return None.
- Never opens before the start of the event, regardless of what the setting says.
- Opens for every registered player/studio once the event has started and the clock reaches that time.
- Does not require the meter or a target to be set or full.
- Crew trigger (l3mon_finale_open) and the on-air meter continue to work.
- The 5-second studio cache does not hold a stale 'closed' once the time arrives.
- Zero extra database statements beyond the config read.
"""
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import event

from CTFd.models import db
from CTFd.plugins.l3mon_core import clock, settings
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.settings import finale_at
from CTFd.plugins.l3mon_story import finale, store
from CTFd.utils import set_config
from story_world import (
    STORY,
    T_LIVE,
    T_START,
    clock as freeze_clock,
    load_plan,
    make_app,
    release_street,
    started,
    team_client,
    write_pic_story,
)
from tests.helpers import destroy_ctfd, login_as_user

FINALE_AT = "l3mon_finale_at"
TARGET_TIME = T_LIVE + 1800  # 30 minutes into the live event


@pytest.fixture
def play(tmp_path, monkeypatch):
    folder = tmp_path / "story"
    folder.mkdir()
    monkeypatch.setenv("L3MON_STORY_DIR", str(folder))
    store.reset()
    write_pic_story(str(folder), "street", title="Picture Hour")
    finale_bundle, files = write_pic_story(str(folder), "finale", title="Last Light", kicker="LAST CALL", panels=3)
    app = make_app()
    with app.app_context():
        started()
        with freeze_clock(T_LIVE):
            admin = login_as_user(app, "admin")
            load_plan(admin)
            release_street(admin)
            alice = team_client(app, "alice", "studio-a")
            yield SimpleNamespace(app=app, admin=admin, alice=alice, visitor=app.test_client(), folder=str(folder), files=files)
    destroy_ctfd(app)
    store.reset()


# --------------------------------------------------------------------------------------------------
# Defensive Setting Parsing
# --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1795876200, 1795876200),
        ("1795876200", 1795876200),
        (" 1795876200 ", 1795876200),
        (None, None),
        ("", None),
        ("   ", None),
        (True, None),
        (False, None),
        ("true", None),
        ("false", None),
        ("tomorrow", None),
        ("12.34", None),
        (-100, None),
        (0, None),
        (1000, None),  # well before 2020
        (1577836799, None),  # 2019-12-31 23:59:59 (before 2020)
        (1577836800, 1577836800),  # 2020-01-01 00:00:00 (valid lower bound)
        (2500000000, 2500000000),  # year 2049 (valid upper bound)
        (2500000001, None),  # absurdly far in the future
        (99999999999, None),  # absurdly far in the future
    ],
)
def test_finale_at_defensive_parsing(app, value, expected):
    """The setting l3mon_finale_at is parsed defensively for odd and out-of-range values."""
    with app.app_context():
        set_config(FINALE_AT, value)
        assert finale_at() == expected


# --------------------------------------------------------------------------------------------------
# Phase and Clock Gate Verification
# --------------------------------------------------------------------------------------------------

def test_finale_never_opens_before_event_start_even_if_timestamp_reached(play):
    """Before the event start, the finale NEVER opens, even if l3mon_finale_at is in the past."""
    with play.app.app_context():
        set_config(FINALE_AT, T_START - 100)
        # Move clock to before start
        with freeze_clock(T_START - 500):
            assert current_phase().state == "before"
            r = play.alice.get(f"{STORY}/finale")
            assert r.status_code == 404
            assert play.alice.get("/story/finale").status_code == 404


def test_finale_remains_closed_after_start_before_timestamp_is_reached(play):
    """During live event, finale is closed before the configured timestamp arrives."""
    with play.app.app_context():
        set_config(FINALE_AT, TARGET_TIME)
        with freeze_clock(T_LIVE):  # T_LIVE is TARGET_TIME - 1800
            assert current_phase().state == "live"
            assert clock.now() < TARGET_TIME
            r = play.alice.get(f"{STORY}/finale")
            assert r.status_code == 404
            assert play.alice.get("/story/finale").status_code == 404


def test_finale_opens_at_or_after_timestamp_for_all_players(play):
    """Once clock reaches or passes l3mon_finale_at during the live event, it opens for everyone."""
    with play.app.app_context():
        set_config(FINALE_AT, TARGET_TIME)

        # Exactly at timestamp
        with freeze_clock(TARGET_TIME):
            assert clock.now() == TARGET_TIME
            r = play.alice.get(f"{STORY}/finale")
            assert r.status_code == 200
            assert r.get_json()["data"]["slug"] == "finale"
            page = play.alice.get("/story/finale")
            assert page.status_code == 200
            assert "Last Light" in page.get_data(as_text=True)

        # After timestamp
        with freeze_clock(TARGET_TIME + 600):
            assert clock.now() > TARGET_TIME
            r = play.alice.get(f"{STORY}/finale")
            assert r.status_code == 200
            assert "finale" in [s["slug"] for s in play.alice.get(STORY).get_json()["data"]["stories"]]


def test_crew_trigger_and_meter_still_work_with_automatic_time(play):
    """The crew's trigger and the on-air meter still open the finale independently of automatic time."""
    from story_world import TARGET
    from tests.helpers import gen_solve
    from CTFd.models import Users, Programme
    from CTFd.cache import clear_standings

    with play.app.app_context():
        set_config(FINALE_AT, TARGET_TIME + 10000)  # Far in future
        with freeze_clock(T_LIVE):
            # 1. Closed initially
            assert play.alice.get(f"{STORY}/finale").status_code == 404

            # 2. Crew trigger opens it
            set_config("l3mon_finale_open", True)
            assert play.alice.get(f"{STORY}/finale").status_code == 200

            # 3. Crew trigger closed again
            set_config("l3mon_finale_open", False)
            finale.forget()
            assert play.alice.get(f"{STORY}/finale").status_code == 404

            # 4. Meter target at 100% opens it
            set_config(TARGET, 1)
            cid = Programme.query.filter_by(slug="lantern_walk").one().challenge_id
            u = Users.query.filter_by(name="alice").one()
            gen_solve(db, user_id=u.id, team_id=u.team_id, challenge_id=cid)
            clear_standings()
            finale.forget()
            assert play.alice.get(f"{STORY}/finale").status_code == 200


def test_five_second_cache_does_not_hold_stale_closed_past_timestamp(play, monkeypatch):
    """The cached meter answer must not delay opening once the timestamp arrives."""
    from CTFd.plugins.l3mon_board import guide

    with play.app.app_context():
        set_config(FINALE_AT, TARGET_TIME)
        calls = []
        real = guide.meter
        monkeypatch.setattr(guide, "meter", lambda v: calls.append(1) or real(v))

        # 1. At TARGET_TIME - 10s: closed and caches the closed state
        with freeze_clock(TARGET_TIME - 10):
            finale.forget()
            assert play.alice.get(f"{STORY}/finale").status_code == 404
            assert len(calls) == 1

            # Second request within the 5s window uses cache
            assert play.alice.get(f"{STORY}/finale").status_code == 404
            assert len(calls) == 1

        # 2. Clock moves to TARGET_TIME: opens immediately without waiting for cache expiry
        with freeze_clock(TARGET_TIME):
            r = play.alice.get(f"{STORY}/finale")
            assert r.status_code == 200, "opens immediately once timestamp is reached"
            # It did not even need to call guide.meter because auto_at takes precedence
            assert len(calls) == 1


def test_finale_automatic_time_query_budget(play):
    """Checking the automatic time adds zero extra database queries beyond settings."""
    with play.app.app_context():
        set_config(FINALE_AT, TARGET_TIME)
        seen = []

        def on(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(db.engine, "before_cursor_execute", on)
        try:
            with freeze_clock(TARGET_TIME + 10):
                # First request warms cache
                play.alice.get(f"{STORY}/finale")
                seen.clear()

                # Second request
                r = play.alice.get(f"{STORY}/finale")
                assert r.status_code == 200
                assert len(seen) == 0, f"expected 0 queries on warm cache, got {len(seen)}: {seen}"
        finally:
            event.remove(db.engine, "before_cursor_execute", on)
