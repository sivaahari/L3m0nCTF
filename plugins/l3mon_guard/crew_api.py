"""Crew review API and admin page for l3mon_guard (SP5 B4.7).

Provides:
    GET    /api/v1/l3mon/admin/guard/evidence          Paged evidence list with filters
    POST   /api/v1/l3mon/admin/guard/evidence/<id>     Update evidence status and note
    DELETE /api/v1/l3mon/admin/guard/evidence          Prune old evidence for data retention
    GET    /admin/l3mon/guard                          Crew review web page
    GET    /plugins/l3mon_guard/assets/<path:name>     Crew page static assets
"""
import datetime
import os
import re

from flask import Blueprint, jsonify, render_template, request, send_from_directory

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_core.text import crew_text
from CTFd.plugins.l3mon_guard.models import EVIDENCE_KINDS, EVIDENCE_STATUSES, Evidence
from CTFd.utils.decorators import admins_only
from CTFd.utils.user import get_current_user

bp = Blueprint("l3mon_guard_admin", __name__, url_prefix="/api/v1/l3mon/admin/guard")
page_bp = Blueprint("l3mon_guard_page", __name__, template_folder="templates")
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _answer(data, status=200):
    response = jsonify({"success": True, "data": data})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _refuse(error_code: str, message: str, status=400):
    response = jsonify({"success": False, "error": error_code, "message": message})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/evidence", methods=["GET"])
@admins_only
def get_evidence():
    """List evidence with pagination and filtering."""
    query = Evidence.query

    status = request.args.get("status", "").strip().lower()
    if status and status in EVIDENCE_STATUSES:
        query = query.filter(Evidence.status == status)

    kind = request.args.get("kind", "").strip().lower()
    if kind and kind in EVIDENCE_KINDS:
        query = query.filter(Evidence.kind == kind)

    team_id_raw = request.args.get("team_id", "").strip()
    if team_id_raw and re.fullmatch(r"[0-9]{1,9}", team_id_raw):
        query = query.filter(Evidence.team_id == int(team_id_raw))

    before_id_raw = request.args.get("before_id", "").strip()
    if before_id_raw and re.fullmatch(r"[0-9]{1,9}", before_id_raw):
        query = query.filter(Evidence.id < int(before_id_raw))

    limit_raw = request.args.get("limit", "50").strip()
    limit = 50
    if limit_raw and re.fullmatch(r"[0-9]{1,9}", limit_raw):
        limit = min(max(1, int(limit_raw)), 200)

    rows = query.order_by(Evidence.id.desc()).limit(limit).all()

    # Pre-fetch team and user names for readability
    data = []
    for r in rows:
        team_name = None
        other_team_name = None
        user_name = None
        reviewer_name = None

        if r.team_id:
            team = Teams.query.filter_by(id=r.team_id).first()
            team_name = team.name if team else f"Team #{r.team_id}"
        if r.other_team_id:
            other_team = Teams.query.filter_by(id=r.other_team_id).first()
            other_team_name = other_team.name if other_team else f"Team #{r.other_team_id}"
        if r.user_id:
            user = Users.query.filter_by(id=r.user_id).first()
            user_name = user.name if user else f"User #{r.user_id}"
        if r.reviewer_id:
            reviewer = Users.query.filter_by(id=r.reviewer_id).first()
            reviewer_name = reviewer.name if reviewer else f"Reviewer #{r.reviewer_id}"

        data.append({
            "id": r.id,
            "kind": r.kind,
            "team_id": r.team_id,
            "team_name": team_name,
            "other_team_id": r.other_team_id,
            "other_team_name": other_team_name,
            "user_id": r.user_id,
            "user_name": user_name,
            "challenge_id": r.challenge_id,
            "detail": r.detail,
            "address": r.address,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "status": r.status,
            "reviewer_id": r.reviewer_id,
            "reviewer_name": reviewer_name,
            "note": r.note,
        })

    return _answer(data)


@bp.route("/evidence/<evidence_id>", methods=["POST"])
@admins_only
def update_evidence(evidence_id):
    """Update status and note of an evidence entry."""
    if not re.fullmatch(r"[0-9]{1,9}", str(evidence_id)):
        return _refuse("invalid_id", "Evidence ID must be numeric.", 404)

    item = Evidence.query.filter_by(id=int(evidence_id)).first()
    if not item:
        return _refuse("not_found", "Evidence entry not found.", 404)

    payload = request.get_json(silent=True) or {}
    new_status = str(payload.get("status", "")).strip().lower()
    if new_status not in EVIDENCE_STATUSES:
        return _refuse("invalid_status", f"Status must be one of: {', '.join(EVIDENCE_STATUSES)}.")

    raw_note = payload.get("note", "")
    clean_note, note_problem = crew_text(raw_note, limit=200, required=False)
    if note_problem:
        return _refuse("invalid_note", note_problem)

    current_user = get_current_user()
    reviewer_id = getattr(current_user, "id", None)

    old_status = item.status
    item.status = new_status
    item.note = clean_note
    item.reviewer_id = reviewer_id

    audit.record(
        "guard.evidence.update",
        f"evidence:{item.id}",
        f"status: {old_status} -> {new_status}; note: {clean_note}",
    )
    db.session.commit()

    return _answer({
        "id": item.id,
        "status": item.status,
        "note": item.note,
        "reviewer_id": item.reviewer_id,
    })


@bp.route("/evidence", methods=["DELETE"])
@admins_only
def delete_evidence():
    """Prune evidence older than specified days."""
    days_raw = request.args.get("older_than_days", "30").strip()
    if not re.fullmatch(r"[0-9]{1,4}", days_raw) or int(days_raw) < 1:
        return _refuse("invalid_days", "older_than_days must be a positive integer.")

    days = int(days_raw)
    cutoff = datetime.datetime.utcfromtimestamp(now() - days * 86400)

    count = Evidence.query.filter(Evidence.created_at < cutoff).delete(synchronize_session=False)
    audit.record(
        "guard.evidence.prune",
        "evidence",
        f"pruned {count} row(s) older than {days} days",
    )
    db.session.commit()

    return _answer({"pruned": count, "older_than_days": days})


@page_bp.route("/admin/l3mon/guard")
@admins_only
def guard_page():
    """The crew's review page."""
    return render_template("l3mon_guard/admin.html")


@page_bp.route("/plugins/l3mon_guard/assets/<path:name>")
@admins_only
def guard_assets(name):
    """Serve static assets for crew review page."""
    return send_from_directory(ASSETS, name)
