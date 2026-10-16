"""The values stand still while the scoreboard is frozen (SP3 part 3.6).

CTFd's `freeze` is one time. From then on the public lists leave out every solve and award made at or after it, but the public standings are
made from the CURRENT stored value of each challenge (test_freeze_facts.py). A dynamic value that falls after the freeze therefore moves a
frozen score, and a falling value on the board tells everybody that other studios solved something. The cure is to keep the stored values
still: while the scoreboard is frozen **nothing writes `challenges.value`**, so every answer that reads it (the board, the panel, the Guide, the
scoreboard, the CTFtime feed, CTFd's own pages, a studio's own live score) is made from the same still numbers, and none of them needs to know.

What writes a value, and what holds:

- CTFd's own two valuation functions, which run after every solve, create and update: `CTFd.plugins.challenges.calculate_value` (a standard
  challenge that carries a scoring function) and `DynamicValueChallenge.calculate_value` (the dynamic type). They are wrapped here. While frozen
  the wrapper commits what is pending (CTFd's update puts the crew's edits there and relies on this function to commit them) and returns the
  challenge, so its value is not written. Otherwise it first ends the transaction it is in (a read view begun earlier would hide what commits while it waits), takes the
  challenge row's lock (SELECT ... FOR UPDATE) as its first statement, and only then lets CTFd count, so a Revoke or a Restore that holds the row and has not committed is waited for, and CTFd's count cannot overwrite the crew
  action's number (the race left open by part 3.3's audit). The lock is taken after CTFd has committed the solve, so a solve's own
  transaction never asks for it.
- `values.recalculate` (our end-of-request recalculation after a ban, the minute check, the crew's "Recalculate", Revoke and Restore) returns
  at once while frozen; `values.heal` and `values.drifted` follow.

Lifting the freeze (unsetting it, or moving it to a later time) is noticed by `triggers.py` and every value is put right when that request ends;
a lift made outside a request is noticed by the next request, and the minute check is the last resort. The true value is never lost: it is a
function of the solves, and the solves are all still there. Setting or moving the freeze values each challenge as of the freeze time (the studios
that solved it before it), so that a freeze set to a time already past does not hold a value made from solves the public list hides.

One more door: CTFd's challenge ratings are public totals that only a solver can change, so a rating after a solve made after the freeze would tell
everybody that somebody solved. Nobody but the crew rates while the scoreboard is frozen.

The test of "frozen" is the clock's: a freeze is set and now is at or after it, whatever the end of the event says (CTFd keeps a scoreboard frozen
after the end until somebody unsets the freeze).
"""
import datetime
import functools
import logging

from flask import jsonify, request

from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_core.clock import current_phase, window
from CTFd.utils.user import is_admin

_log = logging.getLogger("l3mon")
ON = {"hold": True, "lock": True, "ratings": True}  # a test switches a part off to show that it is needed
MARK = "_l3mon_freeze_wrapped"
RATINGS = "api.challenges_challenge_ratings"


def held() -> bool:
    """True while the stored values must not be written."""
    return ON["hold"] and bool(current_phase().frozen)


def cutoff():
    """The freeze time as a naive UTC time (as CTFd dates a solve), or None when no freeze is set."""
    at = window().freeze
    return datetime.datetime.utcfromtimestamp(at) if at else None


def lock_row(challenge_id):
    """Wait for any transaction that holds this challenge (a Revoke, a Restore, a recalculation) and keep the lock until the next commit.
    A locking read starts no read view, so the count that follows sees everything committed before the lock was won, PROVIDED nothing was
    read earlier in the transaction: the caller commits first."""
    db.session.query(Challenges.id).filter(Challenges.id == challenge_id).with_for_update().first()


def _wrapped(original):
    @functools.wraps(original)
    def calculate_value(challenge):
        if held():
            challenge_id = challenge.id
            db.session.commit()  # CTFd's update sets the crew's edits and lets this function commit them: they are kept, the value is not written
            if db.session.query(Challenges.value).filter(Challenges.id == challenge_id).scalar() is None:
                from CTFd.plugins.l3mon_scoring import values

                values.start_value(challenge_id)  # made while frozen with no value: it starts at its start value, there is nothing to hold yet
            return challenge  # CTFd's callers use the challenge it returns
        if ON["lock"]:
            challenge_id = challenge.id  # read before the commit: afterwards the object is expired and reading it would start a new read view
            db.session.commit()  # end this transaction: a read view begun earlier (CTFd read the challenge after the solve) would hide what commits while we wait
            lock_row(challenge_id)
        return original(challenge)

    setattr(calculate_value, MARK, True)
    calculate_value.original = original
    return calculate_value


def _ratings_door():
    """Nobody but the crew rates a challenge while the scoreboard is frozen (see the head of this file)."""
    if not ON["ratings"] or request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint != RATINGS:
        return None
    if not held() or is_admin():
        return None
    return jsonify({"success": False, "errors": {"": ["Ratings are closed while the scoreboard is frozen. They open again when it is lifted."]}}), 403


def install_doors(app):
    if "l3mon_scoring_freeze_doors" not in app.extensions:
        app.extensions["l3mon_scoring_freeze_doors"] = True
        app.before_request(_ratings_door)


def install():
    """Wrap CTFd's two valuation functions, once however many apps are created (the tests create many)."""
    from CTFd.plugins import challenges as standard
    from CTFd.plugins.dynamic_challenges import DynamicValueChallenge

    if not getattr(standard.calculate_value, MARK, False):
        standard.calculate_value = _wrapped(standard.calculate_value)
    if not getattr(DynamicValueChallenge.calculate_value, MARK, False):
        # a classmethod in CTFd, called as DynamicValueChallenge.calculate_value(challenge): a plain function does the same job
        DynamicValueChallenge.calculate_value = staticmethod(_wrapped(DynamicValueChallenge.calculate_value))
