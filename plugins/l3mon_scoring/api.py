"""The crew's scoring API (administrators only).

    GET  /api/v1/l3mon/admin/scoring              every challenge's value against its formula, the voids, the bonuses, the audit lines
    POST /api/v1/l3mon/admin/scoring/revoke       {"challenge_id", "reason"}: set the challenge's solves aside
    POST /api/v1/l3mon/admin/scoring/restore      {"challenge_id", "reason"?}: put them back
    POST /api/v1/l3mon/admin/scoring/bonus        {"team_id", "user_id"?, "trp", "message"}
    POST /api/v1/l3mon/admin/scoring/recalculate  {}: put every dynamic value right and say what changed

Every call needs an administrator (CTFd's own `admins_only`); a signed-in session also needs CTFd's CSRF token, and a script sends an
API token instead. nginx refuses the whole `/api/v1/l3mon/admin` prefix to anyone outside the organisers' addresses (the release
control plugin's snippet already covers it). An unknown key in a request is an error (a typo never passes silently). Answers are never
cached. The crew sees reasons and messages here; no player route does.
"""
import calendar
import os

from flask import Blueprint, jsonify, render_template, request, send_from_directory
from sqlalchemy import func
from sqlalchemy.orm import aliased

from CTFd.models import Awards, Challenges, Solves, Teams, Users, db
from CTFd.plugins.dynamic_challenges import DynamicChallenge
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_core.counting import counted_solve_counts
from CTFd.plugins.l3mon_core.models import Bonus, Void
from CTFd.plugins.l3mon_scoring import bonus, values, voids
from CTFd.plugins.l3mon_scoring.errors import Refused
from CTFd.utils.decorators import admins_only

bp = Blueprint("l3mon_scoring_admin", __name__, url_prefix="/api/v1/l3mon/admin")
page_bp = Blueprint("l3mon_scoring_page", __name__, template_folder="templates")
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

VOIDS_SHOWN = 50
BONUSES_SHOWN = 30
AUDIT_SHOWN = 20


def _answer(data, status=200):
    response = jsonify({"success": True, "data": data})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _refuse(problems, status=400):
    response = jsonify({"success": False, "errors": dict(problems)})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _epoch(moment):
    return None if moment is None else calendar.timegm(moment.timetuple())


def _body(allowed):
    """-> (the JSON object, problems). Anything that is not an object, and any key that is not allowed, is a problem."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, {"body": ["must be a JSON object"]}
    return data, {key: ["is not a known field"] for key in data if key not in allowed}


def _do(call):
    """Run a service call; a Refused becomes CTFd's error shape and leaves nothing half done."""
    try:
        return _answer(call())
    except Refused as refused:
        db.session.rollback()
        return _refuse(refused.problems, refused.status)


# ---- the view ----------------------------------------------------------------------------------------------------------------------

def _challenges() -> list:
    rows = db.session.query(Challenges.id, Challenges.name, Challenges.value).order_by(Challenges.id).all()
    dynamic = {c.id: c for c in DynamicChallenge.query.all()}
    solves = counted_solve_counts([r.id for r in rows])
    held = dict(db.session.query(Solves.challenge_id, func.count(Solves.id)).group_by(Solves.challenge_id).all())  # every solve, counted or not
    open_voids = dict(db.session.query(Void.challenge_id, func.count(Void.id)).filter(Void.outcome == "open").group_by(Void.challenge_id).all())
    shown = []
    for row in rows:
        d = dynamic.get(row.id)
        usable = d is not None and d.initial is not None and d.minimum is not None
        shown.append(
            {
                "id": row.id,
                "name": row.name,
                "type": "dynamic" if usable else "fixed",
                "value": row.value,
                "initial": d.initial if usable else None,
                "floor": d.minimum if usable else None,
                "decay": d.decay if usable else None,
                "function": d.function if usable else None,
                "solves": solves[row.id],
                "held": int(held.get(row.id, 0)),
                "wanted": values.formula(d, solves[row.id]) if usable else row.value,
                "open_voids": int(open_voids.get(row.id, 0)),
            }
        )
    return shown


def _voids() -> list:
    voider, restorer, member = aliased(Users), aliased(Users), aliased(Users)
    rows = (
        db.session.query(Void, Challenges.name, Teams.name, member.name, voider.name, restorer.name)
        .join(Challenges, Challenges.id == Void.challenge_id)
        .join(Teams, Teams.id == Void.team_id)
        .outerjoin(member, member.id == Void.user_id)
        .outerjoin(voider, voider.id == Void.voided_by)
        .outerjoin(restorer, restorer.id == Void.restored_by)
        .order_by(Void.id.desc())
        .limit(VOIDS_SHOWN)
        .all()
    )
    return [
        {
            "id": v.id, "challenge_id": v.challenge_id, "challenge": challenge, "team_id": v.team_id, "team": team, "user": user,
            "solved_at": _epoch(v.solved_at), "voided_at": _epoch(v.voided_at), "voided_by": by, "reason": v.reason, "outcome": v.outcome,
            "restored_at": _epoch(v.restored_at), "restored_by": restored_by,
        }
        for v, challenge, team, user, by, restored_by in rows
    ]


def _bonuses() -> list:
    giver, member = aliased(Users), aliased(Users)
    rows = (
        db.session.query(Bonus, Awards.value, Awards.name, Teams.name, member.name, giver.name)
        .join(Awards, Awards.id == Bonus.award_id)
        .join(Teams, Teams.id == Bonus.team_id)
        .join(member, member.id == Bonus.user_id)
        .outerjoin(giver, giver.id == Bonus.given_by)
        .order_by(Bonus.id.desc())
        .limit(BONUSES_SHOWN)
        .all()
    )
    return [
        {
            "id": b.id, "team_id": b.team_id, "team": team, "user": user, "scope": b.scope, "trp": trp, "title": title, "message": b.message,
            "given_by": by, "given_at": _epoch(b.given_at),
        }
        for b, trp, title, team, user, by in rows
    ]


def _teams() -> list:
    """Every studio with its members, for the bonus form. Names only: no address, no password, nothing a player typed."""
    members = {}
    for user_id, name, team_id in db.session.query(Users.id, Users.name, Users.team_id).filter(Users.team_id.isnot(None)).order_by(Users.id).all():
        members.setdefault(team_id, []).append({"id": user_id, "name": name})
    rows = db.session.query(Teams.id, Teams.name, Teams.captain_id, Teams.banned, Teams.hidden).order_by(func.lower(Teams.name), Teams.id).all()
    return [
        {"id": t.id, "name": t.name, "captain_id": t.captain_id, "banned": bool(t.banned), "hidden": bool(t.hidden), "members": members.get(t.id, [])}
        for t in rows
    ]


@bp.route("/scoring", methods=["GET"])
@admins_only
def get_scoring():
    return _answer(
        {
            "now": int(now()),
            "challenges": _challenges(),
            "teams": _teams(),
            "voids": _voids(),
            "bonuses": _bonuses(),
            "audit": audit.recent(AUDIT_SHOWN, prefix="scoring."),
        }
    )


# ---- the actions -------------------------------------------------------------------------------------------------------------------

@bp.route("/scoring/revoke", methods=["POST"])
@admins_only
def post_revoke():
    data, problems = _body({"challenge_id", "reason"})
    if problems:
        return _refuse(problems)
    return _do(lambda: voids.revoke(data.get("challenge_id"), data.get("reason")))


@bp.route("/scoring/restore", methods=["POST"])
@admins_only
def post_restore():
    data, problems = _body({"challenge_id", "reason"})
    if problems:
        return _refuse(problems)
    return _do(lambda: voids.restore(data.get("challenge_id"), data.get("reason")))


@bp.route("/scoring/bonus", methods=["POST"])
@admins_only
def post_bonus():
    data, problems = _body({"team_id", "user_id", "trp", "message"})
    if problems:
        return _refuse(problems)
    return _do(lambda: bonus.give(data.get("team_id"), data.get("user_id"), data.get("trp"), data.get("message")))


@bp.route("/scoring/recalculate", methods=["POST"])
@admins_only
def post_recalculate():
    data, problems = _body(set())
    if problems:
        return _refuse(problems)

    def run():
        changed = values.recalculate()
        names = {c.id: c.name for c in DynamicChallenge.query.filter(DynamicChallenge.id.in_([c[0] for c in changed])).all()} if changed else {}
        if changed:
            detail = "; ".join(f"{names.get(cid, cid)}: {old} -> {new}" for cid, old, new in changed)
            audit.record("scoring.recalculate", f"{len(changed)} challenge(s)", detail)
            db.session.commit()
        return {"changed": [{"id": cid, "name": names.get(cid), "old": old, "new": new} for cid, old, new in changed]}

    return _do(run)


# ---- the page ----------------------------------------------------------------------------------------------------------------------

@page_bp.route("/admin/l3mon/scoring")
@admins_only
def scoring_page():
    """The crew's page. A shell: its script reads and writes through the API above."""
    return render_template("l3mon_scoring/scoring.html")


@page_bp.route("/plugins/l3mon_scoring/assets/<path:name>")
@admins_only
def scoring_assets(name):
    """The page's script and style, for administrators only (see the release page: CTFd's own helper ignores its admins_only
    argument). send_from_directory refuses any path that leaves the folder."""
    return send_from_directory(ASSETS, name)
