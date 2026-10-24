"""Registration, email verification and studio setup (SP6 stream B).

Provides:
- Route and form compatibility for the theme's registration, verify and studio pages.
- GET /api/v1/l3mon/teams/available: studio name availability check for the setup wizard.
- Theme values: `l3mon.countries`, `l3mon.user_email`.
"""
import re
import secrets
import urllib.parse

from flask import current_app, jsonify, redirect, render_template, request, url_for
from werkzeug.datastructures import ImmutableMultiDict

from CTFd.plugins.l3mon_guard.policies.team_name import (
    clean_team_name,
    validate_team_name,
)
from CTFd.plugins.l3mon_theme.registry import value
from CTFd.utils import get_config
from CTFd.utils.countries import COUNTRIES_LIST
from CTFd.utils.decorators import ratelimit
from CTFd.utils.user import authed, get_current_user

CTFTIME_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?ctftime\.org/team/(\d{1,8})/?(?:[?#].*)?$",
    re.IGNORECASE,
)


@value("user_email", default="")
def user_email():
    if authed():
        u = get_current_user()
        return u.email if u else ""
    return ""


@value("countries", default=[])
def countries():
    return [{"code": code.lower(), "name": name} for code, name in COUNTRIES_LIST if code]


def canonical_ctftime(raw: str) -> tuple[str | None, str | None]:
    """Validate and canonicalize CTFtime team input to https://ctftime.org/team/<number>.
    Returns (canonical_url, error_message).
    """
    raw = (raw or "").strip()
    if not raw:
        return "", None
    if len(raw) > 128:
        return None, "That does not look like a CTFtime team page or number."

    if re.fullmatch(r"\d{1,8}", raw):
        num = int(raw)
        if num > 0:
            return f"https://ctftime.org/team/{num}", None
        return None, "That does not look like a CTFtime team page or number."

    m = CTFTIME_RE.match(raw)
    if m:
        num = int(m.group(1))
        if num > 0:
            canon = f"https://ctftime.org/team/{num}"
            if len(canon) <= 128:
                return canon, None
    return None, "That does not look like a CTFtime team page or number."


def extract_invite_code(raw: str) -> str:
    """Extract and validate invite code from raw token or full invite URL.
    Preserves casing; refuses spaces inside, bad lengths, or external sites.
    """
    raw = (raw or "").strip()
    if not raw or any(c.isspace() for c in raw):
        return ""
    if "?" in raw or raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/"):
        try:
            parsed = urllib.parse.urlparse(raw)
            if parsed.path.rstrip("/") != "/teams/invite":
                return ""
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs and qs["code"]:
                code = qs["code"][0].strip()
                if not code or any(c.isspace() for c in code) or len(code) < 4 or len(code) > 256:
                    return ""
                return code
            return ""
        except Exception:
            return ""
    if len(raw) < 4 or len(raw) > 256:
        return ""
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
    """Option 2 dispatcher for studio registration.
    No Teams model instantiation, db.session.add or db.session.commit.
    """
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
        affiliation = request.form.get("affiliation", "").strip()
        raw_ctftime = request.form.get("ctftime", "").strip()

        clean_name = clean_team_name(teamname)
        if not teamname or not clean_name:
            errors.append("Team name cannot be empty.")
        elif len(clean_name) < 2 or len(clean_name) > 32:
            errors.append("Studio names are 2 to 32 characters.")

        if affiliation and len(affiliation) > 64:
            errors.append("Affiliations are at most 64 characters.")

        canon_website, ct_err = canonical_ctftime(raw_ctftime)
        if ct_err:
            errors.append(ct_err)

        if errors:
            return render_template("teams/team_enrollment.html", errors=errors)

        # Prepare form fields for CTFd teams.new
        items = list(request.form.items(multi=True))
        if not request.form.get("password"):
            items.append(("password", secrets.token_hex(16)))
        if canon_website:
            items.append(("website", canon_website))
        request.form = ImmutableMultiDict(items)

        teams_new_view = current_app.view_functions.get("teams.new")
        if teams_new_view:
            return teams_new_view()
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

    # Route aliases for /register/account and /register/verify: GET and HEAD only (Finding B8)
    app.add_url_rule(
        "/register/account",
        endpoint="l3mon_theme_register_account",
        view_func=lambda: redirect(url_for("auth.register"), code=302),
        methods=["GET", "HEAD"],
    )
    app.add_url_rule(
        "/register/verify",
        endpoint="l3mon_theme_register_verify",
        view_func=lambda: redirect(url_for("auth.confirm"), code=302),
        methods=["GET", "HEAD"],
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
            # Finding B6: Check rules acceptance on /register
            if request.path == "/register":
                if request.form.get("privacy") != "1":
                    name = request.form.get("name") or request.form.get("username", "")
                    email = request.form.get("email", "")
                    return render_template(
                        "register.html",
                        errors=["Tick the box to accept the rules."],
                        name=name,
                        email=email,
                    )
                # Map form field 'username' -> 'name' if 'name' is missing
                if "username" in request.form and "name" not in request.form:
                    items = list(request.form.items(multi=True))
                    items.append(("name", request.form.get("username", "")))
                    request.form = ImmutableMultiDict(items)

            # Map invite input on /teams/join to invite code redirection
            elif request.path == "/teams/join" and "invite" in request.form:
                code = extract_invite_code(request.form.get("invite", ""))
                if code:
                    return redirect(url_for("teams.invite", code=code))

            # Prototype limits and field mapping on /teams/new (Finding B1)
            elif request.path == "/teams/new":
                name = request.form.get("name", "").strip()
                affiliation = request.form.get("affiliation", "").strip()
                raw_website = request.form.get("website", "") or request.form.get("ctftime", "")

                clean = clean_team_name(name)
                if clean and (len(clean) < 2 or len(clean) > 32):
                    return render_template("teams/new_team.html", errors=["Studio names are 2 to 32 characters."], name=name)
                if affiliation and len(affiliation) > 64:
                    return render_template("teams/new_team.html", errors=["Affiliations are at most 64 characters."], name=name)
                if raw_website:
                    canon_web, ct_err = canonical_ctftime(raw_website)
                    if ct_err or len(raw_website) > 128:
                        return render_template("teams/new_team.html", errors=["That does not look like a CTFtime team page or number."], name=name)
                    items = list(request.form.items(multi=True))
                    items = [(k, v) for k, v in items if k not in ("website", "ctftime")]
                    if canon_web:
                        items.append(("website", canon_web))
                    request.form = ImmutableMultiDict(items)

                if not request.form.get("password"):
                    items = list(request.form.items(multi=True))
                    items.append(("password", secrets.token_hex(16)))
                    request.form = ImmutableMultiDict(items)

        return None
