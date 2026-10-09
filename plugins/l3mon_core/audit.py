"""The audit trail: one line for every admin action, saying who did what to which target, and why.

Rules (design section 7): a line is written in the same transaction as the action it describes (record() adds it to the session
and never commits, so a rolled-back action leaves no line and a committed one always does); the text is plain and length-limited
(clipped, never refused, so an audit line can never make an action fail); no player route reads it. The actor's name is copied
onto the line, so deleting the account later does not erase who acted.
"""
import calendar

from flask import has_request_context

from CTFd.models import db
from CTFd.plugins.l3mon_core.models import Audit

SYSTEM = "system"
_LIMITS = {"action": 40, "target": 100, "detail": 500}


def _clip(text, limit) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _signed_in_user():
    """The signed-in user of the current request, if there is one."""
    if not has_request_context():
        return None
    try:
        from CTFd.utils.user import get_current_user

        return get_current_user()
    except Exception:  # a broken session must never stop the action that is being recorded
        return None


def record(action, target, detail="", actor=None, system=False) -> Audit:
    """Add one line to the current transaction. `actor` is a Users row; by default the signed-in user, else `system`.
    `system=True` says the clock or the platform did it, whoever happens to be signed in on the request that noticed."""
    who = None if system else (actor if actor is not None else _signed_in_user())
    line = Audit(
        actor_id=getattr(who, "id", None),
        actor_name=_clip(getattr(who, "name", None) or SYSTEM, 80),
        action=_clip(action, _LIMITS["action"]),
        target=_clip(target, _LIMITS["target"]),
        detail=_clip(detail, _LIMITS["detail"]),
    )
    db.session.add(line)
    return line


def recent(limit=20) -> list:
    """The newest lines first, as plain dictionaries (`at` is a UTC epoch second)."""
    rows = Audit.query.order_by(Audit.id.desc()).limit(max(1, int(limit))).all()
    return [
        {
            "id": row.id,
            "at": calendar.timegm(row.at.timetuple()),
            "actor": row.actor_name,
            "action": row.action,
            "target": row.target,
            "detail": row.detail,
        }
        for row in rows
    ]
