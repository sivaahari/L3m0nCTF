"""The finale: the last story, `finale`, which belongs to no channel.

It plays for every registered player once the broadcast is complete, which is when the Guide's on-air meter, as that player sees it, reads
100 percent, or when the crew flips its trigger (the setting `l3mon_finale_open`, kept in CTFd's own settings so it is in its backups and its
admin API). Before the start nothing opens it, not even the trigger. The crew can preview it at any time like every other story.

The meter is worked out only when there is a finale file to show and the trigger is off, so a platform without one pays nothing. It is worked out
for the one number the gate needs (not the studio's whole Guide), and the answer for a studio is kept for a few seconds: one viewing is about nine
requests (the list, the page, the story, six pictures) and everybody opens it at the same moment, when the meter fills. The crew's trigger is read
every time and never waits for the kept answer.
"""
import time

from CTFd.plugins.l3mon_board import guide
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.settings import finale_open, story_air_target, story_meter
from CTFd.plugins.l3mon_story.store import FINALE

SLUG = FINALE
KEEP_SECONDS = 5
MOST_KEPT = 4000  # studios; the whole table is dropped when it fills, which costs a few measurements and bounds the memory

_kept = {}  # studio id (0 for none) -> (kept until, open or not)


def forget():
    """Drop every kept answer (a test, or after a change that must show at once)."""
    _kept.clear()


def _meter_is_full(who) -> bool:
    key = who.team.id if who.team is not None else 0
    now = time.monotonic()
    found = _kept.get(key)
    if found is not None and found[0] > now:
        return found[1]
    block = guide.meter(who)
    full = bool(block) and block["on_air"] >= 1
    if len(_kept) >= MOST_KEPT:
        _kept.clear()
    _kept[key] = (now + KEEP_SECONDS, full)
    return full


def is_open(who) -> bool:
    """True when this player may watch the finale now (`who` is `viewer.current()`)."""
    if current_phase().state == "before":
        return False
    if finale_open():
        return True
    if not story_meter() or not story_air_target():
        return False
    return _meter_is_full(who)
