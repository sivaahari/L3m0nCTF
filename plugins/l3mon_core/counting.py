"""Who counts. One rule, CTFd's own, shared by the solve counts, the dynamic values, the standings and the CTFtime feed.

The account that counts is the account CTFd scores: the team in team mode, the user in user mode. It counts unless it is
banned or hidden. This is exactly the filter CTFd's own decay functions use (CTFd/plugins/dynamic_challenges/decay.py), and
the tests compare our numbers with CTFd's, so the two can never drift apart. A member of a counted team does not change the
team's standing, whoever of them solved (CTFd counts the account, not each member). Administrators are kept out by CTFd
itself, not by a rule of ours: the administrator CTFd creates at setup is hidden, and in team mode an administrator trying a
flag has no team. (A visible administrator in user mode would count, as in CTFd's own standings; the platform runs in team mode.)
"""
from sqlalchemy import func

from CTFd.models import Solves, Users, db
from CTFd.utils.modes import get_model

CHUNK = 500


def is_counted_team(team) -> bool:
    return team is not None and not team.banned and not team.hidden


def is_counted_user(user) -> bool:
    """A user whose own solves, awards and shares may be shown: not banned and not hidden, as CTFd reads it."""
    return user is not None and not user.banned and not user.hidden


def counted_solve_counts(challenge_ids) -> dict:
    """How many counted accounts solved each challenge, in one grouped query (several for a very long list, so no database's limit
    on a list of values is ever met). Every asked id is in the answer (0 for none); asking for an id twice is harmless."""
    ids = list(dict.fromkeys(challenge_ids))
    if not ids:
        return {}
    Model = get_model() or Users  # CTFd's setup always sets the mode; with none set, the user is the account
    found = {}
    for i in range(0, len(ids), CHUNK):
        rows = (
            db.session.query(Solves.challenge_id, func.count(Solves.id))
            .join(Model, Solves.account_id == Model.id)
            .filter(Solves.challenge_id.in_(ids[i : i + CHUNK]), Model.hidden == False, Model.banned == False)  # noqa: E712 (SQL, not Python)
            .group_by(Solves.challenge_id)
            .all()
        )
        found.update(dict(rows))
    return {i: int(found.get(i, 0)) for i in ids}


def counted_solve_count(challenge_id) -> int:
    return counted_solve_counts([challenge_id])[challenge_id]
