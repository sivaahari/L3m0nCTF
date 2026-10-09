"""Reconcile: CTFd's own challenge state follows the crew's plan.

The crew's plan (l3mon_channel, l3mon_programme) says which programmes are on air. CTFd's stock endpoints read the challenge's
own `state`, so that state is derived from the plan here and never set by hand (design decision C): `visible` for a
programme that is on air, `hidden` for everything else (including a challenge nobody put on a channel, once any channel
exists). With no channel at all this module does nothing and CTFd decides.

reconcile() makes the states match the plan *now*. For every challenge that has to change it runs one conditional update
(`... WHERE id = ? AND state <> ?`): only the call whose update changed a row owns that change, so when eight workers meet a
scheduled drop together the programme is shown once, announced once and reported once. After the commit it clears CTFd's
challenge and standings caches, moves the tick, tells the pull-back handlers, writes the announcement ("New on air": one line
per channel, counts only, never a name) and lets the scheduler work out the next moment something is due.

The `before_flush` hook is the safety net for hands: it forces `hidden` on anything shown that is not on air, and turns a hide
done in CTFd's own challenge editor into a withhold in the plan (otherwise the next reconcile would show it again). A challenge
editor request that tries to reveal a programme which is not on air gets a plain 400 first.
"""
import logging
import re
from collections import namedtuple

from flask import current_app, jsonify, request
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Challenges, Notifications, db
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.airing import is_on_air, on_air_ids, release_active
from CTFd.plugins.l3mon_core.clock import current_phase, now
from CTFd.plugins.l3mon_core import locks
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_core.text import plain_text
from CTFd.plugins.l3mon_core.tick import tick
from CTFd.schemas.notifications import NotificationSchema
from CTFd.utils.user import is_admin

_log = logging.getLogger("l3mon")

VISIBLE = "visible"
HIDDEN = "hidden"
TITLE = "New on air"

Result = namedtuple("Result", "shown hidden")  # the challenge ids this call put on air, and took off air

_pull_back_handlers = []  # called with the ids of programmes that were on air and no longer are (SP4 stops their instances)
_after_change = []  # called with the second reconcile ran at (the scheduler refreshes its next moment here)
_installed = False


def register_pull_back_handler(fn):
    if fn not in _pull_back_handlers:
        _pull_back_handlers.append(fn)


def clear_pull_back_handlers():
    _pull_back_handlers.clear()


def register_after_change(fn):
    if fn not in _after_change:
        _after_change.append(fn)


def desired_states(t=None) -> dict:
    """The state every challenge should have at second `t`; empty while release control is not in use."""
    if not release_active():
        return {}
    on_air = on_air_ids(t)
    return {cid: (VISIBLE if cid in on_air else HIDDEN) for (cid,) in db.session.query(Challenges.id).all()}


def _slugs(ids) -> str:
    rows = db.session.query(Programme.slug).filter(Programme.challenge_id.in_(ids)).order_by(Programme.number).all()
    return ", ".join(slug for (slug,) in rows)


def _claim(challenge_id, target) -> bool:
    """One conditional update. True when this call changed the row, i.e. when the change is ours to announce and report."""
    changed = Challenges.query.filter(Challenges.id == challenge_id, Challenges.state != target).update({"state": target}, synchronize_session=False)
    return changed == 1


def serialize():
    """Take the plan's lock (the shared one, in l3mon_core.locks, where its reasoning is written) and with it a fresh view of the
    database. Everything that changes the plan or acts on it takes this FIRST."""
    locks.serialize()


def reconcile(t=None, system=False, locked=False) -> Result:
    """Make CTFd's challenge states match the plan at second `t` (now by default). `system=True` when the clock, not a person,
    asked for it (the scheduler): the audit lines then name the system, not the visitor whose request happened to notice.
    `locked=True` when the caller has called serialize() already (it must, before it changes the plan)."""
    t = now() if t is None else t
    if not locked:
        serialize()  # the crew's calls have taken the lock already, before they changed anything
    want = desired_states(t)
    rows = db.session.query(Challenges.id, Challenges.state).order_by(Challenges.id).all() if want else []  # one fixed order, so racing workers never deadlock
    shown, hidden = [], []
    for cid, state in rows:
        target = want.get(cid)
        if target is None or state == target:
            continue
        if _claim(cid, target):
            if target == VISIBLE:
                shown.append(cid)
            elif state == VISIBLE:
                hidden.append(cid)
    if shown:
        audit.record("release.drop", "challenges", f"on air: {_slugs(shown)}", system=system)
    if hidden:
        audit.record("release.pull", "challenges", f"off air: {_slugs(hidden)}", system=system)
    db.session.commit()  # this also commits whatever the caller already had pending, so a plan change and its effect land together
    if shown or hidden:
        clear_challenges()
        clear_standings()
        try:
            tick.bump()
        except Exception:  # the counter is a convenience; it must never undo a release
            _log.warning("l3mon: the tick could not be moved after a release", exc_info=True)
        for handler in list(_pull_back_handlers) if hidden else []:
            try:
                handler(list(hidden))
            except Exception as error:  # noqa: BLE001  (a stopped instance host must not stop a release)
                _log.warning("l3mon: a pull-back handler failed: %r", error, exc_info=True)
        if shown:
            try:
                announce(shown, t)
            except Exception as error:  # noqa: BLE001
                _log.warning("l3mon: 'New on air' could not be announced: %r", error, exc_info=True)
    for hook in list(_after_change):
        try:
            hook(t)
        except Exception as error:  # noqa: BLE001
            _log.warning("l3mon: a release hook failed: %r", error, exc_info=True)
    return Result(shown, hidden)


def announce(shown_ids, t=None):
    """The "New on air" notice for programmes that just came on air: one line per channel (in channel order) with a count, never
    a name. Nothing before the start or after the end (a pause does not hold a drop back). Returns the notification or None."""
    t = now() if t is None else t
    if current_phase(t).state not in ("live", "paused") or not shown_ids:
        return None
    rows = (
        db.session.query(Channel.id, Channel.position, Channel.name, Programme.challenge_id)
        .join(Programme, Programme.channel_id == Channel.id)
        .filter(Programme.challenge_id.in_(list(shown_ids)))
        .all()
    )
    per_channel = {}
    for cid, position, name, _ in rows:
        per_channel.setdefault((position, cid), [name, 0])[1] += 1
    lines = [
        f"CH {position} · {plain_text(name)} has {count} new programme{'' if count == 1 else 's'}."
        for (position, _), (name, count) in sorted(per_channel.items())
    ]
    if not lines:
        return None
    note = Notifications(title=TITLE, content=" ".join(lines))
    db.session.add(note)
    db.session.commit()
    data = NotificationSchema().dump(note).data
    data["type"] = "alert"
    data["sound"] = True
    current_app.events_manager.publish(data=data, type="notification")
    return note


# ---- the hand guard ------------------------------------------------------------------------------------------------------

def _enforce(session, flush_context, instances):
    touched = []
    for obj in (*session.new, *session.dirty):
        if isinstance(obj, Challenges) and (obj in session.new or inspect(obj).attrs.state.history.has_changes()):
            touched.append(obj)
    if not touched:
        return
    with session.no_autoflush:
        if not release_active():
            return
        for chal in touched:
            _enforce_one(session, chal)


def _enforce_one(session, chal):
    history = inspect(chal).attrs.state.history
    was = history.deleted[0] if history.deleted else None
    if chal.state == VISIBLE:
        if chal.id is None or not is_on_air(chal.id):
            chal.state = HIDDEN
            audit.record("release.refuse", f"challenge:{chal.id}", "refused to show a challenge that is not on air; use Release control")
        return
    if was == VISIBLE and chal.id is not None:
        entry = session.query(Programme).filter(Programme.challenge_id == chal.id).first()
        if entry is not None and entry.release_state in ("released", "scheduled"):
            entry.release_state = "withheld"
            entry.release_at = None
            audit.record("release.withhold", f"programme:{entry.slug}", "hidden in the challenge editor, so withheld in the plan")


_PATCH = re.compile(r"^/api/v1/challenges/(\d+)/?$")


def _refuse_reveal_request():
    """A challenge editor request that tries to show a programme that is not on air: say why, plainly, before the safety net."""
    if request.method != "PATCH":
        return None
    found = _PATCH.match(request.path)
    if not found or not release_active() or not is_admin():
        return None
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("state") != VISIBLE:
        return None
    if is_on_air(int(found.group(1))):
        return None
    message = "Release control decides what is on air. Release this programme from Admin > Release control."
    return jsonify({"success": False, "errors": {"state": [message]}}), 400


def install(app=None):
    """Listen to every database session in the process (once) and refuse a reveal request in each app."""
    global _installed
    if not _installed:
        event.listen(Session, "before_flush", _enforce)
        _installed = True
    if app is not None and "l3mon_release_reveal" not in app.extensions:
        app.extensions["l3mon_release_reveal"] = True
        app.before_request(_refuse_reveal_request)
