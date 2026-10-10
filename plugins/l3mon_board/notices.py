"""The bell's route: `GET /api/v1/notifications?since_id=`, and CTFd's own notification routes closed to everyone but the crew.

Stock CTFd answers `/api/v1/notifications` to anybody, signed in or not, with every notification and its date, its studio and its account;
anybody may filter it by studio, read one by its id, and the stock page `/notifications` lists them all. `/events` pushes each notification
made through the API, an addressed one included, to every signed-in account. A line meant for one studio would be public. So, before CTFd's
own view runs, for everyone but the crew:

- the list (GET, HEAD) is the contract's: the viewer's own list (news.py), `[{id, title, content}]` newer than `since_id`, with the gate of
  the other player endpoints (401 for a visitor, 403 for a suspended account or a player with no studio) and 400 `invalid` for a
  `since_id` that is not a whole number. The stock filters (`team_id`, `q`, `title` ...) are ignored;
- **anything under `/api/v1/notifications/`** is refused as an id that was never there. CTFd reads the text after the slash as a number
  leniently (`+1`, `1.0`, `1e0`, a space around it, and on MariaDB even `1abc`), so no spelling of an id is trusted: the whole address space is closed;
- the stock page `/notifications` and the event stream `/events` are closed (404) until the theme brings its own (SP6, which polls the tick).

The crew keeps CTFd's own answers (they need the raw rows, and the admin pages use the event stream), and only the crew can write.
"""
from flask import abort, request

from CTFd.plugins.l3mon_board import envelope, news, viewer
from CTFd.utils.user import is_admin

ON = {"guard": True}
LIST = "/api/v1/notifications"
PAGE = "/notifications"
STREAM = "/events"


def guard():
    """Before CTFd's view; None to carry on with CTFd's own answer."""
    if not ON["guard"] or request.method not in ("GET", "HEAD") or is_admin():
        return None
    path = request.path
    bare = path.rstrip("/")
    if bare in (PAGE, STREAM):
        abort(404)
    if bare == LIST:
        return _list()
    if path.startswith(LIST + "/"):
        return viewer.refusal() or envelope.fail("not_found", 404, "There is no such notification.")
    return None


def _list():
    refusal = viewer.refusal()
    if refusal is not None:
        return refusal
    since = request.args.get("since_id")
    newer = 0
    if since not in (None, ""):
        try:
            newer = int(since)
        except ValueError:
            return envelope.fail("invalid", 400, "since_id must be a whole number.", errors={"since_id": ["must be a whole number"]})
    return envelope.ok([line for line in news.lines_for(viewer.current()) if line["id"] > newer])


def after(response):
    """CTFd's own check refuses a suspended account with a page before our guard runs; on this route it is the contract's JSON."""
    if request.path.rstrip("/") == LIST and response.status_code == 403 and response.mimetype == "text/html":
        return envelope.fail("banned", 403, "This account has been suspended.")
    return response


def install(app):
    if "l3mon_board_notices" not in app.extensions:
        app.extensions["l3mon_board_notices"] = True
        app.before_request(guard)
        app.after_request(after)
