"""What a programme is worth.

A fixed programme is worth the value it was created with. A dynamic one follows CTFd's own curve (CTFd/plugins/dynamic_challenges/
decay.py): with `n` solves by studios that count, use `n - 1` once anyone has solved it, and the value falls from its start
towards its floor; every studio that solved it is paid the same, current value, so a studio's TRP can fall after it solved
something. CTFd recalculates only after a solve. A ban, a hide, a deleted studio or user, a deleted or "incorrect" solve and a
Revoke change who counts and leave the stored value stale; `recalculate` puts it right, and `heal` is the minute-by-minute check
that finds anything no hook saw.

`formula` is CTFd's arithmetic, expression for expression, over a count we supply (so one grouped query values every challenge);
test_values.py compares it with CTFd's own functions for every count from 0 to 80, so the two cannot drift apart.

Recalculating does not clear the caches by itself: it marks the session, and the plugin's `after_commit` hook clears CTFd's challenge
and standings caches once the change is really committed (clearing before the commit would let another request cache the old
numbers again).
"""
import logging
import math

from CTFd.models import db
from CTFd.plugins.dynamic_challenges import DynamicChallenge
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.counting import counted_solve_counts

_log = logging.getLogger("l3mon")
CLEAR_FLAG = "l3mon_scoring_clear_caches"


def formula(challenge, solves: int) -> int:
    """The value of `challenge` (anything with initial, minimum, decay and function) when `solves` studios that count solved it."""
    n = solves
    if n != 0:
        n -= 1  # the first solver is paid the full start value
    initial, minimum, decay = challenge.initial, challenge.minimum, challenge.decay or 0
    if challenge.function == "linear":
        value = math.ceil(initial - (decay * n))
    else:  # logarithmic, and any name CTFd does not know (it falls back to this one)
        if decay == 0:
            decay = 1  # CTFd's guard against a division by zero
        value = math.ceil((((minimum - initial) / (decay**2)) * (n**2)) + initial)
    if value < minimum:
        value = minimum
    return value


def _wanted(challenge_ids=None):
    """-> [(challenge, wanted value)] for the dynamic challenges asked for (all by default), in id order. A challenge with no start
    value or no floor is malformed: it is named in the log and skipped, never allowed to stop the others."""
    query = DynamicChallenge.query
    if challenge_ids is not None:
        ids = list(challenge_ids)
        if not ids:
            return []
        query = query.filter(DynamicChallenge.id.in_(ids))
    rows = query.order_by(DynamicChallenge.id).all()
    usable = []
    for row in rows:
        if row.initial is None or row.minimum is None:
            _log.warning("l3mon: dynamic challenge %r (%s) has no start value or no floor; its value is left alone", row.name, row.id)
            continue
        usable.append(row)
    counts = counted_solve_counts([r.id for r in usable])
    return [(r, formula(r, counts[r.id])) for r in usable]


def drifted(challenge_ids=None) -> list:
    """-> [(id, stored, wanted)] for every dynamic challenge whose stored value is not its formula's. Reads only."""
    return [(r.id, r.value, want) for r, want in _wanted(challenge_ids) if r.value != want]


def recalculate(challenge_ids=None) -> list:
    """Set every dynamic value (or those asked for) to its formula; -> [(id, old, new)] for the ones that changed. The caller commits
    (the change is part of its transaction); the caches are cleared after that commit."""
    changed = []
    for row, want in _wanted(challenge_ids):
        if row.value != want:
            changed.append((row.id, row.value, want))
            row.value = want
    if changed:
        db.session.info[CLEAR_FLAG] = True
    return changed


def heal(system=True) -> list:
    """The safety net: fix any stored value that is not its formula's, say so in the audit trail and the log, and commit.
    -> the list of changes; nothing is written when nothing was wrong."""
    changed = recalculate()
    if not changed:
        return []
    names = {r.id: r.name for r in DynamicChallenge.query.filter(DynamicChallenge.id.in_([c[0] for c in changed])).all()}
    detail = "; ".join(f"{names.get(cid, cid)}: {old} -> {new}" for cid, old, new in changed)
    _log.warning("l3mon: a stored value was not its formula's and has been put right (%s)", detail)
    audit.record("scoring.heal", f"{len(changed)} challenge(s)", detail, system=system)
    db.session.commit()
    return changed
