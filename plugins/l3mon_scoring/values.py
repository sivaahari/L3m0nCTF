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

from CTFd.models import Challenges, db
from CTFd.plugins.dynamic_challenges import DynamicChallenge
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.counting import counted_solve_counts

_log = logging.getLogger("l3mon")
CLEAR_FLAG = "l3mon_scoring_clear_caches"
DECAYING = ("linear", "logarithmic")  # the scoring functions CTFd decays by (anything else, "static", is a fixed value)


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


def mark_changed():
    """Say that this transaction changed what players see about scores (a solve, a value): CTFd's challenge and standings caches are
    cleared by the plugin's after-commit hook once the change is really committed. Not before: an earlier clear would let another
    request cache the old numbers again."""
    db.session.info[CLEAR_FLAG] = True


def decaying_rows(challenge_ids=None, lock=False) -> list:
    """Every challenge CTFd 3.8.8 values by a curve, in id order: the `dynamic` type (whatever function it names; an unknown name is read
    as logarithmic, as CTFd does) AND any other challenge whose own scoring function is linear or logarithmic (CTFd's editor offers
    that choice on every challenge, and values it after every solve exactly like a dynamic one; found by the independent audit).
    Each row answers `initial`, `minimum`, `decay` and `function` for its own kind. `lock=True` takes the rows' locks (SELECT ... FOR UPDATE,
    the dynamic ones first and then the others, each in id order, so two callers can never wait for each other)."""
    dynamic = DynamicChallenge.query
    plain = Challenges.query.filter(Challenges.type != "dynamic", Challenges.function.in_(DECAYING))
    if challenge_ids is not None:
        ids = list(challenge_ids)
        if not ids:
            return []
        dynamic = dynamic.filter(DynamicChallenge.id.in_(ids))
        plain = plain.filter(Challenges.id.in_(ids))
    if lock:
        dynamic = dynamic.order_by(DynamicChallenge.id).with_for_update()
        plain = plain.order_by(Challenges.id).with_for_update()
    # The database may call 'Linear' or 'linear ' equal to 'linear' (its collation ignores case and trailing spaces); CTFd compares exactly.
    plain_rows = [row for row in plain.all() if row.function in DECAYING]
    return sorted(dynamic.all() + plain_rows, key=lambda row: row.id)


def _wanted(challenge_ids=None, lock=False):
    """-> [(challenge, wanted value)] for the curve-valued challenges asked for (all by default), in id order. A challenge with no start
    value or no floor is malformed: it is named in the log and skipped, never allowed to stop the others."""
    rows = decaying_rows(challenge_ids, lock)
    usable = []
    for row in rows:
        if row.initial is None or row.minimum is None:
            _log.warning("l3mon: challenge %r (%s) is valued by a curve but has no start value or no floor; its value is left alone", row.name, row.id)
            continue
        usable.append(row)
    counts = counted_solve_counts([r.id for r in usable])
    return [(r, formula(r, counts[r.id])) for r in usable]


def drifted(challenge_ids=None) -> list:
    """-> [(id, stored, wanted)] for every dynamic challenge whose stored value is not its formula's. Reads only."""
    return [(r.id, r.value, want) for r, want in _wanted(challenge_ids) if r.value != want]


def recalculate(challenge_ids=None, lock=False) -> list:
    """Set every dynamic value (or those asked for) to its formula; -> [(id, old, new)] for the ones that changed. The caller commits
    (the change is part of its transaction); the caches are cleared after that commit.

    `lock=True` is for a recalculation that runs beside the crew's actions (the end of a ban's request, the minute check, the crew's
    "Recalculate"): it commits what the caller has pending, then takes the rows' locks as its first statement, so that nothing is counted
    until a Revoke or a Restore that holds the challenge has committed. A count taken before waiting for that lock is out of date when
    the value is written, and would overwrite the crew action's number (found by the independent audit). A locking read starts no
    read view; the counts that follow see everything committed before the locks were won. Revoke and Restore themselves already hold
    their challenge's row and do not ask."""
    if lock:
        db.session.commit()
    changed = []
    for row, want in _wanted(challenge_ids, lock):
        if row.value != want:
            changed.append((row.id, row.value, want))
            row.value = want
    if changed:
        mark_changed()
    return changed


def heal(system=True) -> list:
    """The safety net: fix any stored value that is not its formula's, say so in the audit trail and the log, and commit.
    -> the list of changes; nothing is written when nothing was wrong."""
    changed = recalculate(lock=True)
    if not changed:
        return []
    names = dict(db.session.query(Challenges.id, Challenges.name).filter(Challenges.id.in_([c[0] for c in changed])).all())
    detail = "; ".join(f"{names.get(cid, cid)}: {old} -> {new}" for cid, old, new in changed)
    _log.warning("l3mon: a stored value was not its formula's and has been put right (%s)", detail)
    audit.record("scoring.heal", f"{len(changed)} challenge(s)", detail, system=system)
    db.session.commit()
    return changed
