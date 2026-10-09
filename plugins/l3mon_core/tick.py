"""The tick: one number that changes whenever something a player can see may have changed.

The board, the Guide and the scoreboard ask the tick every 15 seconds and fetch their data only when it differs from the last
one they saw (they compare for difference, never for order). It has two parts, joined by `signature()`:

- a counter in the cache (Redis in production) under `l3mon:ver`, which moves on every committed change players can see;
- the phase of the broadcast (before, live, paused, ended, and frozen), because the clock changes the picture with nothing
  committed at all: 09:00 IST, the end and the freeze second are moments, not writes. Later plugins add their own part with
  `add_signature_part` (none yet: release control needs none, because the scheduler applies a due drop inside the request that
  notices it and moves the counter at once).

The counter:
- starts at a random big number, so if the cache is flushed or restarted the new number is never the one a client holds;
- is moved by Redis's atomic INCRBY, so any number of workers can move it at once without losing a step. On a cache that is not
  Redis (the tests' memory cache, a filesystem cache) it can lose a step under a race and restarts from a random number when
  the cache's own timeout drops the key; for a number only compared for difference that is harmless;
- repairs itself: a key lost between the read and the increment, or a key the cache cannot increment (something stored a
  pickle under it), starts again from a fresh random number instead of coming back small or failing for ever.

`install()` makes every committed change to what players see move the counter once: a solve, an award, a hint unlock, a
challenge and its hints, files, flags and tags, a team, a user, a bracket, a public notification, a setting (the start, the end,
paused, the freeze and ours all live in settings) and our own tables. Bulk deletes and updates (CTFd's admin API uses them to
delete a challenge or a user) are caught through the session's bulk events. One commit moves it once however many rows it
touched; a rollback, a read, a wrong flag (`Fails`) or a tracking row moves nothing. A rolled-back savepoint forgets only itself.

Inside a web request the counter moves when the request ends, not at the commit: CTFd clears its own caches after the commit
returns, and a request that read the new number in between would pair it with old data. Outside a request (a script, a test) it
moves at once. A counter that cannot be moved (the cache is down) is logged and never stops the commit.
"""
import logging
import secrets

from flask import g, has_request_context
from sqlalchemy import event
from sqlalchemy.orm import Session

from CTFd.cache import cache
from CTFd.models import (
    Awards,
    Brackets,
    ChallengeFiles,
    Challenges,
    ChallengeTopics,
    Configs,
    Flags,
    Hints,
    Notifications,
    Solves,
    Tags,
    Teams,
    Topics,
    Unlocks,
    Users,
)
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.models import Bonus, Channel, Note, Programme, Void

KEY = "l3mon:ver"
WATCHED = (
    Solves, Awards, Unlocks, Challenges, Hints, ChallengeFiles, Flags, Tags, Topics, ChallengeTopics, Teams, Users, Brackets,
    Notifications, Configs, Channel, Programme, Void, Bonus, Note,
)  # fmt: skip

_FLAG = "l3mon_tick_pending"
_REQUEST_FLAG = "l3mon_tick_after_request"
_log = logging.getLogger("l3mon")
_installed = False
_parts = []


class Tick:
    def __init__(self, backend, key=KEY):
        self._backend = backend  # a function giving a cachelib-style cache (get, inc, delete)
        self.key = key

    @staticmethod
    def _start() -> int:
        return 10**9 + secrets.randbelow(10**9)

    def value(self) -> int:
        backend = self._backend()
        found = backend.get(self.key)
        if found is None:
            # Created by the same atomic INCRBY that later bumps it. cachelib's add() would store a pickle, which Redis cannot
            # increment (found against a real Redis). Two workers racing here add their two random starts: still random.
            found = backend.inc(self.key, self._start())
        return int(found)

    def bump(self) -> int:
        self.value()  # makes sure the key exists, at a random start
        backend = self._backend()
        try:
            moved = backend.inc(self.key)
        except Exception:  # the cache cannot increment what is stored there: throw it away and start again
            backend.delete(self.key)
            moved = backend.inc(self.key, self._start())
        if moved is None:
            raise RuntimeError("the cache did not move the tick")
        moved = int(moved)
        if moved < 10**9:  # the key vanished between value() and inc, and INCR made a 1, which a client could already hold
            moved = int(backend.inc(self.key, self._start()))
        return moved


tick = Tick(lambda: cache.cache)


def add_signature_part(fn):
    """Add a part to `signature()`: a function that returns something printable and changes when what players see changes."""
    if fn not in _parts:
        _parts.append(fn)


def signature(t=None) -> str:
    """The number players poll: the counter, the phase and frozen state (now, or at second `t`), then the later plugins' parts."""
    phase = current_phase(t)
    code = phase.state[0] + ("f" if phase.frozen else "n")  # b, l, p or e; then n (not frozen) or f
    return ".".join([str(tick.value()), code, *[str(part()) for part in _parts]])


def _bump_quietly():
    try:
        tick.bump()
    except Exception:  # never let the counter stop a commit that already happened
        _log.warning("l3mon: the tick could not be moved", exc_info=True)


def _noted(session, context):
    if session.info.get(_FLAG):
        return
    for row in (*session.new, *session.dirty, *session.deleted):
        if isinstance(row, WATCHED):
            session.info[_FLAG] = True
            return


def _noted_bulk(context):
    """Query.update() and Query.delete() skip the flush events; CTFd's admin API deletes challenges and users that way."""
    if issubclass(context.mapper.class_, WATCHED):
        context.session.info[_FLAG] = True


def _committed(session):
    if not session.info.pop(_FLAG, False):
        return
    if has_request_context():
        g.setdefault(_REQUEST_FLAG, True)  # moved when the request ends, after CTFd has cleared its own caches
    else:
        _bump_quietly()


def _forgotten(session, previous_transaction):
    """A whole transaction rolled back: its changes never happened. A rolled-back savepoint forgets nothing before it."""
    if previous_transaction.parent is None:
        session.info.pop(_FLAG, None)


def _request_ended(error=None):
    if g.pop(_REQUEST_FLAG, False):
        _bump_quietly()


def install(app=None):
    """Listen to every database session in the process (once, however many apps are created: the tests create many), and
    make each app move the counter when a request that changed something ends."""
    global _installed
    if not _installed:
        event.listen(Session, "after_flush", _noted)
        event.listen(Session, "after_bulk_update", _noted_bulk)
        event.listen(Session, "after_bulk_delete", _noted_bulk)
        event.listen(Session, "after_commit", _committed)
        event.listen(Session, "after_soft_rollback", _forgotten)
        _installed = True
    if app is not None and "l3mon_tick" not in app.extensions:
        app.extensions["l3mon_tick"] = True
        app.teardown_request(_request_ended)
