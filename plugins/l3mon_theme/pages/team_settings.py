"""The studio page (/team) and settings page (/settings) for the theme (Stream D).

Provides:
- Value `l3mon.studio_context`: complete view-model for `teams/private.html` (including `_team.html`)
- Value `l3mon.settings_context`: complete view-model for `settings.html` (including `_settings.html`)
- Routes:
    POST /team                       update studio details (captain only)
    POST /team/members/<id>/captain  hand over captain's chair (captain only)
    POST /settings                   update username, email, or password
"""
import logging
from flask import abort, redirect, request, session, url_for

from CTFd.cache import clear_team_session, clear_user_session
from CTFd.constants.sessions import Session
from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_board import board
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_guard.policies.disposable import is_disposable_domain
from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used
from CTFd.plugins.l3mon_guard.policies.team_name import canonical_team_name, validate_team_name
from CTFd.plugins.l3mon_theme.registry import value
from CTFd.schemas.teams import TeamSchema
from CTFd.schemas.users import UserSchema
from CTFd.utils import get_config
from CTFd.utils.countries import SELECT_COUNTRIES_LIST, lookup_country_code
from CTFd.utils.email import check_email_is_blacklisted, check_email_is_whitelisted
from CTFd.utils.security.auth import update_user
from CTFd.utils.user import authed, get_current_team, get_current_user, is_admin

_log = logging.getLogger("l3mon.theme.team_settings")

NOTICES = {
    "team-saved": "Your changes are saved.",
    "captain-moved": "Done. The captain's chair has been handed over.",
    "settings-saved": "Your changes are saved.",
}

FLASH_NOTICE_KEY = "_l3mon_acct_notice"
FLASH_ERROR_KEY = "_l3mon_acct_errors"
FLASH_FORM_KEY = "_l3mon_acct_form"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _take_flash(key: str, default=None):
    val = session.get(key, default)
    if key in session:
        session.pop(key, None)
    return val


def _get_provider_for_user(user: Users) -> str | None:
    """Return provider name ('Google' or 'CTFtime') if account was created via OAuth."""
    if not user:
        return None
    if getattr(user, "oauth_id", None) is not None:
        return "CTFtime"
    try:
        from CTFd.plugins.l3mon_auth.models import Identity
        ident = Identity.query.filter_by(user_id=user.id).first()
        if ident and ident.provider == "google":
            return "Google"
    except Exception:
        pass
    return None


@value("studio_context", default={})
def studio_context() -> dict:
    """Build the view-model `tm.*` for the studio page."""
    user = get_current_user()
    team = get_current_team()
    if not user or not team:
        return {}

    team_size = int(get_config("team_size") or 4)
    phase = current_phase()
    score, place, of = board.numbers(team, phase)

    if phase.frozen:
        standing = f"Ranks frozen · {score} TRP"
    elif place is not None:
        standing = f"{_ordinal(place)} of {of} · {score} TRP"
    else:
        standing = f"Not ranked yet · {score} TRP"

    # Active members, captain first, then viewer, then alphabetical
    user_rows = (
        db.session.query(Users.id, Users.name)
        .filter(Users.team_id == team.id, Users.banned.is_(False))
        .all()
    )

    members_data = []
    for uid, uname in user_rows:
        is_cap = (uid == team.captain_id)
        is_you = (uid == user.id)
        tag_parts = []
        if is_cap:
            tag_parts.append("captain")
        if is_you:
            tag_parts.append("you")
        members_data.append({
            "id": str(uid),
            "name": uname,
            "ini": uname[:1].upper() if uname else "P",
            "tag": " · ".join(tag_parts),
            "cls": "is-you" if is_you else "",
            "_captain": is_cap,
            "_you": is_you,
        })

    # Sort: captain first, then you, then by name case-insensitively
    members_data.sort(
        key=lambda m: (not m["_captain"], not m["_you"], m["name"].casefold())
    )

    viewer_is_captain = (team.captain_id == user.id)
    seats_left = max(0, team_size - len(members_data))
    can_invite = viewer_is_captain and (seats_left > 0)
    seats_text = "1 seat is free" if seats_left == 1 else f"{seats_left} seats are free"

    # Eligible teammates for captaincy handover (not the captain)
    others = (
        [{"id": m["id"], "name": m["name"]} for m in members_data if not m["_captain"]]
        if viewer_is_captain
        else []
    )

    # Dynamic invite link from host
    invite_code = team.get_invite_code()
    platform_host = request.host
    scheme = "https" if (request.is_secure or request.headers.get("X-Forwarded-Proto") == "https") else request.scheme
    invite_url = f"{scheme}://{platform_host}/teams/invite?code={invite_code}"

    # Country select list
    countries = [
        {"code": code, "name": name, "sel": "selected" if code == team.country else ""}
        for code, name in SELECT_COUNTRIES_LIST
        if code
    ]

    # Meta text: country name and affiliation
    country_display = lookup_country_code(team.country) if team.country else None
    meta_parts = [p for p in (country_display, team.affiliation) if p]
    meta_text = " · ".join(meta_parts)

    captain_user = next((m for m in members_data if m["_captain"]), None)
    captain_name = captain_user["name"] if captain_user else ""

    flashed_notice = _take_flash(FLASH_NOTICE_KEY)
    notice_text = NOTICES.get(flashed_notice, "")

    return {
        "name": team.name,
        "meta": meta_text,
        "standing": standing,
        "seats": f"{len(members_data)} of {team_size}",
        "members": members_data,
        "isCaptain": viewer_is_captain,
        "hasOthers": len(others) > 0,
        "others": others,
        "canInvite": can_invite,
        "seatsText": seats_text,
        "invite": invite_url,
        "captain": captain_name,
        "size": str(team_size),
        "affiliation": team.affiliation or "",
        "country": team.country or "",
        "countries": countries,
        "hasNotice": bool(notice_text),
        "notice": notice_text,
    }


@value("settings_context", default={})
def settings_context() -> dict:
    """Build the view-model `st.*` for the settings page."""
    user = get_current_user()
    if not user:
        return {}

    flashed_notice = _take_flash(FLASH_NOTICE_KEY)
    saved = (flashed_notice == "settings-saved")

    flashed_errors = _take_flash(FLASH_ERROR_KEY, {})
    flashed_form = _take_flash(FLASH_FORM_KEY, {})

    provider = _get_provider_for_user(user)
    via_provider = bool(provider)

    name_val = flashed_form.get("name", user.name)
    email_val = flashed_form.get("email", user.email)

    # Verification state
    # pending: True when email unconfirmed and no errors
    pending = (user.verified is False) and not flashed_errors
    confirmed = (user.verified is True) and not flashed_errors

    summary = []
    err = {"name": None, "email": None, "current": None, "password": None}

    if flashed_errors:
        field_anchors = {
            "name": "#f-name",
            "email": "#f-email",
            "confirm": "#f-current",
            "current": "#f-current",
            "password": "#p-new",
        }
        for field, msg in flashed_errors.items():
            if isinstance(msg, list):
                msg = msg[0] if msg else "Invalid value."
            anchor = field_anchors.get(field, "#main")
            summary.append({"href": anchor, "text": str(msg)})
            if field in err:
                err[field] = str(msg)
            elif field == "confirm":
                err["current"] = str(msg)

    return {
        "name": name_val,
        "email": email_val,
        "confirmed": confirmed,
        "pending": pending,
        "viaProvider": via_provider,
        "provider": provider or "",
        "saved": saved,
        "hasSummary": len(summary) > 0,
        "summary": summary,
        "err": err,
    }


def _verify_nonce() -> None:
    sent_nonce = request.form.get("nonce", "")
    expected = Session.nonce or ""
    if not sent_nonce or sent_nonce != expected:
        abort(403)


def install(app):
    """Register write endpoints for team and settings pages."""

    @app.route("/team", methods=["POST"])
    def update_team_details():
        _verify_nonce()
        if not authed():
            return redirect(url_for("auth.login"))

        team = get_current_team()
        user = get_current_user()
        if not team:
            return redirect(url_for("teams.private"))

        # Captain check: 403 for member before any other work (rule D2)
        if team.captain_id != user.id:
            abort(403)

        name = request.form.get("name", "").strip()
        country = request.form.get("country", "").strip()
        affiliation = request.form.get("affiliation", "").strip()

        errors = {}

        # Validate team name policy (confusables, length, collision, impersonation)
        clean_name, name_err = validate_team_name(name, is_crew=is_admin(), exclude_team_id=team.id)
        if name_err:
            errors["name"] = [name_err]
        else:
            name = clean_name

        # Validate country code if provided
        if country and lookup_country_code(country) is None:
            errors["country"] = ["Invalid country selection."]

        if not errors:
            load_data = {"name": name, "country": country, "affiliation": affiliation}
            schema_res = TeamSchema(view="self", instance=team, partial=True).load(load_data)
            if schema_res.errors:
                for k, v in schema_res.errors.items():
                    errors.setdefault(k, []).extend(v if isinstance(v, list) else [v])

        if errors:
            db.session.rollback()
            session[FLASH_ERROR_KEY] = errors
            session[FLASH_FORM_KEY] = {"name": name, "country": country, "affiliation": affiliation}
            return redirect("/team", code=303)

        db.session.commit()
        clear_team_session(team_id=team.id)
        session[FLASH_NOTICE_KEY] = "team-saved"
        return redirect("/team", code=303)

    @app.route("/team/members/<int:member_id>/captain", methods=["POST"])
    def handover_captain(member_id):
        _verify_nonce()
        if not authed():
            return redirect(url_for("auth.login"))

        team = get_current_team()
        user = get_current_user()
        if not team:
            return redirect(url_for("teams.private"))

        # Captain only (rule D2)
        if team.captain_id != user.id:
            abort(403)

        # Target MUST belong to this team and be active (rule D2, D3)
        target = Users.query.filter_by(id=member_id, team_id=team.id, banned=False).first()
        if not target:
            # Refuse target not in studio
            abort(400)

        team.captain_id = target.id
        db.session.commit()
        clear_team_session(team_id=team.id)
        session[FLASH_NOTICE_KEY] = "captain-moved"
        return redirect("/team", code=303)

    @app.route("/settings", methods=["POST"])
    def update_settings():
        _verify_nonce()
        if not authed():
            return redirect(url_for("auth.login"))

        user = get_current_user()
        part = request.form.get("part", "details")
        provider = _get_provider_for_user(user)

        errors = {}

        if part == "details":
            data = {}
            if "name" in request.form:
                data["name"] = request.form.get("name", "").strip()

            if not provider and "email" in request.form:
                email = request.form.get("email", "").strip()
                confirm = request.form.get("confirm", "")
                data["email"] = email
                if email and email != user.email:
                    data["confirm"] = confirm
                    # Disposable domain check
                    if is_disposable_domain(email):
                        errors["email"] = ["Disposable email addresses are not allowed."]
                    # Disguised / duplicate email check
                    elif is_email_disguise_used(email, exclude_user_id=user.id):
                        errors["email"] = ["Email address has already been taken."]

            schema_res = UserSchema(view="self", instance=user, partial=True).load(data)
            if schema_res.errors:
                for k, v in schema_res.errors.items():
                    errors.setdefault(k, []).extend(v if isinstance(v, list) else [v])

            if errors:
                db.session.rollback()
                session[FLASH_ERROR_KEY] = errors
                session[FLASH_FORM_KEY] = data
                return redirect("/settings", code=303)

            # Check if email changed and needs re-verification
            if not provider and "email" in data and data["email"] != user.email:
                if get_config("verify_emails"):
                    user.verified = False

            db.session.commit()
            clear_user_session(user_id=user.id)
            session[FLASH_NOTICE_KEY] = "settings-saved"
            return redirect("/settings", code=303)

        elif part == "password":
            if provider:
                abort(400)

            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            data = {"password": password, "confirm": confirm}

            schema_res = UserSchema(view="self", instance=user, partial=True).load(data)
            if schema_res.errors:
                for k, v in schema_res.errors.items():
                    errors.setdefault(k, []).extend(v if isinstance(v, list) else [v])

            if errors:
                db.session.rollback()
                session[FLASH_ERROR_KEY] = errors
                return redirect("/settings", code=303)

            db.session.commit()
            update_user(user)  # Resets session hash, invalidating other devices (rule D2)
            clear_user_session(user_id=user.id)
            session[FLASH_NOTICE_KEY] = "settings-saved"
            return redirect("/settings", code=303)

        abort(400)
