"""Revoke and Restore: set a broken challenge's solves aside, and put them back.

Why our own action. CTFd's "mark incorrect" deletes the solve AND the submission (measured on 3.8.8): nothing is left of how it was
solved, and a dynamic challenge's value stays stale. Revoke instead:

  - deletes the `solves` row (so the TRP comes off the studio, the standings and the solve count at once) and turns the SUBMISSION
    into a `discard`, which CTFd does not count as an attempt: who sent what, from where and when stays;
  - writes a `Void` record for each solve (who, when it was solved, who voided it, when, why);
  - tells the studio, in its private notes, with the crew's reason;
  - recalculates the challenge's value (a dynamic one is worth its start value again when nobody holds a solve).

Restore reverses it exactly: for each studio it puts back the EARLIEST solve that was set aside (same submission id, same date, so the
totals and the tie-break position come back exactly), marks the studio's other records `superseded`, and marks `skipped` every record
of a studio that has solved the challenge again meanwhile (it keeps that one) or whose solve cannot come back (the member or the
submission is gone, or the crew changed the submission by hand). The studio is told.

Safe to repeat and to run at once on several workers: every step is a conditional statement whose row count decides who acted (the
`solves` row is deleted by exactly one Revoke; a submission is turned back by exactly one Restore), and both take the shared plan lock
(l3mon_core.locks) and then the challenge's own row, EXCLUSIVELY and first.

Why the challenge row first (found by the independent audit, reproduced on MariaDB): inserting a void record or a solve takes InnoDB's
shared lock on the challenge it points at, and the value update later needs the exclusive one. With CTFd's own update of that value (after
a player's solve) arriving in between, the upgrade is a deadlock, and the loser was the player's request. Taking the exclusive lock first
makes a player's solve wait for a moment instead. If a player's solve is already between its own two steps when the crew action asks,
InnoDB picks the smaller transaction as the loser, which is the crew action; it is rolled back and tried again (up to three times).
"""
import datetime
import functools
import logging
import time

from sqlalchemy import delete, insert, update
from sqlalchemy.exc import IntegrityError, OperationalError

from CTFd.models import Challenges, Solves, Submissions, Teams, db
from CTFd.plugins.l3mon_core import audit, locks
from CTFd.plugins.l3mon_core.models import Void
from CTFd.plugins.l3mon_core.text import crew_text
from CTFd.plugins.l3mon_scoring import notes, values
from CTFd.plugins.l3mon_scoring.errors import Refused
from CTFd.utils.config import is_teams_mode

_log = logging.getLogger("l3mon")
LOCK_CONFLICTS = (1213, 1205)  # MariaDB: deadlock found, lock wait timeout
SAVEPOINT_GONE = 1305
ATTEMPTS = 3
REASON_LIMIT = 500
DEFAULT_RESTORE_NOTE = "The crew put your solve back; it counts again."


class _CannotPutBack(Exception):
    pass


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _open(challenge_id, reason, required):
    """Validate the request and take the lock. -> (challenge, clean reason)."""
    if not is_teams_mode():
        raise Refused({"challenge_id": ["the platform runs in team mode"]})
    problems = {}
    if not _whole(challenge_id):
        problems["challenge_id"] = ["must be a whole number"]
    text, why = crew_text(reason, REASON_LIMIT, required=required)
    if why:
        problems["reason"] = [why]
    if problems:
        raise Refused(problems)
    locks.serialize()  # one crew action at a time, on current data
    challenge = Challenges.query.filter_by(id=challenge_id).with_for_update().first()  # and the challenge's own row, exclusively, first
    if challenge is None:
        raise Refused({"challenge_id": ["no such challenge"]}, 404)
    return challenge, text


def _is_a_lock_conflict(error) -> bool:
    """A deadlock or a lock wait timeout, or the 'SAVEPOINT ... does not exist' (1305) that follows a deadlock lost inside a savepoint:
    InnoDB rolled the whole transaction back, so the savepoint is gone when SQLAlchemy tries to release it (found by the audit)."""
    orig = getattr(error, "orig", None)
    code = orig.args[0] if getattr(orig, "args", None) else None
    return code in LOCK_CONFLICTS or (code == SAVEPOINT_GONE and "SAVEPOINT" in str(orig).upper())


def _retrying(action):
    """Run a crew action again, from the start, when the database picked it as the loser of a lock conflict. Anything else is raised."""

    @functools.wraps(action)
    def run(*args, **kwargs):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return action(*args, **kwargs)
            except OperationalError as error:
                db.session.rollback()
                if not _is_a_lock_conflict(error) or attempt == ATTEMPTS:
                    raise
                _log.warning("l3mon: %s lost a lock conflict (attempt %s of %s); trying again", action.__name__, attempt, ATTEMPTS)
                time.sleep(0.05 * attempt)

    return run


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


@_retrying
def revoke(challenge_id=None, reason=None, actor=None) -> dict:
    """Set aside every solve of a challenge. -> {"challenge_id", "name", "voided", "teams", "value_before", "value_after"}.
    Raises Refused (and changes nothing) for a bad request, an unknown challenge, or a challenge nobody holds a solve of."""
    challenge, text = _open(challenge_id, reason, required=True)
    name, before = challenge.name, challenge.value
    rows = (
        db.session.query(Solves.id, Solves.team_id, Solves.user_id, Solves.date)
        .filter(Solves.challenge_id == challenge_id)
        .order_by(Solves.id)
        .all()
    )
    who = actor if actor is not None else audit.acting_user()
    who_id = getattr(who, "id", None)
    solves_t, submissions_t = Solves.__table__, Submissions.__table__

    team_ids = []
    for submission_id, team_id, user_id, solved_at in rows:
        gone = db.session.execute(delete(solves_t).where(solves_t.c.id == submission_id))
        if gone.rowcount != 1:
            continue  # another Revoke set this one aside between our read and our delete
        db.session.execute(update(submissions_t).where(submissions_t.c.id == submission_id).values(type="discard"))
        db.session.add(
            Void(challenge_id=challenge_id, team_id=team_id, user_id=user_id, submission_id=submission_id, solved_at=solved_at,
                 voided_by=who_id, reason=text, outcome="open")
        )
        notes.tell(team_id, "Solve voided", text)
        team_ids.append(team_id)
    if not team_ids:
        db.session.rollback()
        raise Refused({"challenge_id": ["nobody holds a solve of this challenge; there is nothing to set aside"]}, 409)

    values.mark_changed()  # solves were deleted: the standings, scores and challenge lists are stale whether or not a value moves
    db.session.flush()
    changed = values.recalculate([challenge_id])
    after = changed[0][2] if changed else before
    teams = [t.name for t in Teams.query.filter(Teams.id.in_(team_ids)).order_by(Teams.id).all()]
    audit.record("scoring.revoke", name, f"{_plural(len(team_ids), 'solve')} set aside; value {before} -> {after}; {text}", actor=actor)
    db.session.commit()
    return {"challenge_id": challenge_id, "name": name, "voided": len(team_ids), "teams": teams, "value_before": before, "value_after": after}


def _put_back(record) -> bool:
    """Make the solve of one void record count again. False (and nothing changed) when it cannot come back."""
    if record.submission_id is None or record.user_id is None:
        return False
    sub = (
        db.session.query(Submissions.type, Submissions.challenge_id, Submissions.team_id, Submissions.user_id)
        .filter(Submissions.id == record.submission_id)
        .first()
    )
    if sub is None or sub.type != "discard" or (sub.challenge_id, sub.team_id, sub.user_id) != (record.challenge_id, record.team_id, record.user_id):
        return False
    solves_t, submissions_t = Solves.__table__, Submissions.__table__
    try:
        with db.session.begin_nested():  # a failure here undoes only this solve, not the whole Restore
            turned = db.session.execute(
                update(submissions_t).where(submissions_t.c.id == record.submission_id, submissions_t.c.type == "discard").values(type="correct")
            )
            if turned.rowcount != 1:
                raise _CannotPutBack()  # another Restore turned it back first
            db.session.execute(
                insert(solves_t).values(id=record.submission_id, challenge_id=record.challenge_id, user_id=record.user_id, team_id=record.team_id)
            )
    except (IntegrityError, _CannotPutBack):  # the member solved it again for another studio, or a race
        return False
    return True


def _resolve(record, outcome, who_id, when):
    """Close one open record. The condition is the claim: a record is resolved by exactly one Restore."""
    table = Void.__table__
    done = db.session.execute(
        update(table)
        .where(table.c.id == record.id, table.c.outcome == "open")
        .values(outcome=outcome, restored_at=when if outcome == "restored" else None, restored_by=who_id if outcome == "restored" else None)
    )
    return done.rowcount == 1


@_retrying
def restore(challenge_id=None, reason="", actor=None) -> dict:
    """Put back the solves a Revoke set aside. -> {"challenge_id", "name", "restored", "skipped", "superseded", "value_before", "value_after"}."""
    challenge, text = _open(challenge_id, reason, required=False)
    name, before = challenge.name, challenge.value
    records = Void.query.filter_by(challenge_id=challenge_id, outcome="open").order_by(Void.voided_at, Void.id).all()
    if not records:
        db.session.rollback()
        raise Refused({"challenge_id": ["nothing is set aside for this challenge; there is nothing to restore"]}, 409)

    who = actor if actor is not None else audit.acting_user()
    who_id = getattr(who, "id", None)
    when = datetime.datetime.utcnow()
    by_team = {}
    for record in records:
        by_team.setdefault(record.team_id, []).append(record)

    counts = {"restored": 0, "skipped": 0, "superseded": 0}
    for team_id, group in by_team.items():
        holds = db.session.query(Solves.id).filter(Solves.challenge_id == challenge_id, Solves.team_id == team_id).first() is not None
        back = False
        for record in group:
            if back:
                outcome = "superseded"  # its earlier solve has come back instead
            elif holds:
                outcome = "skipped"  # it solved the challenge again and keeps that solve
            elif _put_back(record):
                outcome, back = "restored", True
                notes.tell(team_id, "Solve restored", text or DEFAULT_RESTORE_NOTE)
            else:
                outcome = "skipped"
            if not _resolve(record, outcome, who_id, when):
                if outcome == "restored":
                    raise RuntimeError("a void record was resolved by someone else after its solve was put back")  # rolls everything back
                continue  # another Restore got to this record first (only possible when there is no channel row to lock)
            counts[outcome] += 1
    if not sum(counts.values()):  # every record was taken by another Restore meanwhile: this one has nothing to do
        db.session.rollback()
        raise Refused({"challenge_id": ["nothing is set aside for this challenge; there is nothing to restore"]}, 409)

    if counts["restored"]:
        values.mark_changed()  # solves were put back: the same caches are stale
    db.session.flush()
    changed = values.recalculate([challenge_id])
    after = changed[0][2] if changed else before
    detail = f"{counts['restored']} restored, {counts['skipped']} skipped, {counts['superseded']} superseded; value {before} -> {after}"
    audit.record("scoring.restore", name, detail + (f"; {text}" if text else ""), actor=actor)
    db.session.commit()
    return {"challenge_id": challenge_id, "name": name, **counts, "value_before": before, "value_after": after}
