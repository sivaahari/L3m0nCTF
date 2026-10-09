"""When a dynamic value must be recalculated.

CTFd recalculates a dynamic challenge's value after a solve and at no other time. Everything else that changes who counts leaves the
stored value stale (measured on 3.8.8, plugins/l3mon_scoring/tests/probe_facts.py): a ban or a hide of a studio, a deleted studio, a
deleted user, a deleted solve, "mark incorrect". Four ways, from the most immediate to the last resort (each can be switched off in
`ON`, so a test can prove it is needed):

- `flush`: a session hook (noticed in `after_flush`, done in `after_flush_postexec`). When a flush changes `banned` or `hidden` of a
  studio or user, or deletes a studio, user or solve, the values are recalculated in the SAME transaction, so the cause and the new numbers are committed together or not at all.
- `bulk`: deleting a user is a set of bulk deletes (notifications, awards, unlocks, submissions, solves, the user) with no flush
  to hook, so those only set a flag on the request, and the values are recalculated and committed when the request ends.
- `check`: once a minute, one worker (the one that wins an atomic cache add) compares every stored value with its formula and fixes
  and reports any difference (`values.heal`). This covers what no hook sees: a script, an edit in the database, two players' solves
  racing each other.
- the crew's own actions (Revoke, Restore, "Recalculate") call `values.recalculate` themselves.

Clearing CTFd's challenge and standings caches happens after the commit that holds the new numbers (a hook on `after_commit`), never
before: an earlier clear would let another request cache the old numbers again.

A recalculation that fails is logged and never stops the request that asked for it.
"""
import logging

from flask import g, has_request_context, request
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from CTFd.cache import cache, clear_challenges, clear_standings
from CTFd.models import Solves, Teams, Users, db
from CTFd.plugins.l3mon_scoring import values

_log = logging.getLogger("l3mon")

ON = {"flush": True, "bulk": True, "check": True}
CHECK_KEY = "l3mon:scoring:checked"
CHECK_SECONDS = 60
WATCHED = (Teams, Users, Solves)

_PENDING = "l3mon_scoring_recalculate_after_flush"
_REQUEST_FLAG = "l3mon_scoring_recalculate_at_end"
_SKIP_PREFIXES = ("/themes/", "/plugins/", "/static/")
_SKIP_PATHS = ("/healthcheck", "/l3mon/healthz")
_installed = False


# -- the flush hook --------------------------------------------------------------------------------------------------------------

def _who_counts_changed(session) -> bool:
    for obj in session.dirty:
        if isinstance(obj, (Teams, Users)):
            attrs = inspect(obj).attrs
            if attrs.banned.history.has_changes() or attrs.hidden.history.has_changes():
                return True
    return any(isinstance(obj, WATCHED) for obj in session.deleted)


def _after_flush(session, context):
    """Notice, while the history of each object still exists, that who counts has changed. The recalculation itself waits for
    `_after_flush_postexec`: SQLAlchemy throws away any change made to a previously clean object inside `after_flush` ("Attribute
    history events accumulated on previously clean instances within inner-flush event handlers have been reset"), found the hard way."""
    if ON["flush"] and not session.info.get(_PENDING) and _who_counts_changed(session):
        session.info[_PENDING] = True


def _after_flush_postexec(session, context):
    """The flush is complete and the transaction is still open: recalculate now, in the same transaction. What this changes is
    flushed by the commit that is under way, so the cause and the new numbers are committed together or not at all."""
    if not session.info.pop(_PENDING, False) or session is not db.session():
        return
    try:
        values.recalculate()
    except Exception:  # noqa: BLE001  (the ban, hide or delete itself must still go through)
        _log.warning("l3mon: the dynamic values could not be recalculated after a change of who counts", exc_info=True)


# -- the bulk hook and the end of the request ------------------------------------------------------------------------------------

def _after_bulk_delete(context):
    if ON["bulk"] and issubclass(context.mapper.class_, WATCHED) and has_request_context():
        g.setdefault(_REQUEST_FLAG, True)


def _end_of_request(response):
    if g.pop(_REQUEST_FLAG, False):
        try:
            if values.recalculate():
                db.session.commit()  # the caches are cleared by the after_commit hook
        except Exception:  # noqa: BLE001
            db.session.rollback()
            _log.warning("l3mon: the dynamic values could not be recalculated after a bulk delete", exc_info=True)
    return response


# -- the caches, after the commit ------------------------------------------------------------------------------------------------

def _committed(session):
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
        event.listen(Session, "after_flush_postexec", _after_flush_postexec)
        event.listen(Session, "after_bulk_delete", _after_bulk_delete)
        event.listen(Session, "after_commit", _committed)
        event.listen(Session, "after_soft_rollback", _rolled_back)
        _installed = True
    if "l3mon_scoring_triggers" not in app.extensions:
        app.extensions["l3mon_scoring_triggers"] = True
        app.before_request(_minute_check)
        app.after_request(_end_of_request)
