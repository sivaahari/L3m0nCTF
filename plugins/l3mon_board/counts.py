"""How many studios have solved a programme, as one viewer may know it.

Live, that is CTFd's own count: the studios that count (not banned, not hidden). While the scoreboard is frozen CTFd's count is the one at the
freeze, so the public cannot learn that anybody solved since. A studio always knows its own solves, so the count it is told also holds its own
solve of a programme made at or after the freeze (when the studio counts at all: a hidden or suspended studio is never in a count, not even in
its own). The board, the programme panel, the Guide's story meter and the reply to a flag all say the same number through here.
"""
from CTFd.models import Solves, db
from CTFd.plugins.l3mon_core.clock import window
from CTFd.plugins.l3mon_core.counting import is_counted_team
from CTFd.utils.challenges import get_solve_counts_for_challenges
from CTFd.utils.dates import unix_time_to_utc


def own_late(team) -> set:
    """The challenge ids this studio solved at or after the freeze (nothing when no freeze is set or the studio does not count)."""
    freeze = window().freeze
    if team is None or not freeze or not is_counted_team(team):
        return set()
    rows = db.session.query(Solves.challenge_id).filter(Solves.account_id == team.id, Solves.date >= unix_time_to_utc(freeze)).all()
    return {challenge_id for (challenge_id,) in rows}


def for_viewer(viewer) -> dict:
    """{challenge id: count} for every challenge CTFd counts, as this viewer (see viewer.current) may know it."""
    counts = {challenge_id: int(count) for challenge_id, count in (get_solve_counts_for_challenges(admin=False) or {}).items()}
    for challenge_id in own_late(viewer.team):
        counts[challenge_id] = counts.get(challenge_id, 0) + 1
    return counts


def for_challenge(team, challenge_id, public) -> int:
    """The one challenge's count: CTFd's own (`public`) plus the studio's own later solve of it."""
    return int(public or 0) + (1 if challenge_id in own_late(team) else 0)
