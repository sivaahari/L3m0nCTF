"""The one lock the crew's actions share.

Release control (3.2) and scoring (3.3) both change what players see and both must not run at once against each other's stale view,
so they take the same lock first.
"""
from CTFd.models import db
from CTFd.plugins.l3mon_core.models import Channel


def serialize():
    """Take the plan's lock, and with it a fresh view of the database. Everything that changes the plan or acts on it (the crew's
    calls, a reconcile by the scheduler, a Revoke or a Restore) takes this FIRST, so two of them never run at once against each
    other's stale view.

    The lock is a row lock (SELECT ... FOR UPDATE) on the lowest channel, held until the transaction ends; it blocks only the
    others that ask for it, never a player's read or a player's solve (no player route touches a channel). The commit before it ends
    the read transaction CTFd's own request hooks may have started: MariaDB's default isolation (REPEATABLE READ) shows a transaction
    the data as it was at its first plain read, and a reconcile that judged the plan from such an old view could skip a change
    another worker had made in between (found by the independent review of 3.2). The locking read itself starts no view, so the
    first plain read after it sees everything committed before the lock was won. On SQLite (the tests) there is one writer at a
    time and the lock does nothing."""
    db.session.commit()
    db.session.query(Channel.id).order_by(Channel.id).limit(1).with_for_update().first()
