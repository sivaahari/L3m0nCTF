"""Registration, email verification and studio setup (SP6 stream B).

Provides:
- Route and form compatibility for the theme's registration, verify and studio pages.
- GET /api/v1/l3mon/teams/available: studio name availability check for the setup wizard.
- Theme values: `l3mon.countries`, `l3mon.user_email`.
"""
import functools
import re
import secrets
import urllib.parse

from flask import current_app, jsonify, redirect, render_template, request, url_for
from werkzeug.datastructures import ImmutableMultiDict

from CTFd.plugins.l3mon_core.signup_rules import enforced
from CTFd.plugins.l3mon_guard.policies.team_name import (
    clean_team_name,
    validate_team_name,
    MSG_EMPTY as TEAM_MSG_EMPTY,
    MSG_LENGTH as TEAM_MSG_LENGTH,
    MSG_CHARS as TEAM_MSG_CHARS,
    MSG_SCRIPTS as TEAM_MSG_SCRIPTS,
    MSG_TAKEN as TEAM_MSG_TAKEN,
    ALL_MESSAGES as TEAM_NAME_MESSAGES,
)
from CTFd.plugins.l3mon_guard.policies.username import (
    ALL_MESSAGES as USERNAME_MESSAGES,
)
from CTFd.plugins.l3mon_theme.registry import value
from CTFd.utils import get_config
from CTFd.utils.countries import COUNTRIES_LIST, lookup_country_code
from CTFd.utils.decorators import ratelimit
from CTFd.utils.user import authed, get_current_user, is_admin

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
    return [{"code": code, "name": name} for code, name in COUNTRIES_LIST if code]


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

    cleaned_name, err = validate_team_name(raw_name)
    if err:
        reason = "taken" if err == TEAM_MSG_TAKEN else "invalid"
        return jsonify({"success": True, "data": {"available": False, "reason": reason}})

    return jsonify({"success": True, "data": {"available": True, "reason": None}})


def handle_register_studio():
    """Option 2 dispatcher for studio registration.
    No Teams model instantiation, db.session.add or db.session.commit.
    """
    if not authed():
        return redirect(url_for("auth.login", next="/register/studio"))

    user = get_current_user()
    if user.banned:
        return render_template("errors/403.html"), 403

    if get_config("verify_emails") and not user.verified:
        return redirect(url_for("auth.confirm"))

    if user.team_id:
        return redirect(url_for("teams.private"))

    errors = []
    if request.method == "POST":
        mode = request.form.get("mode", "create")
        if mode == "join":
            raw_invite = request.form.get("invite", "")
            code = extract_invite_code(raw_invite)
            if not code:
                errors.append("Paste the invite link your captain sent you.")
                return render_template("teams/team_enrollment.html", errors=errors)
            return redirect(url_for("teams.invite", code=code))

        # Mode create
        teamname = request.form.get("name", "").strip()
        affiliation = request.form.get("affiliation", "").strip()
        raw_country = request.form.get("country", "").strip()
        raw_ctftime = request.form.get("ctftime", "").strip()

        country = raw_country.upper()
        if country and not lookup_country_code(country):
            errors.append("Invalid country")

        if affiliation and len(affiliation) > 64:
            errors.append("Affiliations are at most 64 characters.")

        canon_website, ct_err = canonical_ctftime(raw_ctftime)
        if ct_err:
            errors.append(ct_err)

        if errors:
            return render_template("teams/team_enrollment.html", errors=errors)

        # Prepare form fields for CTFd teams.new
        # Drop ANY client-supplied website, country, ctftime, or name
        items = [
            (k, v) for k, v in request.form.items(multi=True)
            if k not in ("website", "ctftime", "country", "name")
        ]
        items.append(("name", teamname))
        if country:
            items.append(("country", country))
        if affiliation:
            items.append(("affiliation", affiliation))
        if not request.form.get("password"):
            items.append(("password", secrets.token_hex(16)))
        if canon_website:
            items.append(("website", canon_website))
        request.form = ImmutableMultiDict(items)

        teams_new_view = current_app.view_functions.get("teams.new")
        if teams_new_view:
            return teams_new_view()
        return redirect(url_for("teams.private"))

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

    # B2: Wrap auth.confirm so go=check never sends mail, 303s to studio step when verified, and confirmed players land on studio step
    def _wrap_confirm(orig_view):
        @functools.wraps(orig_view)
        def wrapped(*args, **kwargs):
            if request.method == "POST":
                go = request.form.get("go", "")
                if go == "check":
                    if not authed():
                        return redirect(url_for("auth.login"))
                    user = get_current_user()
                    if user.verified:
                        if user.team_id:
                            return redirect(url_for("teams.private"), code=303)
                        return redirect(url_for("l3mon_theme_register_studio"), code=303)
                    return render_template("confirm.html", notyet=True)
            if authed():
                user = get_current_user()
                if user.verified and request.path == "/confirm":
                    if user.team_id:
                        return redirect(url_for("teams.private"), code=303)
                    return redirect(url_for("l3mon_theme_register_studio"), code=303)
            return orig_view(*args, **kwargs)

        return wrapped

    if "auth.confirm" in app.view_functions:
        app.view_functions["auth.confirm"] = _wrap_confirm(app.view_functions["auth.confirm"])

    # Hooks for field mapping and protection
    @app.before_request
    def theme_registration_hooks():
        # B9: Keep gates off hot paths - compare path FIRST!
        path = request.path
        if path not in ("/register", "/register/studio", "/teams/new", "/teams/join", "/team"):
            return None

        # B3: Unconfirmed player gating on GET
        if request.method == "GET" and path in ("/team", "/teams/new", "/teams/join", "/register/studio"):
            if authed():
                user = get_current_user()
                if user.banned:
                    return render_template("errors/403.html"), 403
                if get_config("verify_emails") and not user.verified:
                    return redirect(url_for("auth.confirm"))
                if user.team_id and path in ("/register/studio", "/teams/new", "/teams/join"):
                    return redirect(url_for("teams.private"))

        if request.method == "POST":
            # Finding B6: Check rules acceptance on /register
            if path == "/register":
                if enforced() and request.form.get("privacy") != "1":
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
            elif path == "/teams/join" and "invite" in request.form:
                code = extract_invite_code(request.form.get("invite", ""))
                if code:
                    return redirect(url_for("teams.invite", code=code))

            # Prototype limits and field mapping on /teams/new (Finding B1, B4)
            elif path == "/teams/new":
                name = request.form.get("name", "").strip()
                affiliation = request.form.get("affiliation", "").strip()
                raw_country = request.form.get("country", "").strip()
                raw_website = request.form.get("website", "") or request.form.get("ctftime", "")

                country = raw_country.upper()
                if country and not lookup_country_code(country):
                    return render_template("teams/new_team.html", errors=["Invalid country"], name=name)

                if affiliation and len(affiliation) > 64:
                    return render_template("teams/new_team.html", errors=["Affiliations are at most 64 characters."], name=name)

                items = [
                    (k, v) for k, v in request.form.items(multi=True)
                    if k not in ("website", "ctftime", "country")
                ]
                if country:
                    items.append(("country", country))

                if raw_website:
                    canon_web, ct_err = canonical_ctftime(raw_website)
                    if ct_err or len(raw_website) > 128:
                        return render_template("teams/new_team.html", errors=["That does not look like a CTFtime team page or number."], name=name)
                    if canon_web:
                        items.append(("website", canon_web))

                if not request.form.get("password"):
                    items.append(("password", secrets.token_hex(16)))
                request.form = ImmutableMultiDict(items)

        return None
