"""A studio's private lines: "Solve voided", "Solve restored", "Bonus +50 TRP", each with the crew's words.

Stored in our own table (`l3mon_note`), never in CTFd's notifications (which every player can list). Written by the crew's actions
inside the transaction of the action; read by the studio itself through the Guide and the bell (SP3 part 3.5). No public route
returns a line (test_scoring_doors.py proves it).
"""
import calendar

from CTFd.models import db
from CTFd.plugins.l3mon_core.models import Note

TITLE_LIMIT = 100
TEXT_LIMIT = 500


def _clip(text, limit) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tell(team_id, title, text) -> Note:
    """Add one line for a studio to the current transaction (the action it belongs to commits it). Long words are clipped, never refused."""
    line = Note(team_id=team_id, title=_clip(title, TITLE_LIMIT), text=_clip(text, TEXT_LIMIT))
    db.session.add(line)
    return line


def latest(team_id, limit=5) -> list:
    """The studio's newest lines first: [{"title", "content", "at"}] (`at` is a UTC epoch second)."""
    rows = Note.query.filter_by(team_id=team_id).order_by(Note.created_at.desc(), Note.id.desc()).limit(max(1, int(limit))).all()
    return [{"title": r.title, "content": r.text, "at": calendar.timegm(r.created_at.timetuple())} for r in rows]
