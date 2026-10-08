"""The one switch the organisers can flip that CTFd does not already have, kept in CTFd's own settings table (so it is in its
backups and its admin API).

    l3mon_show_coming_count  "12 coming up" under the channel strip. ON unless switched off

There is no freeze switch here on purpose. CTFd freezes its own standings and team pages whenever its `freeze` setting holds a
time, and a second flag of ours could say "not frozen" while CTFd's scoreboard is. The freeze is therefore that one time
(clock.py reads it): unset is no freeze, which is how it stays until the core decides.
"""
from CTFd.utils import get_config

SHOW_COMING_KEY = "l3mon_show_coming_count"

_ON = ("1", "true", "yes", "on")


def _flag(key, default):
    """True for an on-spelling, False for any other value that is set, `default` when nothing is set."""
    value = get_config(key)
    if value is None:
        return default
    return str(value).strip().lower() in _ON


def show_coming_count() -> bool:
    return _flag(SHOW_COMING_KEY, True)
