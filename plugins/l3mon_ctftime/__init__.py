"""l3mon_ctftime: the public CTFtime feeds.

CTFtime reads the standings of a live event from one public JSON address and, afterwards, the final standings. This
plugin serves both in the smallest format CTFtime documents (it warns against sending more until it announces more):

    {"standings": [{"pos": 1, "team": "Team name exactly as registered", "score": 4200}]}

    GET /ctftime/standings.json        the live standings; while the scoreboard is frozen, the frozen ones
    GET /ctftime/final-standings.json  the final standings, only once the event has ended AND an organiser has published them

Public means no sign-in, no personal data (only the public team name and its score) and a short shared cache. The rows come
from CTFd's own standings, so the order, the tie-break (who reached the score first) and the freeze are exactly what the
platform's scoreboard shows. Banned and hidden accounts never appear; neither does anyone with a score of zero or less.

Publishing the final standings is a deliberate step, taken after cheating cases are settled and bans are applied:

    PATCH /api/v1/configs   {"l3mon_final_standings_published": true}      (administrator token)
"""
import json

from flask import Blueprint, Response

from CTFd.utils import get_config
from CTFd.utils.dates import ctf_ended
from CTFd.utils.scores import get_standings

PUBLISH_KEY = "l3mon_final_standings_published"

feeds = Blueprint("l3mon_ctftime", __name__)


def _number(score):
    """A whole number when the score is one (it nearly always is), otherwise rounded to two places."""
    value = float(score)
    return int(value) if value == int(value) else round(value, 2)


def rows(standings):
    """The feed's rows from CTFd's standings: only a positive score, ranked in the order CTFd gave (pos counts rows shown)."""
    out = []
    for row in standings:
        if row.score is None or float(row.score) <= 0:
            continue
        out.append({"pos": len(out) + 1, "team": row.name, "score": _number(row.score)})
    return out


def _published() -> bool:
    return str(get_config(PUBLISH_KEY) or "").strip().lower() in ("1", "true", "yes", "on")


def _respond(feed_rows):
    # json.dumps writes every character above 0x7E as a \uXXXX escape, so the bytes are plain ASCII and any name round-trips
    body = json.dumps({"standings": feed_rows}, separators=(",", ":"))
    return Response(body, status=200, mimetype="application/json", headers={"Cache-Control": "public, max-age=15"})


@feeds.route("/ctftime/standings.json")
def live():
    return _respond(rows(get_standings(admin=False)))


@feeds.route("/ctftime/final-standings.json")
def final():
    if not (ctf_ended() and _published()):
        body = json.dumps({"success": False, "error": "not_found", "message": "The final standings are not published yet."})
        return Response(body, status=404, mimetype="application/json", headers={"Cache-Control": "no-store"})
    # admin=True ignores the freeze, so late solves count; the rows carry hidden and banned flags, which are dropped here
    visible = [row for row in get_standings(admin=True) if not row.hidden and not row.banned]
    return _respond(rows(visible))


def load(app):
    from CTFd.plugins.l3mon_ctftime import oauth

    app.register_blueprint(feeds)
    app.register_blueprint(oauth.oauth)
    oauth.warn_if_half_configured()
