"""Who is asking: the signed-in account, its studio, whether it is the crew, and what it has solved.

The gate answers the contract's four refusals in its order (signed in, banned, verified, has a studio). The crew (CTFd
administrators) pass without a studio and see what players see, with nothing solved. The platform runs in team mode; in user mode
nobody has a studio and every account is treated like the crew.
"""
from functools import wraps
from types import SimpleNamespace

from CTFd.models import Solves, Teams
from CTFd.plugins.l3mon_board.envelope import fail
from CTFd.utils import get_config
from CTFd.utils.config import is_teams_mode
from CTFd.utils.user import authed, get_current_user, is_admin


def refusal(need_team=True):
    """The refusal for this request, or None when the account may use the player endpoints. `need_team` False is for a thing every
    registered account may have (the cold-open comic): signed in, not suspended, verified when the settings ask for it."""
    if not authed():
        return fail("auth_required", 401, "Sign in to carry on.")
    user = get_current_user()
    if user is None:
        return fail("auth_required", 401, "Sign in to carry on.")
    if user.banned:
        return fail("banned", 403, "This account has been suspended.")
    if is_admin():
        return None
    if get_config("verify_emails") and not user.verified:
        return fail("unverified", 403, "Verify your email address to carry on.")
    if need_team and is_teams_mode() and user.team_id is None:
        return fail("no_team", 403, "Set up your studio to carry on.")
    return None


def player(view):
    """A route only a signed-in account may use (see refusal)."""

    @wraps(view)
    def guarded(*args, **kwargs):
        no = refusal()
        return no if no is not None else view(*args, **kwargs)

    return guarded


def current_team():
    """The asker's studio row, or None (the crew, a visitor, an account with no studio, user mode)."""
    user = get_current_user() if authed() else None
    if user is None or is_admin() or not is_teams_mode() or user.team_id is None:
        return None
    return Teams.query.get(user.team_id)


def current() -> SimpleNamespace:
    """The asker. `team` is the studio row (None for the crew and in user mode); `solved` the challenge ids the studio has solved."""
    user = get_current_user()
    admin = bool(is_admin())
    team = Teams.query.get(user.team_id) if (is_teams_mode() and user is not None and user.team_id is not None and not admin) else None
    solved = set()
    if team is not None:
        solved = {cid for (cid,) in Solves.query.with_entities(Solves.challenge_id).filter_by(account_id=team.id).all()}
    return SimpleNamespace(user=user, team=team, admin=admin, solved=solved)
