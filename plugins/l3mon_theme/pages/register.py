"""Registration, email verification and studio setup (SP6 stream B).

Provides:
- Route and form compatibility for the theme's registration page.
- GET /api/v1/l3mon/teams/available: studio name availability check for the setup wizard.
"""
from flask import jsonify, request
from werkzeug.datastructures import ImmutableMultiDict

from CTFd.plugins.l3mon_guard.policies.team_name import (
    clean_team_name,
    validate_team_name,
)
from CTFd.utils.decorators import ratelimit
from CTFd.utils.user import authed


def check_studio_availability():
    if not authed():
        return jsonify({"success": False, "error": "auth_required", "message": "You must be signed in"}), 401

    raw_name = request.args.get("name", "")
    if not raw_name or not isinstance(raw_name, str):
        return jsonify({"success": True, "data": {"available": False, "reason": "invalid"}})

    clean = clean_team_name(raw_name)
    if len(clean) < 2 or len(clean) > 32:
        return jsonify({"success": True, "data": {"available": False, "reason": "invalid"}})

    cleaned_name, err = validate_team_name(raw_name)
    if err:
        reason = "taken" if ("taken" in err.lower() or "reserved" in err.lower()) else "invalid"
        return jsonify({"success": True, "data": {"available": False, "reason": reason}})

    return jsonify({"success": True, "data": {"available": True, "reason": None}})


def install(app):
    # Studio availability check
    app.add_url_rule(
        "/api/v1/l3mon/teams/available",
        endpoint="l3mon_theme_teams_available",
        view_func=ratelimit(method="GET", limit=20, interval=60, key_prefix="l3mon_avail")(check_studio_availability),
        methods=["GET"],
    )

    # Route /register/account to CTFd's register view
    reg_view = app.view_functions.get("auth.register")
    if reg_view:
        app.add_url_rule(
            "/register/account",
            endpoint="l3mon_theme_register_account",
            view_func=reg_view,
            methods=["GET", "POST"],
        )

    # Map form field 'username' -> 'name' for /register and /register/account if 'name' is missing
    @app.before_request
    def map_registration_username():
        if request.method == "POST" and request.path in ("/register", "/register/account"):
            if "username" in request.form and "name" not in request.form:
                items = list(request.form.items(multi=True))
                items.append(("name", request.form.get("username", "")))
                request.form = ImmutableMultiDict(items)
        return None
