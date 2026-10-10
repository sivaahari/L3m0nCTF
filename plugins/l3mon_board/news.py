"""The news a viewer may read: one list in one numbering, and the two numbers the pages need (the newest id and a version).

A player's list holds three kinds of line, oldest first:

- the public notifications (CTFd's, with no studio and no account named),
- the ones CTFd addresses to the viewer's studio or to the viewer's own account,
- the studio's private lines (`l3mon_note`: a void, a restore, a bonus message), only for the viewer's own studio.

The two tables count their ids separately, so a line's id as a page sees it is made from the time it was written: milliseconds since the
epoch times a thousand, plus 500 for a private line, plus **the line's place among the viewer's own lines of the same kind written in the
same millisecond** (0 for the first). The place is a count of what this viewer may read, never a row number: both tables count their rows
across all studios, and a row number in an id would let a studio count the lines written for the others. The id is different for every line,
does not move when an older line is removed, and stays below 2^53, so a browser keeps it exactly. It is NOT CTFd's own notification id, and
it tells when the line was written (a player may read the time of the news; it is not a secret).

The id follows the time a line was written, not the moment it was committed, and a public line sorts below a private one made in the same
millisecond. So a line committed late (a long crew action racing a "New on air") can land under an id a page has already seen: a page counts
what it has not shown yet **by id**, not by the highest id it has seen. The crew's own list is CTFd's, with CTFd's ids, and so are the crew's numbers here.

The version is a hash of the whole list (ids, titles and texts), so a change of any line (a new one, an edit, a removal) is a change and
another studio's lines are not in it: the number of private lines the crew wrote for someone else never shows.
"""
import calendar
import hashlib

from CTFd.models import Notifications, db
from CTFd.plugins.l3mon_core.models import Note

PRIVATE = 500  # the mark of a private line within the thousand ids of one millisecond; places run from 0 to 499


def _millis(moment) -> int:
    if moment is None:
        return 0
    return calendar.timegm(moment.timetuple()) * 1000 + moment.microsecond // 1000


def _lines(rows, private) -> list:
    """[{id, title, content}] for (row id, title, text, moment) rows of one kind, the id made as the module says."""
    out, taken = [], {}
    for row_id, title, text, moment in sorted(rows, key=lambda row: row[0]):
        ms = _millis(moment)
        place = taken.get(ms, 0)
        taken[ms] = place + 1
        out.append({"id": ms * 1000 + (PRIVATE if private else 0) + min(place, PRIVATE - 1), "title": str(title or ""), "content": str(text or "")})
    return out


def lines_for(viewer) -> list:
    """[{id, title, content}] for a viewer (see viewer.current), oldest first."""
    team_id = viewer.team.id if viewer.team is not None else None
    user_id = viewer.user.id if viewer.user is not None else None
    mine = Notifications.team_id.is_(None) & Notifications.user_id.is_(None)
    if team_id is not None:
        mine = mine | (Notifications.team_id == team_id)
    if user_id is not None:
        mine = mine | (Notifications.user_id == user_id)
    lines = _lines(db.session.query(Notifications.id, Notifications.title, Notifications.content, Notifications.date).filter(mine).all(), False)
    if team_id is not None:
        lines += _lines(db.session.query(Note.id, Note.title, Note.text, Note.created_at).filter(Note.team_id == team_id).all(), True)
    lines.sort(key=lambda line: line["id"])
    return lines


def _version(lines) -> int:
    digest = hashlib.sha256("\x1f".join(f"{line['id']}\x1e{line['title']}\x1e{line['content']}" for line in lines).encode("utf-8")).hexdigest()
    return int(digest[:13], 16)  # 52 bits: a whole number a browser keeps exactly


def state_for(viewer) -> tuple:
    """(notif_id, notif_ver). `notif_id` is the newest id the viewer may read (0 for none); `notif_ver` changes when their list does (0 for none).
    For the crew the list is CTFd's own (every notification, CTFd's ids): what their `GET /api/v1/notifications?since_id=` answers from."""
    if viewer.admin:
        rows = db.session.query(Notifications.id, Notifications.title, Notifications.content).order_by(Notifications.id).all()
        lines = [{"id": int(row_id), "title": str(title or ""), "content": str(text or "")} for row_id, title, text in rows]
    else:
        lines = lines_for(viewer)
    if not lines:
        return 0, 0
    return lines[-1]["id"], _version(lines)
