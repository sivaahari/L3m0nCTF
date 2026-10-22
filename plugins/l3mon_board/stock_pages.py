"""CTFd's own team and player pages, closed to everyone but the crew (owner's decision, 22 October 2026).

Stock CTFd shows to anybody, signed in or not, the list of studios (`/teams`), a studio's public page (`/teams/<id>`), the list of players
(`/users`), a player's public page (`/users/<id>`) and a player's own profile (`/user`), each with the programmes that studio or player has solved.
None of them is in the platform's design (a studio is met through the Guide and the scoreboard), their look is CTFd's own, and the strict content
security policy cannot go on while a stock page is open. So, before CTFd's own view runs, for everyone but the crew, they answer 404 (the way
`notices.py` closes `/notifications`): to a visitor, to a player, and to a player asking for their own studio's or their own page.

Deny by default, so that no spelling of an address is trusted (`/teams/01`, `/teams/1/`, `/users/+1`: CTFd's router accepts some and a hook that
tries to recognise "an id" would miss others): everything under `/users/` and `/user/`, and everything under `/teams/` except the three routes of
the way into a studio, which the design uses (`/teams/new`, `/teams/join`, `/teams/invite`). `/team` (a studio's own page) and `/settings` are not
under these prefixes and stay open. The crew keeps CTFd's own answers (the admin pages link to them).

The API is not touched here: `/api/v1/teams/<id>`, `/api/v1/users/<id>` and their lists are guarded by `l3mon_release` (nothing withheld) and
`l3mon_scoring` (nothing frozen), and the doors tests of both prove it.
"""
from flask import abort, request

from CTFd.utils.user import is_admin

ON = {"guard": True}
WAY_IN = frozenset({"new", "join", "invite"})  # the routes under /teams/ that the design uses (a studio is made, joined, or joined by an invite)


def closes(path):
    """True for the stock pages above, however the address is spelt."""
    bare = path.rstrip("/")
    if bare in ("/teams", "/users", "/user"):
        return True
    if path.startswith(("/users/", "/user/")):
        return True
    if path.startswith("/teams/"):
        return path[len("/teams/"):].split("/", 1)[0] not in WAY_IN
    return False


def guard():
    """Before CTFd's view; None to carry on with CTFd's own answer."""
    if not ON["guard"]:
        return None
    if not closes(request.path):
        return None  # not one of these pages: no need to ask who is asking (is_admin reads the cache), and this hook runs on every request
    if is_admin():
        return None
    abort(404)


def install(app):
    if "l3mon_board_stock_pages" not in app.extensions:
        app.extensions["l3mon_board_stock_pages"] = True
        app.before_request(guard)
