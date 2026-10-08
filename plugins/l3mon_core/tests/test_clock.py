"""The clock: 09:00 IST is 03:30 UTC, the four phases, the freeze, and agreement with CTFd at the exact seconds that matter.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import calendar

import pytest
from freezegun import freeze_time

from CTFd.plugins.l3mon_core import clock
from CTFd.plugins.l3mon_core.clock import Phase, current_phase, ist_text, ist_to_epoch, phase_at, to_ist, utc_text, window
from CTFd.utils import set_config
from CTFd.utils.dates import ctf_ended, ctf_started, ctftime
from tests.helpers import create_ctfd, destroy_ctfd

UTC = lambda *a: calendar.timegm(a)  # noqa: E731  (year, month, day, hour, minute, second) -> epoch seconds
START = UTC(2026, 11, 28, 3, 30, 0)  # 09:00 IST on 28 November 2026
END = START + 24 * 3600
FREEZE = END - 3600


# ---- conversion ----

def test_nine_in_the_morning_in_india_is_half_past_three_in_the_night_in_utc():
    assert ist_to_epoch(2026, 11, 28, 9, 0) == START
    assert ist_to_epoch(2026, 11, 29, 9, 0) == END
    assert ist_text(START) == "28 Nov 2026, 09:00 IST"
    assert utc_text(START) == "28 Nov 2026, 03:30 UTC"


def test_to_ist_gives_an_aware_time_in_india():
    t = to_ist(START)
    assert (t.year, t.month, t.day, t.hour, t.minute) == (2026, 11, 28, 9, 0)
    assert t.utcoffset().total_seconds() == 5.5 * 3600 and t.tzname() == "IST"


def test_the_date_rolls_over_the_right_way_between_the_two_zones():
    evening_utc = UTC(2026, 11, 28, 18, 30, 0)  # 18:30 UTC is midnight in India, already the next day
    assert utc_text(evening_utc) == "28 Nov 2026, 18:30 UTC"
    assert ist_text(evening_utc) == "29 Nov 2026, 00:00 IST"
    early_ist = ist_to_epoch(2026, 11, 29, 0, 15)  # 00:15 IST is 18:45 UTC the day before
    assert utc_text(early_ist) == "28 Nov 2026, 18:45 UTC"


def test_india_has_no_daylight_saving_in_any_month():
    for month in range(1, 13):
        t = ist_to_epoch(2026, month, 15, 12, 0)
        assert utc_text(t).endswith("06:30 UTC"), month


# ---- the phase, as a pure function ----

@pytest.mark.parametrize(
    "t,state",
    [
        (START - 3600, "before"),
        (START - 1, "before"),
        (START, "before"),  # CTFd: started only when now > start, so at the very second it is still before
        (START + 1, "live"),
        (START + 12 * 3600, "live"),
        (END - 1, "live"),
        (END, "ended"),
        (END + 1, "ended"),
        (END + 30 * 86400, "ended"),
    ],
)
def test_the_four_states_around_the_window(t, state):
    assert phase_at(t, START, END) == Phase(state, False)


def test_paused_only_means_something_while_the_window_is_open():
    assert phase_at(START + 10, START, END, paused=True) == Phase("paused", False)
    assert phase_at(START - 10, START, END, paused=True).state == "before"
    assert phase_at(END + 10, START, END, paused=True).state == "ended"


def test_the_freeze_starts_at_its_second_and_stays_after_the_end():
    assert phase_at(FREEZE - 1, START, END, freeze=FREEZE) == Phase("live", False)
    assert phase_at(FREEZE, START, END, freeze=FREEZE) == Phase("live", True)
    assert phase_at(END + 5, START, END, freeze=FREEZE) == Phase("ended", True)
    assert phase_at(FREEZE + 5, START, END, paused=True, freeze=FREEZE) == Phase("paused", True)


def test_a_missing_start_end_or_freeze_is_open_and_zero_means_missing_like_in_ctfd():
    assert phase_at(START, None, None).state == "live"
    assert phase_at(START, 0, 0).state == "live"
    assert phase_at(START, None, END).state == "live"
    assert phase_at(END + 1, None, END).state == "ended"
    assert phase_at(START + 1, START, None).state == "live"
    assert phase_at(START - 1, START, None).state == "before"
    assert phase_at(END, START, END, freeze=None).frozen is False
    assert phase_at(END, START, END, freeze=0).frozen is False


# ---- reading CTFd's settings, and agreeing with CTFd at the exact seconds ----

def _app_with_window(**config):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        set_config("start", str(START))
        set_config("end", str(END))
        for key, value in config.items():
            set_config(key, value)
    return app


@pytest.mark.parametrize(
    "stamp,state,ctfd_live",
    [
        ("2026-11-28 03:29:59", "before", False),
        ("2026-11-28 03:30:00", "before", False),
        ("2026-11-28 03:30:01", "live", True),
        ("2026-11-28 15:00:00", "live", True),
        ("2026-11-29 03:29:59", "live", True),
        ("2026-11-29 03:30:00", "ended", False),
    ],
)
def test_the_phase_and_ctfds_own_checks_agree_at_the_seconds_that_matter(stamp, state, ctfd_live):
    app = _app_with_window()
    with app.app_context():
        with freeze_time(stamp):
            assert current_phase().state == state
            assert ctftime() is ctfd_live, "a flag is accepted exactly when we say live"
            assert (current_phase().state == "ended") or not ctf_ended()
            assert ctf_started() == (state != "before")
    destroy_ctfd(app)


def test_one_second_after_the_end_ctfd_also_calls_it_ended():
    app = _app_with_window()
    with app.app_context():
        with freeze_time("2026-11-29 03:30:01"):
            assert current_phase().state == "ended" and ctf_ended() is True
    destroy_ctfd(app)


def test_paused_comes_from_ctfds_paused_setting_inside_the_window_only():
    app = _app_with_window(paused="true")
    with app.app_context():
        with freeze_time("2026-11-28 10:00:00"):
            assert current_phase() == Phase("paused", False)
        with freeze_time("2026-11-28 02:00:00"):
            assert current_phase().state == "before"
    destroy_ctfd(app)


def test_the_freeze_is_ctfds_own_freeze_time_and_nothing_else():
    app = _app_with_window(freeze=str(FREEZE))
    with app.app_context():
        with freeze_time("2026-11-29 02:29:59"):
            assert current_phase() == Phase("live", False)
        with freeze_time("2026-11-29 02:30:00"):
            assert current_phase() == Phase("live", True), "from the freeze second on, with no second switch to disagree with CTFd's scoreboard"
        with freeze_time("2026-11-29 03:30:00"):
            assert current_phase() == Phase("ended", True)
        assert window() == clock.Window(START, END, FREEZE)
        set_config("freeze", "")
        with freeze_time("2026-11-29 03:00:00"):
            assert current_phase().frozen is False, "no freeze time, no freeze: the default"
    destroy_ctfd(app)


def test_our_frozen_flag_and_ctfds_frozen_standings_flip_at_the_same_second():
    from CTFd.models import Solves, db
    from CTFd.utils.scores import get_standings
    from tests.helpers import gen_challenge, gen_solve, gen_team

    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        set_config("start", str(START))
        set_config("end", str(END))
        set_config("freeze", str(FREEZE))
        chal = gen_challenge(db)
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        before = gen_solve(db, user_id=team.members[0].id, team_id=team.id, challenge_id=chal.id)
        late_chal = gen_challenge(db, name="late", value=50)
        late = gen_solve(db, user_id=team.members[0].id, team_id=team.id, challenge_id=late_chal.id)
        import datetime

        before.date = datetime.datetime.utcfromtimestamp(FREEZE - 1)
        late.date = datetime.datetime.utcfromtimestamp(FREEZE + 1)
        db.session.commit()
        from CTFd.cache import clear_standings

        clear_standings()
        assert [(row.name, int(row.score)) for row in get_standings()] == [("t", 100)], "CTFd's scoreboard leaves out the solve after the freeze"
        with freeze_time("2026-11-29 02:29:59"):
            assert current_phase().frozen is False
        with freeze_time("2026-11-29 02:30:00"):
            assert current_phase().frozen is True
    destroy_ctfd(app)


def test_window_reads_the_settings_and_never_raises_on_nonsense():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        assert window() == clock.Window(None, None, None)
        set_config("start", "not a number")
        assert window().start is None
        set_config("end", "-5")
        assert window().end == -5 and current_phase(START + 5).state == "ended" and ctf_ended(), "a negative end is long past, as CTFd reads it"
        set_config("end", "0")
        assert window().end is None
        set_config("start", str(START))
        assert window().start == START
        assert current_phase(START + 5).state == "live"
    destroy_ctfd(app)
