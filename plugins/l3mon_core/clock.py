"""The event clock: the phase of the broadcast, and times in India and in UTC.

The online round runs from 10:00 IST to 22:00 IST on 28 November 2026 (twelve hours), which is 04:30 to 16:30 UTC. Everything is stored and compared as UTC epoch
seconds (what CTFd's `start`, `end` and `freeze` settings hold); India Standard Time is UTC+5:30 all year, with no daylight
saving, and is only for words people read.

The phase follows CTFd's own rules so that a flag is accepted exactly when the site says the broadcast is live:

    before   now <= start      (CTFd: started only when now > start)
    live     start < now < end
    paused   live, with CTFd's "paused" setting on
    ended    now >= end        (CTFd says ended one instant later; no clock reads that instant)

`frozen` is separate: the scoreboard freeze, from the freeze second on. The freeze is CTFd's own `freeze` setting and nothing
else (no second switch of ours that could disagree with CTFd's own scoreboard): unset is no freeze, which is how it stays
until the core decides. The queries and screens that honour it come in 3.6.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

from CTFd.utils import get_config

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30), "IST")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class Phase(NamedTuple):
    state: str  # before | live | paused | ended
    frozen: bool


class Window(NamedTuple):
    start: Optional[int]
    end: Optional[int]
    freeze: Optional[int]


def now() -> float:
    """Seconds since the epoch. One place to read the clock, so a test can freeze it (freezegun patches time.time)."""
    return time.time()


def to_ist(t) -> datetime:
    """An aware datetime in India for a UTC epoch second."""
    return datetime.fromtimestamp(t, IST)


def ist_to_epoch(year, month, day, hour=0, minute=0, second=0) -> int:
    """The UTC epoch second of a wall-clock time in India."""
    return int(datetime(year, month, day, hour, minute, second, tzinfo=IST).timestamp())


def _text(t, tz, label) -> str:
    dt = datetime.fromtimestamp(t, tz)
    return f"{dt.day} {_MONTHS[dt.month - 1]} {dt.year}, {dt:%H:%M} {label}"


def ist_text(t) -> str:
    """For example `28 Nov 2026, 10:00 IST`. English month names always, whatever the server's locale."""
    return _text(t, IST, "IST")


def utc_text(t) -> str:
    """For example `28 Nov 2026, 04:30 UTC`."""
    return _text(t, UTC, "UTC")


def phase_at(t, start, end, paused=False, freeze=None) -> Phase:
    """The phase at second `t`. A missing (None) or zero start, end or freeze is open, as in CTFd."""
    if end and t >= end:
        state = "ended"
    elif start and t <= start:
        state = "before"
    else:
        state = "paused" if paused else "live"
    return Phase(state, bool(freeze) and t >= freeze)


def _epoch(value) -> Optional[int]:
    """A setting as epoch seconds, read the way CTFd reads it (int(value), zero is none). Text that is not a whole number is no
    time at all. A negative number is kept: CTFd calls an end before 1970 long past, and so do we."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n or None


def window() -> Window:
    """The start, end and freeze second from CTFd's own settings. Never raises on a bad value."""
    return Window(_epoch(get_config("start")), _epoch(get_config("end")), _epoch(get_config("freeze")))


def current_phase(t=None) -> Phase:
    """The phase now (or at `t`), from CTFd's settings."""
    w = window()
    return phase_at(now() if t is None else t, w.start, w.end, paused=bool(get_config("paused")), freeze=w.freeze)
