"""The studio page (/team) and settings page (/settings) for the theme (Stream D).

Provides:
- Value `l3mon.studio_context`: complete view-model for `teams/private.html` (including `_team.html`)
- Value `l3mon.settings_context`: complete view-model for `settings.html` (including `_settings.html`)
- Routes:
    POST /team                       update studio details (captain only)
    POST /team/members/<id>/captain  hand over captain's chair (captain only)
    POST /team/members/<id>/remove   remove a member; what they earned stays with the studio (captain only)
    POST /settings                   update username, email, or password
"""
import datetime
import logging
from flask import abort, redirect, request, session, url_for

from CTFd.cache import clear_team_session, clear_user_session
from CTFd.constants.sessions import Session
from CTFd.models import Awards, Challenges, Solves, Teams, Users, db
from CTFd.plugins.l3mon_board import board
from CTFd.plugins.l3mon_core import activity, studio_rules
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.visibility import visible_challenge_ids
from CTFd.plugins.l3mon_core.player_locks import lock_studio
from CTFd.plugins.l3mon_core.signup_rules import enforced
from CTFd.plugins.l3mon_guard.policies.disposable import is_disposable_domain
from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used
from CTFd.plugins.l3mon_guard.policies.password import (
    V_MSG_PASS_COMMON,
    V_MSG_PASS_EMAIL,
    V_MSG_PASS_EMPTY,
    V_MSG_PASS_LONG,
    V_MSG_PASS_SHORT,
    V_MSG_PASS_USER,
    validate_password,
)
from CTFd.plugins.l3mon_guard.policies.team_name import canonical_team_name, validate_team_name
from CTFd.plugins.l3mon_guard.policies.username import validate_username
from CTFd.plugins.l3mon_theme.registry import value
from CTFd.schemas.teams import TeamSchema
from CTFd.schemas.users import UserSchema
from CTFd.utils import get_config
from CTFd.utils.config import can_send_mail
from CTFd.utils.countries import SELECT_COUNTRIES_LIST, lookup_country_code
from CTFd.utils.decorators import ratelimit
from CTFd.utils.email import check_email_is_blacklisted, check_email_is_whitelisted, verify_email_address
from CTFd.utils.security.auth import update_user
from CTFd.utils.user import authed, get_current_team, get_current_user, is_admin
from CTFd.utils.validators import validate_email

_log = logging.getLogger("l3mon.theme.team_settings")

NOTICES = {
    "team-saved": "Your changes are saved.",
    "captain-moved": "Done. The captain's chair has been handed over.",
    "member-removed": "Done. The member was removed from your studio. Their solves and hints stay with the studio.",
    "settings-saved": "Your changes are saved.",
    "settings-saved-confirm": "Your changes are saved. Open the link we sent to your new address to confirm it.",
    "settings-saved-nomail": "Your changes are saved, but confirmation mail could not be sent. Please ask the crew to confirm your account.",
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


def _certificate(user):
    """The viewer's own qualifier certificate card (plugins/l3mon_certificates), or None: not released, no code, or the plugin is absent.
    A failure there never takes the studio page down."""
    try:
        from CTFd.plugins.l3mon_certificates.release import card_for
    except ImportError:
        return None
    try:
        return card_for(user)
    except Exception:  # noqa: BLE001
        _log.warning("l3mon: the certificate card could not be drawn", exc_info=True)
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

    # Solves and player-wise points for this studio
    solves_query = (
        db.session.query(
            Solves.challenge_id,
            Solves.date,
            Challenges.name,
            Challenges.category,
            Challenges.value,
            Users.name,
            Solves.user_id,
        )
        .join(Challenges, Solves.challenge_id == Challenges.id)
        .join(Users, Solves.user_id == Users.id)
        .filter(Solves.account_id == team.id)
        .order_by(Solves.date.desc())
    )
    # a programme the crew pulled back leaves every list, this one too (the same rule as the board and /api/v1/teams/me/solves)
    solves_rows = solves_query.all()
    shown = visible_challenge_ids(admin=False, solved_ids=frozenset(row[0] for row in solves_rows))
    solves_rows = [row for row in solves_rows if row[0] in shown]

    solves_data = []
    member_scores = {}
    for cid, sdate, cname, ccat, cval, solver_name, solver_uid in solves_rows:
        ist_time = sdate + datetime.timedelta(hours=5, minutes=30) if sdate else None
        time_str = ist_time.strftime("%d %b, %H:%M IST") if ist_time else ""
        val = int(cval or 0)
        solves_data.append({
            "name": cname or "Programme",
            "category": ccat or "",
            "trp": val,
            "solver": solver_name or "Teammate",
            "time": time_str,
        })
        member_scores[solver_uid] = member_scores.get(solver_uid, 0) + val

    awards_rows = (
        db.session.query(Awards.user_id, Awards.value)
        .filter(Awards.team_id == team.id, Awards.user_id.isnot(None))
        .all()
    )
    for auid, aval in awards_rows:
        member_scores[auid] = member_scores.get(auid, 0) + int(aval or 0)

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
        m_score = member_scores.get(uid, 0)
        members_data.append({
            "id": str(uid),
            "name": uname,
            "ini": uname[:1].upper() if uname else "P",
            "tag": " · ".join(tag_parts),
            "cls": "is-you" if is_you else "",
            "score": m_score,
            "trp": m_score,
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
    if not captain_user and members_data:
        captain_name = members_data[0]["name"]
    else:
        captain_name = captain_user["name"] if captain_user else ""

    flashed_notice = _take_flash(FLASH_NOTICE_KEY)
    notice_text = NOTICES.get(flashed_notice, "")

    flashed_errors = _take_flash(FLASH_ERROR_KEY, {})
    flashed_form = _take_flash(FLASH_FORM_KEY, {})

    summary = []
    err = {"name": None, "country": None, "affiliation": None, "members": None}

    # A refusal of a removal belongs inside the removal card, not in the "Fix these to save your changes" list of the details form.
    removal_refusal = flashed_errors.pop("members", None) if isinstance(flashed_errors, dict) else None
    if removal_refusal:
        err["members"] = str(removal_refusal[0] if isinstance(removal_refusal, list) else removal_refusal)

    if flashed_errors:
        field_anchors = {"name": "#t-name", "country": "#t-country", "affiliation": "#t-aff"}
        for field, msg in flashed_errors.items():
            if isinstance(msg, list):
                msg = msg[0] if msg else "Invalid value."
            anchor = field_anchors.get(field, "#main")
            summary.append({"href": anchor, "text": str(msg)})
            if field in err:
                err[field] = str(msg)

    certificate = _certificate(user)

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
        "solves": solves_data,
        "solves_count": len(solves_data),
        "solvesCount": str(len(solves_data)),
        "hasSolves": len(solves_data) > 0,
        "hasCertificate": certificate is not None,
        "certificate": certificate or {},
        "hasNotice": bool(notice_text),
        "notice": notice_text,
        "hasSummary": len(summary) > 0,
        "summary": summary,
        "err": err,
    }


@value("settings_context", default={})
def settings_context() -> dict:
    """Build the view-model `st.*` for the settings page."""
    user = get_current_user()
    if not user:
        return {}

    flashed_notice = _take_flash(FLASH_NOTICE_KEY)
    saved = bool(flashed_notice)
    notice_text = NOTICES.get(flashed_notice, "")

    flashed_errors = _take_flash(FLASH_ERROR_KEY, {})
    flashed_form = _take_flash(FLASH_FORM_KEY, {})

    provider = _get_provider_for_user(user)
    via_provider = bool(provider)
    is_google = (provider == "Google")

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
        "isGoogle": is_google,
        "saved": saved,
        "notice": notice_text,
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
    @ratelimit(method="POST", limit=10, interval=60)
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

        lock_studio(team.id)  # two renames (a second tab, a double press) must not both pass the once-a-day rule

        raw_name = request.form.get("name", "")
        country = request.form.get("country", "").strip()
        affiliation = request.form.get("affiliation", "").strip()

        errors = {}

        # D3: If the posted name equals the stored one (raw_name == team.name), do not judge it again
        if raw_name == team.name:
            name = team.name
        else:
            # Validate team name policy (confusables, length, collision, impersonation)
            clean_name, name_err = validate_team_name(raw_name, is_crew=is_admin(), exclude_team_id=team.id)
            if name_err:
                errors["name"] = [name_err]
                name = raw_name
            else:
                name = clean_name

        # The rename policy (studio_rules.py): once a day, locked from the real start or after the first solve. The API path has the same rule in a before_request.
        if "name" not in errors and name != team.name:
            refusal = studio_rules.rename_refusal(team, name, user)
            if refusal:
                errors["name"] = [refusal]
                activity.record("studio.rename.refused", actor=user, team_id=team.id, target=f"team:{team.id}", detail={"asked": name, "via": "form"})

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
    @ratelimit(method="POST", limit=10, interval=60)
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

    @app.route("/team/members/<int:member_id>/remove", methods=["POST"])
    @ratelimit(method="POST", limit=10, interval=60)
    def remove_member(member_id):
        _verify_nonce()
        if not authed():
            return redirect(url_for("auth.login"))

        team = get_current_team()
        user = get_current_user()
        if not team:
            return redirect(url_for("teams.private"))

        # Captain only (like the hand-over of the chair)
        if team.captain_id != user.id:
            abort(403)

        lock_studio(team.id)  # a removal and a join of the same studio take turns

        # A person who is not in this studio: a made-up request, not something the page can send
        target = Users.query.filter_by(id=member_id, team_id=team.id).first()
        if not target:
            abort(400)

        refusal = studio_rules.removal_refusal(team, target, user)
        if refusal:
            session[FLASH_ERROR_KEY] = {"members": [refusal]}
            return redirect("/team", code=303)

        studio_rules.remove_member(team, target, user)
        db.session.commit()
        clear_team_session(team_id=team.id)
        clear_user_session(user_id=target.id)
        session[FLASH_NOTICE_KEY] = "member-removed"
        return redirect("/team", code=303)

    @app.route("/settings", methods=["POST"])
    @ratelimit(method="POST", limit=10, interval=60)
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
                raw_name = request.form.get("name", "")
                if raw_name.strip() != user.name:
                    clean_name, name_err = validate_username(
                        raw_name, is_crew=is_admin(), exclude_user_id=user.id
                    )
                    if name_err:
                        errors["name"] = [name_err]
                    else:
                        data["name"] = clean_name

            email_changed = False
            if not provider and "email" in request.form:
                raw_email = request.form.get("email", "")
                email = raw_email.strip()
                confirm = request.form.get("confirm", "")
                if email and email.casefold() != user.email.casefold():
                    email_changed = True
                    # D1: Check format first with CTFd's own validator and prototype words
                    if not validate_email(email):
                        errors["email"] = ["Enter an email address like name@example.com."]
                    elif is_disposable_domain(email):
                        errors["email"] = ["Disposable email addresses are not allowed."]
                    elif is_email_disguise_used(email, exclude_user_id=user.id):
                        errors["email"] = ["Email address has already been taken."]
                    else:
                        data["email"] = email
                        data["confirm"] = confirm

            if not errors and data:
                schema_res = UserSchema(view="self", instance=user, partial=True).load(data)
                # If name only differed in case, avoid duplicate error from case-insensitive DB
                if (
                    "name" in data
                    and data["name"].casefold() == user.name.casefold()
                    and schema_res.errors
                    and "name" in schema_res.errors
                ):
                    schema_res.errors.pop("name", None)
                    user.name = data["name"]

                if schema_res.errors:
                    for k, v in schema_res.errors.items():
                        errors.setdefault(k, []).extend(v if isinstance(v, list) else [v])

            if errors:
                db.session.rollback()
                session[FLASH_ERROR_KEY] = errors
                session[FLASH_FORM_KEY] = {
                    "name": request.form.get("name", user.name),
                    "email": request.form.get("email", user.email),
                }
                return redirect("/settings", code=303)

            # D2: Email change handling
            if email_changed:
                if get_config("verify_emails"):
                    user.verified = False

            db.session.commit()
            clear_user_session(user_id=user.id)
            if data:  # which fields changed, never their values (the ledger, docs/runbooks/17-activity-log.md)
                activity.record("account.update", actor=user, team_id=user.team_id, target=f"user:{user.id}", detail={"fields": sorted(k for k in data if k != "confirm")})

            if email_changed and get_config("verify_emails"):
                if can_send_mail():
                    verify_email_address(user.email)
                    session[FLASH_NOTICE_KEY] = "settings-saved-confirm"
                else:
                    session[FLASH_NOTICE_KEY] = "settings-saved-nomail"
            else:
                session[FLASH_NOTICE_KEY] = "settings-saved"

            return redirect("/settings", code=303)

        elif part == "password":
            if provider:
                abort(400)

            password = (request.form.get("password") or "").strip()
            confirm = request.form.get("confirm", "")

            # D1: Password validation against guard policy before schema load
            if enforced():
                valid, pass_err = validate_password(password, username=user.name, email=user.email)
                if not valid:
                    errors["password"] = [pass_err]

            data = {"password": password, "confirm": confirm}

            if not errors:
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
            activity.record("account.update", actor=user, team_id=user.team_id, target=f"user:{user.id}", detail={"fields": ["password"]})
            session[FLASH_NOTICE_KEY] = "settings-saved"
            return redirect("/settings", code=303)

        abort(400)
