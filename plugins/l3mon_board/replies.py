"""The reply to a flag, and the phase rules in front of a flag and a hint.

CTFd's own endpoint keeps judging the flag, recording the solve and valuing the challenge; its answer gains `data.l3mon`:

    correct    {value, reel, reels_needed, channel_signal, channel_complete, first_blood, solves}
    incorrect  {tries_left}                      only when the challenge limits tries
    refused    {reason, [retry_after], [tries_left]}   reason: processing | no_tries | ratelimited | paused | ended
    unlocked   {score, cost}                     on a bought hint: the studio's TRP after the purchase and what the hint cost

`value` is what the programme is worth after this solve (CTFd has already valued it). `reel` is the studio's solves of programmes
it can see, `reels_needed` a third of all the cells (rounded up), `channel_signal` the share of the channel's cells that are clear.
`first_blood` is exact: no studio that counts has an earlier solve of it, so two studios racing for it cannot both be told they
were first. `solves` is the programme's count as this studio may know it (CTFd's, which honours the freeze, plus the
studio's own solve if the freeze left it out).

The reasons are read from CTFd's own wording, which is pinned by test_board_ctfd_facts.py.

In front of CTFd, for a studio only (the crew passes):
- after the end a flag is answered `403 paused` with `reason: ended`, the same for a right and a wrong flag. CTFd would say "correct"
  to a right one and record nothing;
- a hint cannot be bought before the start, while paused or after the end: the contract's `phase_closed`, and no TRP is spent.
"""
import json
import math
import re

from flask import Response, request

from CTFd.models import Challenges, Hints, Solves, Teams, db
from CTFd.plugins.l3mon_board import envelope, panel, viewer
from CTFd.plugins.l3mon_core.clock import current_phase, window
from CTFd.plugins.l3mon_core.models import Programme
from CTFd.plugins.l3mon_core.visibility import visible_challenge_ids
from CTFd.utils.dates import unix_time_to_utc
from CTFd.utils.challenges import get_solve_counts_for_challenges
from CTFd.utils.user import is_admin

ON = {"extras": True, "ended": True, "hints": True}
ATTEMPT = "api.challenges_challenge_attempt"
UNLOCK = "api.unlocks_unlock_list"
_SECONDS = re.compile(r"(\d+) seconds?")
HINT_REFUSALS = {
    "before": "Hints open when the broadcast starts.",
    "paused": "Hints are paused for a moment. No TRP was spent.",
    "ended": "The broadcast has ended. Hints can no longer be unlocked.",
}


def _asked_challenge_id():
    body = request.get_json(silent=True) if request.is_json else request.form
    try:
        return int((body or {}).get("challenge_id"))
    except (TypeError, ValueError):
        return None


def _studio_only():
    """True for a signed-in player with a studio. The crew, visitors and accounts CTFd itself refuses are not ours to answer."""
    return not is_admin() and viewer.refusal() is None


def _correct(challenge_id, team):
    """The numbers of a correct reply, or None when the solve cannot be found (the reply is then CTFd's own)."""
    db.session.commit()  # end the transaction CTFd's solve ran in, so everything below is read fresh
    own = Solves.query.filter_by(account_id=team.id, challenge_id=challenge_id).first()
    challenge = Challenges.query.get(challenge_id)
    if own is None or challenge is None:
        return None
    solved = {cid for (cid,) in Solves.query.with_entities(Solves.challenge_id).filter_by(account_id=team.id).all()}
    shown = visible_challenge_ids(solved_ids=solved)
    cells = {}  # channel -> the challenge ids of all its programmes, on air or not
    for cid, channel_id in Programme.query.with_entities(Programme.challenge_id, Programme.channel_id).all():
        cells.setdefault(channel_id, []).append(cid)
    on_plan = {cid for ids in cells.values() for cid in ids}
    total_cells = len(on_plan)
    mine = Programme.query.filter_by(challenge_id=challenge_id).first()
    in_channel = cells.get(mine.channel_id, []) if mine is not None else []
    done = sum(1 for cid in in_channel if cid in solved and cid in shown)
    reel = sum(1 for cid in solved if cid in shown and cid in on_plan)
    # first blood: no studio that counts solved it before this one (the solve ids are in the order CTFd recorded them), so two studios
    # racing for it cannot both be first; a hidden studio's own solve is "first" when no counted studio was ahead of it, as in the demo
    freeze = window().freeze
    earlier = Solves.query.join(Teams, Solves.account_id == Teams.id).filter(
        Solves.challenge_id == challenge_id, Solves.id < own.id, Teams.hidden == False, Teams.banned == False  # noqa: E712 (SQL, not Python)
    )
    if freeze:
        earlier = earlier.filter(Solves.date < unix_time_to_utc(freeze))  # while frozen a studio may not learn that another solved it after the freeze
    earlier = earlier.first()
    counted = int((get_solve_counts_for_challenges(admin=False) or {}).get(challenge_id, 0))
    if freeze and own.date >= unix_time_to_utc(freeze) and not team.hidden and not team.banned:
        counted += 1  # the freeze leaves the studio's own later solve out of the public count; the studio is told its own (a studio that never counts is told none)
    return {
        "value": int(db.session.query(Challenges.value).filter_by(id=challenge_id).scalar() or 0),
        "reel": reel, "reels_needed": math.ceil(total_cells / 3),
        "channel_signal": round(done / len(in_channel), 2) if in_channel else 0, "channel_complete": bool(in_channel) and done == len(in_channel),
        "first_blood": earlier is None, "solves": counted,
    }


def _refusal(status, message):
    """The reason for a 403 or 429 reply of CTFd's, from its own wording (pinned by test_board_ctfd_facts.py)."""
    seconds = _SECONDS.search(message)
    if status == "paused":
        return {"reason": "paused"}
    if message.startswith("Another submission is already being processed"):
        return {"reason": "processing", "retry_after": 1}
    if message.startswith("Not accepted. You have 0 tries remaining"):
        return {"reason": "no_tries", "tries_left": 0}
    if status == "ratelimited" and seconds:
        return {"reason": "ratelimited", "retry_after": max(1, int(seconds.group(1)))}
    if status == "ratelimited":
        return {"reason": "ratelimited", "retry_after": 10}
    return None


def _unlocked(response):
    """A bought hint: CTFd's own reply plus the studio's score after the purchase and the hint's cost. Nothing is added to a refusal."""
    body = response.get_json(silent=True)
    data = body.get("data") if isinstance(body, dict) else None
    team = viewer.current_team()
    if response.status_code != 200 or not isinstance(data, dict) or "l3mon" in data or data.get("type") != "hints" or team is None:
        return response
    db.session.commit()  # end the transaction CTFd's purchase ran in, so the score below is read fresh
    hint = Hints.query.get(data.get("target"))
    if hint is None:
        return response
    data["l3mon"] = {"score": int(team.get_score(admin=True) or 0), "cost": int(hint.cost or 0)}
    response.set_data(json.dumps(body))
    return response


def decorate(response):
    if ON["extras"] and request.endpoint == UNLOCK and request.method == "POST":
        return _unlocked(response)
    if not ON["extras"] or request.endpoint != ATTEMPT or request.method != "POST":
        return response
    body = response.get_json(silent=True)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or "l3mon" in data:
        return response
    status, extra = data.get("status"), None
    team = viewer.current_team()
    if status == "correct" and response.status_code == 200 and team is not None:
        cid = _asked_challenge_id()
        extra = _correct(cid, team) if cid is not None else None
    elif status == "incorrect" and team is not None:
        challenge = Challenges.query.get(_asked_challenge_id() or 0)
        left = panel.tries_left(challenge, team) if challenge is not None else None
        extra = {"tries_left": left} if left is not None else None
    elif status in ("ratelimited", "paused"):
        extra = _refusal(status, str(data.get("message") or ""))
    if extra is None:
        return response
    data["l3mon"] = extra
    response.set_data(json.dumps(body))
    if "retry_after" in extra:
        response.headers["Retry-After"] = str(extra["retry_after"])
    return response


def _after_the_end():
    if not ON["ended"] or request.method != "POST" or request.endpoint != ATTEMPT:
        return None
    if current_phase().state != "ended" or not _studio_only():
        return None
    body = {"success": True, "data": {"status": "paused", "message": "The broadcast has ended.", "l3mon": {"reason": "ended"}}}
    response = Response(json.dumps(body), status=403, mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    return response


def _hints_by_phase():
    if not ON["hints"] or request.method != "POST" or request.endpoint != UNLOCK:
        return None
    state = current_phase().state
    if state == "live" or not _studio_only():
        return None
    return envelope.fail("phase_closed", 403, HINT_REFUSALS[state], phase=state)


def guard():
    """Before CTFd's view: the answer for a flag after the end, or for a hint outside the live hours; None to carry on."""
    return _after_the_end() or _hints_by_phase()


def install(app):
    if "l3mon_board_replies" not in app.extensions:
        app.extensions["l3mon_board_replies"] = True
        app.before_request(guard)
        app.after_request(decorate)
