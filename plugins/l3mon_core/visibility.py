"""What a player may see: the same set CTFd's own challenge list shows, never more.

A programme exists for a player only while it is `visible` in CTFd (hidden and locked are not) and every prerequisite CTFd knows
of has been solved by them. CTFd ignores a prerequisite that names no challenge and counts a hidden challenge as one that
exists; so do we. A challenge CTFd would show as an anonymous "???" placeholder (an unmet prerequisite with `anonymize`) is not
visible here at all, because for us a withheld programme looks exactly like one that does not exist. Administrators see
everything. (CTFd also shows a challenge in a state it does not know; we do not: only the word `visible` is visible.)

Hiding is CTFd's own `hidden` state plus the guards of 3.2 on the doors CTFd leaves open (design decision C), so the stock
pages and our endpoints cannot disagree. The crew's plan (airing.py) tightens the rule in this one place: once any channel
exists a player sees a programme only while it is on air, so every later endpoint asks here and never repeats the rule. With no
channel the plan is not in use and CTFd's own state alone decides.
"""
from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_core.airing import is_on_air, on_air_ids, release_active

VISIBLE = "visible"


def _prerequisites(requirements) -> set:
    return set((requirements or {}).get("prerequisites", []))


def is_visible(challenge, admin=False, solved_ids=frozenset(), t=None) -> bool:
    """True when this challenge may be shown to a viewer who has solved `solved_ids`. A missing challenge is shown to nobody."""
    if challenge is None:
        return False
    if admin:
        return True
    if challenge.state != VISIBLE:
        return False
    if release_active() and not is_on_air(challenge.id, t):
        return False
    needs = _prerequisites(challenge.requirements)
    if not needs:
        return True
    existing = {row[0] for row in db.session.query(Challenges.id).all()}  # only asked when there are prerequisites at all
    return (needs & existing) <= set(solved_ids)


def visible_challenge_ids(admin=False, solved_ids=frozenset(), t=None) -> set:
    """The ids of every challenge that may be shown to a viewer who has solved `solved_ids`, in one query (and one for the plan)."""
    rows = db.session.query(Challenges.id, Challenges.state, Challenges.requirements).all()
    if admin:
        return {cid for cid, _, _ in rows}
    existing = {cid for cid, _, _ in rows}
    solved = set(solved_ids)
    shown = {cid for cid, state, requirements in rows if state == VISIBLE and (_prerequisites(requirements) & existing) <= solved}
    if release_active():
        shown &= on_air_ids(t)
    return shown
