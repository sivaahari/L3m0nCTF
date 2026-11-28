"""The crew's certificates API and page (administrators only).

    GET  /api/v1/l3mon/admin/certificates           how many codes are loaded, the release moment, whether they show, whether the notification went out
    PUT  /api/v1/l3mon/admin/certificates/release   {"at": "now"} release now; {"at": <epoch seconds>} release at that moment (in the future,
                                                    at most 60 days on); {"at": null} hide them again

Any crew account may release (the owner's decision of 26 Nov 2026); every change is a line in the audit trail (`certificates.release`) with
who did it. Releasing needs codes loaded (loader.py). Hiding takes the card off every studio page at once; the codes stay valid on the portal,
and the notification that already went out is not taken back (and is not sent a second time).

Every call needs an administrator (CTFd's own `admins_only`); a signed-in session also needs CTFd's CSRF token. nginx refuses the whole
`/api/v1/l3mon/admin` prefix to anyone outside the crew's addresses. An unknown key in a request is an error. Answers are never cached.
"""
import os

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory

from CTFd.models import db
from CTFd.plugins.l3mon_certificates import release
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.clock import ist_text, now
from CTFd.utils import set_config
from CTFd.utils.decorators import admins_only, authed_only
from CTFd.utils.user import get_current_user

bp = Blueprint("l3mon_certificates_admin", __name__, url_prefix="/api/v1/l3mon/admin")
page_bp = Blueprint("l3mon_certificates_page", __name__, template_folder="templates")
player_bp = Blueprint("l3mon_certificates_player", __name__)
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MAX_AHEAD = 60 * 24 * 3600


def _answer(data, status=200):
    response = jsonify({"success": status < 400, **({"data": data} if status < 400 else {"errors": data})})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _state():
    t = int(now())
    at = release.release_at()
    return {
        "codes": release.count(),
        "release_at": at,
        "release_at_text": ist_text(at) if at else None,
        "released": release.released(t),
        "announced_at": release.announced_at(),
        "base_url": release.base_url(),
        "now": t,
    }


@bp.route("/certificates", methods=["GET"])
@admins_only
def get_certificates():
    return _answer(_state())


@bp.route("/certificates/release", methods=["PUT"])
@admins_only
def put_release():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _answer({"body": ["must be a JSON object"]}, 400)
    extra = {key: ["is not a known field"] for key in data if key != "at"}
    if extra or "at" not in data:
        return _answer(extra or {"at": ['is required: "now", a moment in epoch seconds, or null to hide']}, 400)
    at, t = data["at"], int(now())  # whole seconds: CTFd reads a setting back as a number only when it is all digits
    was = release.release_at()
    if at is None:
        # the audit line first: set_config commits the session, so the change and its line are saved together or not at all
        audit.record("certificates.release", "qualifier certificates", "hidden again" + (f" (they were set for {ist_text(was)})" if was else ""))
        set_config(release.KEY_AT, None)
        db.session.commit()
        return _answer(_state())
    if at == "now":
        at = t
    elif not isinstance(at, int) or isinstance(at, bool):
        return _answer({"at": ['must be "now", a whole number of epoch seconds, or null']}, 400)
    elif at <= t or at > t + MAX_AHEAD:
        return _answer({"at": ["a set moment must be in the future and at most 60 days from now (use \"now\" to release at once)"]}, 400)
    codes_loaded = release.count()
    if codes_loaded == 0:
        return _answer({"codes": ["no codes are loaded yet: run the import on the server first (runbook 22, step 7)"]}, 409)
    detail = f"{'released now' if at == t else 'set to show at ' + ist_text(at)} ({codes_loaded} codes)"
    audit.record("certificates.release", "qualifier certificates", detail)  # before set_config, which commits both together
    set_config(release.KEY_AT, str(at))
    db.session.commit()
    if at == t:
        release.announce(t)
    return _answer(_state())


@player_bp.route("/api/v1/l3mon/certificate/qr.svg", methods=["GET"])  # = release.QR_PATH (a test keeps them equal)
@authed_only
def own_qr():
    """The signed-in player's own QR code (the card's picture). Anyone else, and anyone before the release, gets 404: it never says whose."""
    picture = release.qr_svg_for(get_current_user())
    if picture is None:
        response = jsonify({"success": False, "errors": {"certificate": ["not found"]}})
        response.status_code = 404
    else:
        response = current_app.response_class(picture, mimetype="image/svg+xml")
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@page_bp.route("/admin/l3mon/certificates")
@admins_only
def certificates_page():
    """The crew's page. A shell: its script reads and writes through the API above."""
    return render_template("l3mon_certificates/certificates.html")


@page_bp.route("/plugins/l3mon_certificates/assets/<path:name>")
@admins_only
def certificates_assets(name):
    """The page's script and style, for administrators only. send_from_directory refuses any path that leaves the folder."""
    return send_from_directory(ASSETS, name)
