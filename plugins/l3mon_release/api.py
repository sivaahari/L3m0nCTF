"""The crew's API (administrators only).

    GET /api/v1/l3mon/admin/release       the plan, what players see right now, and the newest audit lines
    PUT /api/v1/l3mon/admin/release       release, withhold or schedule channels and programmes (a batch is one transaction)
    PUT /api/v1/l3mon/admin/programmes    load the channels and programmes (an idempotent upsert; new entries start withheld)

Every call needs an administrator (CTFd's own `admins_only`); a signed-in session also needs CTFd's CSRF token, which CTFd checks
for every unsafe request, and a script sends an API token instead. nginx refuses the whole prefix to anyone outside the
organisers' addresses (deploy/nginx/snippets/api-admin-routes-l3mon.conf). Answers are never cached.
"""
import os

from flask import Blueprint, jsonify, render_template, request, send_from_directory
from sqlalchemy.exc import IntegrityError

from CTFd.models import db
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_release import plan
from CTFd.plugins.l3mon_release.reconcile import reconcile, serialize
from CTFd.utils.decorators import admins_only

bp = Blueprint("l3mon_release_admin", __name__, url_prefix="/api/v1/l3mon/admin")
page_bp = Blueprint("l3mon_release_page", __name__, template_folder="templates")
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


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


@bp.route("/release", methods=["GET"])
@admins_only
def get_release():
    return _answer(plan.release_view(now()))


@bp.route("/release", methods=["PUT"])
@admins_only
def put_release():
    serialize()
    t = now()
    clean, problems = plan.validate_changes(request.get_json(silent=True), t)
    if problems:
        return _refuse(problems)
    changed = plan.apply_changes(clean)
    outcome = reconcile(t, locked=True)  # commits the changes and what they do to CTFd's own states, together
    data = plan.release_view(now())
    data["result"] = {"changed": changed, "shown": len(outcome.shown), "hidden": len(outcome.hidden)}
    return _answer(data)


@bp.route("/programmes", methods=["PUT"])
@admins_only
def put_programmes():
    serialize()
    clean, problems = plan.validate_plan(request.get_json(silent=True))
    if problems:
        return _refuse(problems)
    try:
        counts = plan.apply_plan(clean)
        created = sum(v["created"] for v in counts.values())
        updated = sum(v["updated"] for v in counts.values())
        if created or updated:
            audit.record(
                "plan.sync", "plan",
                f"channels +{counts['channels']['created']} ~{counts['channels']['updated']}; programmes +{counts['programmes']['created']} ~{counts['programmes']['updated']}",
            )
        reconcile(locked=True)
    except IntegrityError as error:
        db.session.rollback()
        return _refuse(plan.refused(error))
    return _answer(counts)


@page_bp.route("/admin/l3mon/release")
@admins_only
def release_page():
    """The crew's page. A shell: its script reads and writes through the API above."""
    return render_template("l3mon_release/release.html")


@page_bp.route("/plugins/l3mon_release/assets/<path:name>")
@admins_only
def release_assets(name):
    """The page's script and style, for administrators only. (CTFd's own plugin-assets helper takes an admins_only argument and then
    ignores it, so the two files are served here.) send_from_directory refuses any path that leaves the folder."""
    return send_from_directory(ASSETS, name)
