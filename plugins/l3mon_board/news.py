"""The news a viewer may read, as the two numbers the pages need: the newest id and a version that changes whenever the list does.

Today this is CTFd's public notifications. Part 3.5 adds the studio's private lines (a void, a restore, a bonus) to the same
answer with one numbering per viewer; the board and the tick call only `state_for`, so they do not change then.
"""
from sqlalchemy import func

from CTFd.models import Notifications, db

_STEP = 100_000  # a list never gets this long; the version is (newest id, count) in one whole number


def state_for(team) -> tuple:
    """(notif_id, notif_ver) for a viewer. `notif_id` is the newest id they may read (0 for none); `notif_ver` changes when a line is
    added or taken away. Both ids and counts are whole numbers, so a page can compare them for difference."""
    newest, count = db.session.query(func.max(Notifications.id), func.count(Notifications.id)).one()
    newest = int(newest or 0)
    return newest, newest * _STEP + min(int(count), _STEP - 1)
