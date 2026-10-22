"""Registration, email verification and studio setup (SP6 stream B).

Provides:
- Route and form compatibility for the theme's registration, verify and studio pages.
- GET /api/v1/l3mon/teams/available: studio name availability check for the setup wizard.
- Theme values: `l3mon.countries`, `l3mon.user_email`.
"""
import secrets
import urllib.parse

from flask import jsonify, redirect, render_template, request, url_for
from werkzeug.datastructures import ImmutableMultiDict

from CTFd.cache import clear_team_session, clear_user_session
from CTFd.models import Teams, db
from CTFd.plugins.l3mon_guard.policies.team_name import (
    clean_team_name,
    validate_team_name,
)
from CTFd.plugins.l3mon_theme.registry import value
from CTFd.utils import get_config, validators
from CTFd.utils.countries import COUNTRIES_LIST
from CTFd.utils.decorators import ratelimit
from CTFd.utils.user import authed, get_current_user
from CTFd.utils.validators import ValidationError


@value("user_email", default="")
def user_email():
    if authed():
        u = get_current_user()
        return u.email if u else ""
    return ""


@value("countries", default=[])
def countries():
    return [{"code": code.lower(), "name": name} for code, name in COUNTRIES_LIST if code]


def extract_invite_code(raw):
    raw = (raw or "").strip()
    if "?" in raw:
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs and qs["code"]:
            return qs["code"][0].strip()
    return raw


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


def handle_register_studio():
    if not authed():
        return redirect(url_for("auth.login", next="/register/studio"))

    user = get_current_user()
    if user.team_id:
        return redirect(url_for("challenges.listing"))

    if get_config("verify_emails") and not user.verified:
        return redirect(url_for("auth.confirm"))

    errors = []
    if request.method == "POST":
        mode = request.form.get("mode", "create")
        if mode == "join":
            raw_invite = request.form.get("invite", "")
            code = extract_invite_code(raw_invite)
            if not code:
                errors.append("Please provide an invite code")
                return render_template("teams/team_enrollment.html", errors=errors)
            return redirect(url_for("teams.invite", code=code))

        # Mode create
        teamname = request.form.get("name", "").strip()
        country = request.form.get("country", "").strip().upper()
        affiliation = request.form.get("affiliation", "").strip()
        website = request.form.get("ctftime", "").strip()

        cleaned_name, err = validate_team_name(teamname)
        if err:
            errors.append(err)

        if not teamname or not cleaned_name:
            errors.append("That team name is invalid")
        elif len(cleaned_name) < 2 or len(cleaned_name) > 32:
            errors.append("Studio names are 2 to 32 characters.")

        if country:
            try:
                validators.validate_country_code(country)
            except ValidationError:
                errors.append("Invalid country")

        if affiliation and len(affiliation) > 128:
            errors.append("Please provide a shorter affiliation")

        num_teams_limit = int(get_config("num_teams", default=0))
        num_teams = Teams.query.filter_by(banned=False, hidden=False).count()
        if num_teams_limit and num_teams >= num_teams_limit:
            errors.append(f"Reached the maximum number of teams ({num_teams_limit}). Please join an existing team.")

        if errors:
            return render_template("teams/team_enrollment.html", errors=errors), 403

        passphrase = secrets.token_hex(16)
        team = Teams(
            name=cleaned_name,
            password=passphrase,
            captain_id=user.id,
            hidden=(user.type == "admin"),
        )
        if country:
            team.country = country
        if affiliation:
            team.affiliation = affiliation
        if website:
            team.website = website

        db.session.add(team)
        db.session.commit()

        user.team_id = team.id
        db.session.commit()

        clear_user_session(user_id=user.id)
        clear_team_session(team_id=team.id)

        return redirect(url_for("challenges.listing"))

    return render_template("teams/team_enrollment.html", errors=errors)


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

    # Route /register/verify to CTFd's confirm view
    confirm_view = app.view_functions.get("auth.confirm")
    if confirm_view:
        app.add_url_rule(
            "/register/verify",
            endpoint="l3mon_theme_register_verify",
            view_func=confirm_view,
            methods=["GET", "POST"],
        )

    # Route /register/studio
    app.add_url_rule(
        "/register/studio",
        endpoint="l3mon_theme_register_studio",
        view_func=handle_register_studio,
        methods=["GET", "POST"],
    )

    # Route aliases for /register/go-live and /register/done
    app.add_url_rule(
        "/register/go-live",
        endpoint="l3mon_theme_register_golive",
        view_func=lambda: redirect(url_for("challenges.listing")),
        methods=["GET", "HEAD"],
    )
    app.add_url_rule(
        "/register/done",
        endpoint="l3mon_theme_register_done",
        view_func=lambda: redirect(url_for("challenges.listing")),
        methods=["GET", "HEAD"],
    )

    # Hooks for field mapping and protection
    @app.before_request
    def theme_registration_hooks():
        if request.method == "POST":
            # Map form field 'username' -> 'name' for /register and /register/account if 'name' is missing
            if request.path in ("/register", "/register/account"):
                if "username" in request.form and "name" not in request.form:
                    items = list(request.form.items(multi=True))
                    items.append(("name", request.form.get("username", "")))
                    request.form = ImmutableMultiDict(items)

            # Map invite input on /teams/join to invite code redirection
            elif request.path == "/teams/join" and "invite" in request.form:
                code = extract_invite_code(request.form.get("invite", ""))
                if code:
                    return redirect(url_for("teams.invite", code=code))

            # Ensure random password generated on /teams/new if empty
            elif request.path == "/teams/new":
                if not request.form.get("password"):
                    items = list(request.form.items(multi=True))
                    items.append(("password", secrets.token_hex(16)))
                    request.form = ImmutableMultiDict(items)

        return None
