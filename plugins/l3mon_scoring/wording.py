"""Every score word a player reads is TRP. The only place CTFd's API says "points" to a player is the answer to an unaffordable hint
(CTFd/api/v1/unlocks.py), so that one sentence is rewritten on its way out. Nothing else about the answer changes."""
import json

from flask import request

STOCK = "You do not have enough points to unlock this hint"
TRP = "You do not have enough TRP to unlock this hint"
ON = {"unlocks": True}


def _rewrite(response):
    if not ON["unlocks"] or request.method != "POST" or request.path.rstrip("/") != "/api/v1/unlocks" or response.status_code != 400:
        return response
    body = response.get_json(silent=True)
    if isinstance(body, dict) and isinstance(body.get("errors"), dict) and body["errors"].get("score") == STOCK:
        body["errors"]["score"] = TRP
        response.set_data(json.dumps(body))
    return response


def install(app):
    if "l3mon_scoring_wording" not in app.extensions:
        app.extensions["l3mon_scoring_wording"] = True
        app.after_request(_rewrite)
