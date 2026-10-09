"""The scheduler: a drop happens on time, from the first request at or after its second, and costs nothing until then.

There is no background clock. Every request first asks one question: "has the next moment come?". The answer is a small
record in the cache (`next`: the earliest second at which something changes, `end`: the end setting it was worked out for).
Until that second the check is a single cache read and no database work. At the second, the request that finds it due runs
reconcile() before it answers, so the very page that notices the drop already shows it; reconcile() claims each change with
one conditional update, so however many workers notice it together, the drop is shown, announced and reported once.

The moments are: every `scheduled` channel or programme time still to come, and the end of the broadcast (when everything on
the plan can be read again). If the crew moves the end, the stored `end` no longer matches and the next request reconciles.
If the record is missing (a restart, a flushed cache, the cache's own timeout) the next request works it out and reconciles,
which also puts right anything that fell due while nobody was looking. A reconcile that fails never breaks the page: the
failure is logged and the request goes on, and the next try is a few seconds later, not on every request.

The health routes and the static files never wait on any of this.
"""
import logging

from flask import request

from CTFd.cache import cache
from CTFd.models import db
from CTFd.plugins.l3mon_core.airing import release_active, to_epoch
from CTFd.plugins.l3mon_core.clock import now, window
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_release.reconcile import reconcile, register_after_change

_log = logging.getLogger("l3mon")

KEY = "l3mon:release:next"
RETRY_SECONDS = 10
RECORD_SECONDS = 30  # short on purpose: a record worked out from a view that was a moment out of date mends itself within this
_SKIP_PREFIXES = ("/themes/", "/plugins/", "/static/")
_SKIP_PATHS = ("/healthcheck", "/l3mon/healthz")


def _end() -> int:
    return window().end or 0


def next_event(t) -> int:
    """The earliest second after `t` at which the picture changes by itself, or 0 when there is none (or no plan)."""
    if not release_active():
        return 0
    moments = []
    for model in (Channel, Programme):
        rows = db.session.query(model.release_at).filter(model.release_state == "scheduled", model.release_at.isnot(None)).all()
        moments += [to_epoch(moment) for (moment,) in rows if to_epoch(moment) > t]
    end = _end()
    if end > t:
        moments.append(end)
    return min(moments) if moments else 0


def refresh(t=None):
    """Work out the next moment and remember it together with the end it was worked out for."""
    t = now() if t is None else t
    cache.set(KEY, {"next": next_event(t), "end": _end()}, timeout=RECORD_SECONDS)


def maybe_apply(t=None) -> bool:
    """Apply what is due, if anything is. True when a reconcile ran. Never raises."""
    t = now() if t is None else t
    try:
        stored = cache.get(KEY)
        end = _end()
        if isinstance(stored, dict) and stored.get("end") == end and (not stored.get("next") or t < stored["next"]):
            return False
        try:
            reconcile(t, system=True)  # refreshes the record itself; the clock did this, not the visitor
        except Exception as error:  # noqa: BLE001  (a failing database must not take the pages down with it)
            db.session.rollback()
            _log.warning("l3mon: the scheduler could not apply what is due: %r", error, exc_info=True)
            cache.set(KEY, {"next": t + RETRY_SECONDS, "end": end}, timeout=RECORD_SECONDS)
            return False
        return True
    except Exception as error:  # noqa: BLE001  (the cache itself may be down)
        _log.warning("l3mon: the scheduler could not look: %r", error, exc_info=True)
        return False


def _on_request():
    path = request.path
    if path in _SKIP_PATHS or path.startswith(_SKIP_PREFIXES) or request.method == "OPTIONS":
        return None
    maybe_apply()
    return None


def install(app):
    """Remember the next moment after every reconcile, and look at it before every ordinary request."""
    register_after_change(refresh)
    if "l3mon_release_scheduler" not in app.extensions:
        app.extensions["l3mon_release_scheduler"] = True
        app.before_request(_on_request)
