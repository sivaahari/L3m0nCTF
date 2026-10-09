"""The doors CTFd leaves open.

With a challenge `hidden`, CTFd's own routes by id already answer as for an id that never existed (the challenge, its solves, its
hints, an unlock, a flag attempt, a rating). Probing CTFd 3.8.8 (and the independent review of this part) found seven things
that still name or hand out a programme that is not on air, and this module closes them. Each guard is one small function with its own switch in `ON`, so a test can switch it
off and prove that its check then fails.

lists    A team's or a user's solves and awards (the public API, the `me` lists and the team, user, team and profile pages) name the
         challenge: its name, category and value, and "Hint for <name>". The six model getters those routes use are wrapped so
         that, in a web request from anybody but an administrator, entries about a challenge the viewer may not see are left out
         (CTFd's own `admin` argument means "ignore the freeze" and is passed by the `me` routes, so it is not read as "staff").
         A hint award is matched through the hint id in its name ("Hint 7"); one that cannot be matched is dropped. Outside a web
         request nothing is filtered.
unlocks  CTFd's unlock route takes `type` as the name of ANY table and checks a challenge's state only for the types that have a
         challenge, so a player could tell a withheld id from a missing one (400 against 404) and count the rows of our own
         tables, the audit trail included. Any type but `hints` and `solutions` now answers CTFd's own 404.
next_id  An administrator can point a challenge's "next challenge" at any other one, and the challenge's JSON then carries that
         id. It is blanked unless the viewer may see that challenge.
scoreboard  The score history of /api/v1/scoreboard/top/<n> carries the id of every challenge a team solved (only awards have
         none). The id of a challenge the viewer may not see is blanked, as CTFd already does for awards; the score steps stay,
         because points are kept when a programme is pulled back.
files    A challenge file is served to any signed-in visitor, and a signed link works for anybody, with no look at the challenge.
         The file route now answers 404, like a file that does not exist, unless the challenge is visible and on air.
shares   The social share lets any signed-in player sign a link for any user and challenge, and the public link prints the
         challenge's name and value. The platform has no use for it: /share/* and POST /api/v1/shares answer 404.

All three do nothing while release control is not in use (no channel exists), so stock CTFd behaves as it does without us.
"""
import json

from flask import abort, g, has_request_context, request

from CTFd.models import Challenges, Files, Hints, Solves, Teams, Users
from CTFd.plugins.l3mon_core.airing import is_on_air, release_active
from CTFd.plugins.l3mon_core.visibility import VISIBLE, visible_challenge_ids
from CTFd.utils.user import authed, get_current_user, is_admin

ON = {"lists": True, "files": True, "shares": True, "scoreboard": True, "unlocks": True, "next_id": True}
UNLOCKABLE = ("hints", "solutions")  # the unlock types CTFd checks the challenge for
_CACHE_KEY = "l3mon_viewer_visible_ids"
_installed = False


def _is_staff() -> bool:
    return has_request_context() and is_admin()


def _viewer_visible_ids() -> set:
    """The challenges the person making this request may see; worked out once per request."""
    if has_request_context() and _CACHE_KEY in g:
        return g.get(_CACHE_KEY)
    solved = set()
    if has_request_context() and authed():
        user = get_current_user()
        if user is not None:
            solved = {cid for (cid,) in Solves.query.with_entities(Solves.challenge_id).filter_by(account_id=user.account_id).all()}
    ids = visible_challenge_ids(solved_ids=solved)
    if has_request_context():
        setattr(g, _CACHE_KEY, ids)
    return ids


def _hint_ids_of(awards) -> dict:
    wanted = {}
    for award in awards:
        if award.category == "hints":
            parts = (award.name or "").split()
            if len(parts) == 2 and parts[0] == "Hint" and parts[1].isdigit():
                wanted[award.id] = int(parts[1])
    if not wanted:
        return {}
    by_hint = dict(Hints.query.with_entities(Hints.id, Hints.challenge_id).filter(Hints.id.in_(set(wanted.values()))).all())
    return {award_id: by_hint.get(hint_id) for award_id, hint_id in wanted.items()}


def _only_what_may_be_seen(kind, rows):
    ids = _viewer_visible_ids()
    if kind == "awards":
        mapped = _hint_ids_of(rows)
        return [row for row in rows if (row.category != "hints") or (mapped.get(row.id) in ids)]
    return [row for row in rows if row.challenge_id in ids]


def _wrap(model, name, kind):
    original = getattr(model, name)
    if getattr(original, "_l3mon_original", None) is not None:
        return

    def guarded(self, admin=False):
        rows = original(self, admin=admin)
        if not has_request_context() or not ON["lists"] or _is_staff() or not release_active():
            return rows
        return _only_what_may_be_seen(kind, rows)

    guarded._l3mon_original = original
    guarded.__name__ = name
    setattr(model, name, guarded)


def _file_door():
    if request.endpoint != "views.files" or not ON["files"] or not release_active() or _is_staff():
        return None
    entry = Files.query.filter_by(location=(request.view_args or {}).get("path")).first()
    challenge_id = getattr(entry, "challenge_id", None)
    if entry is None or entry.type != "challenge" or challenge_id is None:
        return None  # a missing file is CTFd's own 404; page and solution files are not ours
    challenge = Challenges.query.get(challenge_id)
    if challenge is None or challenge.state != VISIBLE or not is_on_air(challenge_id):
        abort(404)
    return None


def _share_door():
    if not ON["shares"] or not release_active():
        return None
    if request.path.startswith("/share/") or request.path.rstrip("/") == "/api/v1/shares":
        abort(404)
    return None


def _unlock_door():
    if request.endpoint != "api.unlocks_unlock_list" or request.method != "POST":
        return None
    if not ON["unlocks"] or not release_active() or _is_staff():
        return None
    body = request.get_json(silent=True)
    if isinstance(body, dict) and body.get("type") not in UNLOCKABLE:
        abort(404)
    return None


def _next_id_door(response):
    if request.endpoint != "api.challenges_challenge" or request.method != "GET" or response.status_code != 200:
        return response
    if not ON["next_id"] or not release_active() or _is_staff():
        return response
    data = response.get_json(silent=True)
    inner = data.get("data") if isinstance(data, dict) else None
    if isinstance(inner, dict) and inner.get("next_id") is not None and inner["next_id"] not in _viewer_visible_ids():
        inner["next_id"] = None
        response.set_data(json.dumps(data))
    return response


def _scoreboard_door(response):
    if request.endpoint != "api.scoreboard_scoreboard_detail" or response.status_code != 200:
        return response
    if not ON["scoreboard"] or not release_active() or _is_staff():
        return response
    data = response.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        return response
    ids = _viewer_visible_ids()
    for team in data["data"].values():
        for step in team.get("solves", []):
            if step.get("challenge_id") is not None and step["challenge_id"] not in ids:
                step["challenge_id"] = None
    response.set_data(json.dumps(data))
    return response


def install(app=None):
    """Wrap the getters (once for the process) and register the request guards on the app."""
    global _installed
    if not _installed:
        for model in (Users, Teams):
            _wrap(model, "get_solves", "solves")
            _wrap(model, "get_fails", "fails")
            _wrap(model, "get_awards", "awards")
        _installed = True
    if app is not None and "l3mon_release_doors" not in app.extensions:
        app.extensions["l3mon_release_doors"] = True
        app.before_request(_file_door)
        app.before_request(_share_door)
        app.before_request(_unlock_door)
        app.after_request(_scoreboard_door)
        app.after_request(_next_id_door)
