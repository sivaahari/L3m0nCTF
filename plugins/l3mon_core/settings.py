"""The one switch the organisers can flip that CTFd does not already have, kept in CTFd's own settings table (so it is in its
backups and its admin API).

    l3mon_show_coming_count  "12 coming up" under the channel strip. ON unless switched off
    l3mon_story_meter        the story meter in the Guide (reels and "on air"). ON unless switched off
    l3mon_story_air_target   how many solves of all studios fill the "on air" meter; 0 (unset) leaves the meter at 0

There is no freeze switch here on purpose. CTFd freezes its own standings and team pages whenever its `freeze` setting holds a
time, and a second flag of ours could say "not frozen" while CTFd's scoreboard is. The freeze is therefore that one time
(clock.py reads it): unset is no freeze, which is how it stays until the core decides.
"""
from CTFd.utils import get_config

SHOW_COMING_KEY = "l3mon_show_coming_count"
METER_KEY = "l3mon_story_meter"
AIR_TARGET_KEY = "l3mon_story_air_target"

_ON = ("1", "true", "yes", "on")


def _flag(key, default):
    """True for an on-spelling, False for any other value that is set, `default` when nothing is set."""
    value = get_config(key)
    if value is None:
        return default
    return str(value).strip().lower() in _ON


def show_coming_count() -> bool:
    return _flag(SHOW_COMING_KEY, True)


def story_meter() -> bool:
    return _flag(METER_KEY, True)


def story_air_target() -> int:
    """The crew's target as a whole number of solves, or 0 when it is unset, not a whole number, negative or absurdly large."""
    value = get_config(AIR_TARGET_KEY)
    if isinstance(value, bool):
        return 0
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return number if 0 < number <= 10_000_000 else 0

