"""The tick the pages poll every 15 seconds: `{ver, notif_ver, own}`.

ver        the platform's tick (a counter and the phase, `l3mon_core.tick.signature`): one string for every studio. The pages compare
           it for difference and fetch the board only when it changed.
notif_ver  changes when the news the viewer may read changes: public lines, lines addressed to the studio or the account, and the studio's
           own private lines, in one list (news.py). For the crew it is CTFd's own list.
own        how many things have happened to the signed-in studio: its solves, hint unlocks, awards (a bonus is one), private lines
           and voids. It only goes up when the studio gains something, and a void adds a record and a line as a solve leaves, so a
           Revoke also moves it. Pages follow `ver` OR `own`: a teammate's solve reaches an open page even when the shared tick is
           held back (3.6). 0 for the crew.
"""
from sqlalchemy import func

from CTFd.models import Awards, Solves, Unlocks, db
from CTFd.plugins.l3mon_board import news
from CTFd.plugins.l3mon_core.models import Note, Void
from CTFd.plugins.l3mon_core.tick import signature


def own(team_id) -> int:
    """The studio's count, in one query."""
    if team_id is None:
        return 0

    def count(model):
        return db.session.query(func.count(model.id)).filter(model.team_id == team_id).scalar_subquery()

    return int(db.session.query(count(Solves) + count(Unlocks) + count(Awards) + count(Note) + count(Void)).scalar() or 0)


def answer(viewer) -> dict:
    """The tick for a viewer (see viewer.current)."""
    return {"ver": signature(), "notif_ver": news.state_for(viewer)[1], "own": own(viewer.team.id if viewer.team is not None else None)}
