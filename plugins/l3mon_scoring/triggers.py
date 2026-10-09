"""When a dynamic value must be recalculated.

CTFd recalculates a challenge's value after a solve made by a player and at no other time. Everything else that changes who counts
leaves the stored value stale (measured on 3.8.8, plugins/l3mon_scoring/tests/probe_facts.py): a ban or a hide of a studio, a deleted
studio, user or solve, a member removed from a studio, "mark incorrect", and a solve added by the crew ("mark correct", a submission
made by hand). Three ways, from the most immediate to the last resort (each can be switched off in `ON`, so a test can prove it is
needed):

- `flush` and `bulk`: the hooks only NOTICE. A flush that bans or hides a studio or user, or deletes a studio, user or solve, notes
  "every value"; a flush that adds a solve notes that solve's challenge; a bulk delete of studios, users, solves or submissions (CTFd
  deletes a user, or removes a member, with bulk deletes and no flush) notes "every value". The value is recalculated when the
  REQUEST ENDS, in a fresh transaction after the cause has committed.
- `check`: once a minute, one worker (the one that wins an atomic cache add) compares every stored value with its formula and fixes
  and reports any difference (`values.heal`). This covers what no hook sees: a script, an edit in the database, a crew action on a
  worker that failed to recalculate.
- the crew's own actions (Revoke, Restore, "Recalculate") call `values.recalculate` themselves.

Why not inside the transaction that caused the change (the first version did, and an independent audit measured 10 of 24 correct
flags recorded when eight players solved one challenge at once). A solve's transaction has inserted a row that points at the challenge,
which takes InnoDB's shared lock on the challenge row; recalculating there asks for the exclusive lock on the same row, and two
players doing that at once deadlock: one is told 500 and loses a correct flag. CTFd itself avoids this by valuing the challenge in a
second transaction after the solve has committed, and so does this. The same rule keeps a ban from taking the challenge row while
holding the studio's row, which would be the opposite order to Revoke's. A player's own flag (`/api/v1/challenges/attempt`) is left to
CTFd, which values the challenge itself right after the solve.

Clearing CTFd's challenge and standings caches happens after the commit that holds the new numbers (a hook on `after_commit`), never
before: an earlier clear would let another request cache the old numbers again.

A recalculation that fails is logged and never stops the request that asked for it.
"""
import logging

from flask import g, has_request_context, request
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from CTFd.cache import cache, clear_challenges, clear_standings
from CTFd.models import Solves, Submissions, Teams, Users, db
from CTFd.plugins.l3mon_scoring import values

_log = logging.getLogger("l3mon")

ON = {"flush": True, "bulk": True, "check": True}
CHECK_KEY = "l3mon:scoring:checked"
CHECK_SECONDS = 60
WATCHED = (Teams, Users, Solves)  # a flush that deletes one of these, or bans or hides a studio or user, changes who counts
WATCHED_BULK = (Teams, Users, Solves, Submissions)  # CTFd also removes a member's submissions (and with them the solves) in bulk
ATTEMPT = "/api/v1/challenges/attempt"

_EVERYTHING = "l3mon_scoring_recalculate_everything"  # on `g`: who counts changed, so every curve-valued challenge
_THESE = "l3mon_scoring_recalculate_these"  # on `g`: solves were added, so only their challenges
_SKIP_PREFIXES = ("/themes/", "/plugins/", "/static/")
_SKIP_PATHS = ("/healthcheck", "/l3mon/healthz")
_installed = False


# -- noticing ---------------------------------------------------------------------------------------------------------------------

def _note_everything():
    if has_request_context():
        g.setdefault(_EVERYTHING, True)


def _note_challenges(ids):
    if has_request_context():
        g.setdefault(_THESE, set()).update(ids)


def _who_counts_changed(session) -> bool:
    for obj in session.dirty:
        if isinstance(obj, (Teams, Users)):
            attrs = inspect(obj).attrs
            if attrs.banned.history.has_changes() or attrs.hidden.history.has_changes():
                return True
    return any(isinstance(obj, WATCHED) for obj in session.deleted)


def _after_flush(session, context):
    """Notice, while the history of each object still exists, that who counts has changed or that a solve was added. Nothing is
    written here: the value is recalculated when the request ends."""
    if not ON["flush"]:
        return
    if _who_counts_changed(session):
        _note_everything()
    added = {obj.challenge_id for obj in session.new if isinstance(obj, Solves) and obj.challenge_id is not None}
    if added:
        _note_challenges(added)


def _after_bulk_delete(context):
    if ON["bulk"] and issubclass(context.mapper.class_, WATCHED_BULK):
        _note_everything()


# -- the end of the request -------------------------------------------------------------------------------------------------------

def _end_of_request(response):
    everything = g.pop(_EVERYTHING, False)
    these = g.pop(_THESE, None)
    if request.path.rstrip("/") == ATTEMPT:
        these = None  # CTFd values the challenge itself, in its own transaction, right after a player's solve
    if not (everything or these):
        return response
    try:
        # A view that returned an error may have left work uncommitted; it is rolled back at teardown anyway, and must not be committed by us.
        db.session.rollback()
        if values.recalculate(None if everything else these, lock=True):
            db.session.commit()  # the caches are cleared by the after_commit hook
    except Exception:  # noqa: BLE001  (the ban, the delete or the solve itself went through; the minute check will put it right)
        db.session.rollback()
        _log.warning("l3mon: the dynamic values could not be recalculated at the end of the request", exc_info=True)
    return response


# -- the caches, after the commit ------------------------------------------------------------------------------------------------

def _committed(session):
    if session.in_nested_transaction():
        return  # SQLAlchemy 1.4 also calls this when a savepoint is released; wait for the real commit
    if session.info.pop(values.CLEAR_FLAG, False):
        try:
            clear_challenges()
            clear_standings()
        except Exception:  # noqa: BLE001  (the cache being down must not fail a commit that already happened)
            _log.warning("l3mon: the caches could not be cleared after a recalculation", exc_info=True)


def _rolled_back(session, previous_transaction):
    if previous_transaction.parent is None:
        session.info.pop(values.CLEAR_FLAG, None)


# -- the minute check ------------------------------------------------------------------------------------------------------------

def _minute_check():
    path = request.path
    if not ON["check"] or path in _SKIP_PATHS or path.startswith(_SKIP_PREFIXES) or request.method == "OPTIONS":
        return None
    try:
        if cache.get(CHECK_KEY) or not cache.add(CHECK_KEY, 1, timeout=CHECK_SECONDS):
            return None  # looked at within the minute, or another worker has this one
        values.heal()
    except Exception:  # noqa: BLE001  (a page must never fail because of the check)
        db.session.rollback()
        _log.warning("l3mon: the minute check of the dynamic values could not run", exc_info=True)
    return None


def install(app):
    """Listen to every database session in the process (once, however many apps are created: the tests create many), and give each
    app its request hooks."""
    global _installed
    if not _installed:
        event.listen(Session, "after_flush", _after_flush)
        event.listen(Session, "after_bulk_delete", _after_bulk_delete)
        event.listen(Session, "after_commit", _committed)
        event.listen(Session, "after_soft_rollback", _rolled_back)
        _installed = True
    if "l3mon_scoring_triggers" not in app.extensions:
        app.extensions["l3mon_scoring_triggers"] = True
        app.before_request(_minute_check)
        app.after_request(_end_of_request)
