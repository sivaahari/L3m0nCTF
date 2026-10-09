"""What is on air: the plan side of "what may a player see". One rule, asked from one place (visibility.py).

The crew's plan has three states for every channel and every programme: released, withheld, or scheduled for a second. A
programme is on air when its channel is on air and it is on air itself. A `scheduled` entry whose second has come counts as
released (the scheduler in l3mon_release turns that into CTFd's own challenge state, so the stock endpoints agree; this
module is the rule both sides use). Once the broadcast has ended every programme on the plan is on air again, as in the demo.

Release control is "in use" as soon as any channel exists. From then on a challenge that is in no channel is not on air (a
challenge nobody put on the plan is held back, not shown by accident). With no channel at all nothing here applies and stock
CTFd decides, which is how CTFd's own tests and a fresh install behave.

A state or a time this module does not understand never counts as on air.
"""
import calendar

from CTFd.models import db
from CTFd.plugins.l3mon_core.clock import current_phase, now
from CTFd.plugins.l3mon_core.models import Channel, Programme

RELEASED = "released"
SCHEDULED = "scheduled"


def release_active() -> bool:
    """True when the crew has made at least one channel, i.e. release control is in use."""
    return db.session.query(Channel.id).first() is not None


def to_epoch(moment):
    """A naive UTC datetime (how the database holds it) as epoch seconds."""
    return calendar.timegm(moment.timetuple())


def entry_on_air(state, release_at, t) -> bool:
    """One channel's or one programme's own setting, at epoch second `t`. The parent is not looked at."""
    if state == RELEASED:
        return True
    if state == SCHEDULED and release_at is not None:
        return to_epoch(release_at) <= t
    return False


def _rows():
    return (
        db.session.query(Programme.challenge_id, Programme.release_state, Programme.release_at, Channel.release_state, Channel.release_at)
        .join(Channel, Programme.channel_id == Channel.id)
    )


def on_air_ids(t=None) -> set:
    """The challenge ids whose programme is on air at second `t` (now by default): every programme once the broadcast has ended,
    otherwise those whose channel and programme are both on air. Says nothing about CTFd's own state or about prerequisites."""
    t = now() if t is None else t
    rows = _rows().all()
    if current_phase(t).state == "ended":
        return {row[0] for row in rows}
    return {cid for cid, p_state, p_at, c_state, c_at in rows if entry_on_air(c_state, c_at, t) and entry_on_air(p_state, p_at, t)}


def is_on_air(challenge_id, t=None) -> bool:
    """The same answer for one challenge, with one small query."""
    t = now() if t is None else t
    row = _rows().filter(Programme.challenge_id == challenge_id).first()
    if row is None:
        return False
    _, p_state, p_at, c_state, c_at = row
    if current_phase(t).state == "ended":
        return True
    return entry_on_air(c_state, c_at, t) and entry_on_air(p_state, p_at, t)
