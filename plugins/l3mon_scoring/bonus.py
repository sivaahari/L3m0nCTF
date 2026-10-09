"""Bonus: the crew gives a studio TRP (or takes some away) with a message only that studio and the crew can read.

It is a plain CTFd award, so it counts everywhere TRP counts: the studio's score, the standings, the scoreboard and the CTFtime feed,
and it moves the studio's tie-break date to the date of the award (CTFd's rule: the date of the latest solve or award). The award's
NAME is its only public text, "Bonus +50 TRP" or "Adjustment -20 TRP"; its description is empty. The message lives in `l3mon_bonus`
and in the studio's private line (`l3mon_note`), so a message can never give a challenge away.

CTFd's team score sums each member's awards by user, so an award must carry a user: a team-wide bonus is attached to the captain and
marked `scope = team`, a bonus for one member to that member and marked `scope = member`. (A bonus goes with its account if that
account is deleted, as any award does in CTFd.)

A repeat is refused: the same studio, member, amount and message within a minute is almost certainly a double click, and a bonus is
the one action that cannot be sent twice without harm.
"""
import datetime

from CTFd.cache import clear_standings
from CTFd.models import Awards, Teams, Users, db
from CTFd.plugins.l3mon_core import audit, locks
from CTFd.plugins.l3mon_core.models import Bonus
from CTFd.plugins.l3mon_core.text import crew_text
from CTFd.plugins.l3mon_scoring import notes
from CTFd.plugins.l3mon_scoring.errors import Refused
from CTFd.utils.config import is_teams_mode

MAX_TRP = 1000
MESSAGE_LIMIT = 200
REPEAT_SECONDS = 60
CATEGORY = "bonus"


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def title_for(trp: int) -> str:
    return f"{'Bonus' if trp > 0 else 'Adjustment'} {trp:+d} TRP"


def give(team_id=None, user_id=None, trp=None, message=None, actor=None) -> dict:
    """Give a studio `trp` (a whole number from -1000 to 1000, never 0) with `message`. `user_id` is an optional member of the studio.
    -> {"award_id", "team_id", "user_id", "scope", "trp", "title"}. Raises Refused, and then changes nothing."""
    if not is_teams_mode():
        raise Refused({"team_id": ["the platform runs in team mode"]})
    problems = {}
    if not _whole(team_id):
        problems["team_id"] = ["must be a whole number"]
    if user_id is not None and not _whole(user_id):
        problems["user_id"] = ["must be a whole number"]
    if not _whole(trp) or trp == 0 or abs(trp) > MAX_TRP:
        problems["trp"] = [f"must be a whole number from -{MAX_TRP} to {MAX_TRP}, and not 0"]
    text, why = crew_text(message, MESSAGE_LIMIT)
    if why:
        problems["message"] = [why]
    if problems:
        raise Refused(problems)

    locks.serialize()  # one crew action at a time, on current data: the repeat check below cannot be raced
    team = Teams.query.filter_by(id=team_id).first()
    if team is None:
        raise Refused({"team_id": ["no such studio"]}, 404)
    if user_id is None:
        member = Users.query.filter_by(id=team.captain_id).first() if team.captain_id else None
        if member is None or member.team_id != team.id:
            raise Refused({"user_id": ["the studio has no captain; choose one of its members"]})
        scope = "team"
    else:
        member = Users.query.filter_by(id=user_id).first()
        if member is None or member.team_id != team.id:
            raise Refused({"user_id": ["is not a member of this studio"]})
        scope = "member"

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=REPEAT_SECONDS)
    repeat = (
        db.session.query(Bonus.id)
        .join(Awards, Awards.id == Bonus.award_id)
        .filter(Bonus.team_id == team.id, Bonus.user_id == member.id, Bonus.message == text, Awards.value == trp, Bonus.given_at >= cutoff)
        .first()
    )
    if repeat:
        raise Refused({"message": ["the same bonus was given to this studio a moment ago; it looks like a double click"]}, 409)

    title = title_for(trp)
    award = Awards(user_id=member.id, team_id=team.id, name=title, description="", value=trp, category=CATEGORY)
    db.session.add(award)
    db.session.flush()
    who = actor if actor is not None else audit.acting_user()
    db.session.add(Bonus(award_id=award.id, team_id=team.id, user_id=member.id, scope=scope, message=text, given_by=getattr(who, "id", None)))
    notes.tell(team.id, title, text)
    audit.record("scoring.bonus", team.name, f"{title} to {member.name if scope == 'member' else 'the studio'}; {text}", actor=actor)
    db.session.commit()
    clear_standings()
    return {"award_id": award.id, "team_id": team.id, "user_id": member.id, "scope": scope, "trp": trp, "title": title}
