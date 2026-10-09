"""The one lock the crew's actions share.

Release control (3.2) and scoring (3.3) both change what players see and both must not run at once against each other's stale view,
so they take the same lock first.
"""
from CTFd.models import Configs, db
from CTFd.plugins.l3mon_core.models import Channel

LOCK_KEY = "l3mon_plan_lock"  # the settings row the lock falls back to while there is no channel


def _fallback_row_id() -> int:
    """The id of the settings row that stands in for a channel. Created once; if two workers create it at the same moment both rows
    exist, and everyone locks the one with the lowest id."""
    found = db.session.query(Configs.id).filter(Configs.key == LOCK_KEY).order_by(Configs.id).first()
    if found is None:
        db.session.add(Configs(key=LOCK_KEY, value="1"))
        db.session.commit()
        found = db.session.query(Configs.id).filter(Configs.key == LOCK_KEY).order_by(Configs.id).first()
    return found.id


def serialize():
    """Take the plan's lock, and with it a fresh view of the database. Everything that changes the plan or acts on it (the crew's
    calls, a reconcile by the scheduler, a Revoke or a Restore, a Bonus) takes this FIRST, so two of them never run at once against
    each other's stale view.

    The lock is a row lock (SELECT ... FOR UPDATE) on the lowest channel, held until the transaction ends; it blocks only the
    others that ask for it, never a player's read or a player's solve (no player route touches a channel). While there is no channel
    yet (set-up, a rehearsal) a settings row stands in: a locking read of an EMPTY table takes only a gap lock, and gap locks do not
    block each other (found by the independent audit of 3.3: eight simultaneous bonuses were all given). The commit before the lock
    ends the read transaction CTFd's own request hooks may have started: MariaDB's default isolation (REPEATABLE READ) shows a
    transaction the data as it was at its first plain read, and a reconcile that judged the plan from such an old view could skip a
    change another worker had made in between (found by the independent review of 3.2). The locking read itself starts no view, so
    the first plain read after it sees everything committed before the lock was won. On SQLite (the tests) there is one writer at a
    time and the lock does nothing."""
    db.session.commit()
    if db.session.query(Channel.id).order_by(Channel.id).limit(1).with_for_update().first() is None:
        row_id = _fallback_row_id()
        # Finding the row was a plain read, which starts MariaDB's read view; the view must not predate the lock, or the reads after
        # it would be as stale as before. End it, so that the locking read is the first statement of a new transaction.
        db.session.commit()
        db.session.query(Configs.id).filter(Configs.id == row_id).with_for_update().first()
