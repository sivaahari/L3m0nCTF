"""The programme panel: CTFd's own answer for one challenge, plus one key, `l3mon`, and nothing taken away.

`GET /api/v1/challenges/<id>` keeps every key CTFd sends (the description, the hints a studio has unlocked, the files, the solve
count, the value as it is now) and gains

    l3mon: {slug, number, channel, channel_name, sponsor, difficulty, author, live, decay, start, floor, first_blood_open,
            tries_left, score, phase}

`author` is CTFd's own `attribution`. `decay`, `start` and `floor` describe a value that falls (the dynamic type, or any
challenge CTFd gives a scoring function) and are null for a fixed one. `tries_left` is the studio's, by the rule CTFd uses to
lock it out (lock-out, or timeout), and null when the challenge does not limit tries. `score` is the studio's own score, live
(null for the crew). A challenge that is on no channel gets no block.

Before the start CTFd answers every challenge route with a 403 and a bare sentence; for a studio that is the contract's
`phase_closed` (with `phase: before`), the same for a real id and a missing one.
"""
import json
from datetime import datetime, timedelta

from flask import abort, request

from CTFd.models import Challenges, Fails, Solves, db
from CTFd.plugins.l3mon_board import envelope, viewer
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.models import LIVE_DELIVERIES, Channel, Programme
from CTFd.plugins.l3mon_core.visibility import is_visible
from CTFd.plugins.l3mon_scoring.values import DECAYING
from CTFd.utils import get_config
from CTFd.utils.user import authed, is_admin

ON = {"block": True, "before": True, "locked": True}
DETAIL = "api.challenges_challenge"
BEFORE_START = {
    DETAIL: "Programmes go on air when the broadcast starts.",
    "api.challenges_challenge_list": "Programmes go on air when the broadcast starts.",
    "api.challenges_challenge_attempt": "Submissions open when the broadcast starts.",
}


def tries_left(challenge, team):
    """How many wrong flags the studio may still send, by CTFd's own rule; None when the challenge does not limit tries or nobody has a studio."""
    if team is None or not challenge.max_attempts:
        return None
    fails = Fails.query.filter_by(account_id=team.id, challenge_id=challenge.id)
    if get_config("max_attempts_behavior", "lockout") == "timeout":
        seconds = int(get_config("max_attempts_timeout", 300))
        fails = fails.filter(Fails.date >= datetime.utcnow() - timedelta(seconds=seconds))
    return max(0, int(challenge.max_attempts) - fails.count())


def block(challenge, solves, team, phase) -> dict:
    """The `l3mon` block for a challenge that has a programme, or None."""
    row = (
        Programme.query.filter_by(challenge_id=challenge.id)
        .join(Channel, Programme.channel_id == Channel.id)
        .with_entities(Programme, Channel)
        .first()
    )
    if row is None:
        return None
    programme, channel = row
    falls = challenge.function in DECAYING
    return {
        "slug": programme.slug, "number": programme.number, "channel": channel.id, "channel_name": channel.name,
        "sponsor": {"name": channel.sponsor_name, "logo": channel.sponsor_logo} if channel.kind == "sponsored" else None,
        "difficulty": programme.difficulty, "author": challenge.attribution, "live": programme.delivery in LIVE_DELIVERIES,
        "decay": falls, "start": int(challenge.initial) if falls and challenge.initial is not None else None,
        "floor": int(challenge.minimum) if falls and challenge.minimum is not None else None,
        "first_blood_open": solves == 0, "tries_left": tries_left(challenge, team),
        "score": int(team.get_score(admin=True) or 0) if team is not None else None,
        "phase": {"state": phase.state, "frozen": phase.frozen},
    }


def _closed_before_the_start(response):
    """CTFd's 403 before the start, for a studio, as the contract's phase_closed."""
    message = BEFORE_START.get(request.endpoint)
    if message is None or response.status_code != 403 or current_phase().state != "before":
        return None
    if is_admin() or viewer.refusal() is not None:
        return None  # the crew passes CTFd's check anyway; a visitor, a suspended or unverified account and one with no studio keep CTFd's own answer
    return envelope.fail("phase_closed", 403, message, phase="before")


def locked_door():
    """Before CTFd's view: a programme whose prerequisite the studio has not met is a missing one.

    Stock CTFd answers such a request with a 403, or (with `anonymize`) with a 200 whose name is "???", or (with `anonymize`
    set to "preview") with the real name, category and value; the board already treats the programme as absent, so the panel
    must too. Aborting here gives the exact answer a missing id gets. Not before the start (CTFd answers every id alike then,
    and a different answer for a locked one would tell the two apart); the crew and visitors are left alone."""
    if not ON["locked"] or request.method != "GET" or request.endpoint != DETAIL:
        return None
    if not authed() or is_admin() or current_phase().state == "before":
        return None
    try:
        challenge = Challenges.query.get(int(request.view_args.get("challenge_id")))
    except (TypeError, ValueError):
        return None
    if challenge is None or not (challenge.requirements or {}).get("prerequisites"):
        return None  # a missing id is CTFd's to answer, and a challenge without prerequisites needs nothing from us
    if not is_visible(challenge, solved_ids=viewer.current().solved):
        abort(404)
    return None


def decorate(response):
    if request.method not in ("GET", "POST") or request.endpoint not in BEFORE_START:
        return response
    if ON["before"]:
        closed = _closed_before_the_start(response)
        if closed is not None:
            return closed
    if not ON["block"] or request.endpoint != DETAIL or request.method != "GET" or response.status_code != 200:
        return response
    body = response.get_json(silent=True)
    inner = body.get("data") if isinstance(body, dict) else None
    if not isinstance(inner, dict) or "id" not in inner:
        return response
    challenge = Challenges.query.get(inner["id"])
    if challenge is None:
        return response
    team = viewer.current_team()
    extra = block(challenge, inner.get("solves"), team, current_phase())
    if extra is not None:
        inner["l3mon"] = extra
        if team is not None:
            # CTFd reads "solved by me" from the public solve counts and says false when no studio that counts has solved the challenge:
            # wrong for a hidden studio, and for a studio whose only solve came after the freeze. The studio's own record is the truth.
            inner["solved_by_me"] = db.session.query(Solves.id).filter_by(account_id=team.id, challenge_id=challenge.id).first() is not None
        response.set_data(json.dumps(body))
    return response


def install(app):
    if "l3mon_board_panel" not in app.extensions:
        app.extensions["l3mon_board_panel"] = True
        app.before_request(locked_door)
        app.after_request(decorate)
