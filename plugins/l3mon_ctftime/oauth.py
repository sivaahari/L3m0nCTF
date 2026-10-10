"""Login with CTFtime (OAuth2, authorization code): `GET /auth/ctftime` and `GET /auth/ctftime/callback`.

A player who plays under a CTFtime team can sign in with it instead of registering by hand. CTFd's own OAuth flow is built for another
provider and does not fit CTFtime (it sends no `redirect_uri`, fixes the scope, needs an email in the answer, limits the callback to ten
a minute per address, and finds an account by email, which would hand an account to anyone who can put its email on a CTFtime profile),
so this is a small flow of our own with these rules:

- **Off until configured.** Without `CTFTIME_CLIENT_ID` and a secret both routes answer 404, so the platform can be deployed before
  CTFtime approves the event and nothing shows. The client id is the CTFtime event id; the secret is read from `CTFTIME_CLIENT_SECRET` or
  from the file named by `CTFTIME_CLIENT_SECRET_FILE` (a Docker secret), never stored and never logged.
- **State.** 16 random bytes as 32 hex characters (CTFtime refuses longer or odd ones), kept in the session, checked in constant time,
  valid for ten minutes and for one use. Nothing is asked of CTFtime before it matches.
- **Who is who.** An account is found by the CTFtime user id (`Users.oauth_id`) and never by email. A new account needs an email
  address that no other account uses; it starts **unverified**, because CTFtime's email may come from an unverified social account, so
  the platform's own email check still applies. A CTFtime sign-in can never open an administrator's account, a suspended account, or an
  account that registration would refuse (closed registration, the limit of accounts).
- **Studios.** In team mode the CTFtime team becomes a studio (`Teams.oauth_id` is the CTFtime team id, the name is kept exactly, because
  CTFtime's feed finds a team by its name), with the first member as captain. A later member of the same CTFtime team joins it while
  there is room. A name that another studio already has, a full studio or the limit of studios leaves the player without a studio, who then
  sets one up the normal way; nothing is taken over.
- **Failure is quiet and safe.** CTFtime or its Cloudflare answering with an error, a timeout or something that is not what the documented
  answer looks like all end the same way: no account is made, nobody is signed in, the player is told to use the normal sign-in, and the
  log says which step failed (never the code, the token or the secret).
- **Rate.** 120 starts and 120 callbacks a minute per address: a campus can share one.
"""
import hmac
import logging
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import requests
from flask import Blueprint, abort, redirect, request, session, url_for
from sqlalchemy.exc import IntegrityError

from CTFd.cache import clear_team_session, clear_user_session
from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core.text import crew_text
from CTFd.utils import get_config, validators
from CTFd.utils.config.visibility import registration_visible
from CTFd.utils.decorators import ratelimit
from CTFd.utils.logging import log
from CTFd.utils.security.auth import login_user
from CTFd.utils.user import authed

_log = logging.getLogger("l3mon")

API = "https://oauth.ctftime.org"
SCOPE = "profile:read team:read"
STATE_SECONDS = 600
TIMEOUT = (3.05, 8)  # connect, read: CTFtime sits behind Cloudflare and must never hold a worker for long
SESSION_KEY = "l3mon_ctftime"
NAME_LIMIT = 64
BEARER = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")  # the characters a bearer token is made of (RFC 6750): nothing that could end a header line

DENIED = "CTFtime sign-in was cancelled."
STATE = "That CTFtime sign-in could not be checked (it may have taken too long). Please start again."
UNAVAILABLE = "CTFtime sign-in is unavailable right now. Use the normal sign-in instead."
BAD_PROFILE = "CTFtime did not send what we need. Use the normal sign-in instead."
NO_EMAIL = "CTFtime did not share an email address. Register with your email and password instead."
EMAIL_USED = "This email address cannot be used here. Use the normal sign-in or registration instead."
CLOSED = "Registration is closed."
FULL = "The platform has reached its limit of accounts."
REFUSED = "This account cannot sign in with CTFtime."
BANNED = "This account has been suspended."

oauth = Blueprint("l3mon_ctftime_oauth", __name__)


# ---- settings (read at each request, so a change of environment needs no code) ---------------------------------------------------

def client_id() -> str:
    return os.environ.get("CTFTIME_CLIENT_ID", "").strip()


def client_secret() -> str:
    value = os.environ.get("CTFTIME_CLIENT_SECRET", "").strip()
    if value:
        return value
    path = os.environ.get("CTFTIME_CLIENT_SECRET_FILE", "").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""
    return ""


def configured() -> bool:
    return bool(client_id() and client_secret())


def base() -> str:
    """The provider's address: CTFtime's, or (for the tests only, and only on this machine) a stand-in over plain http."""
    value = os.environ.get("CTFTIME_OAUTH_BASE", "").strip().rstrip("/") or API
    parts = urlsplit(value)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")):
        return value
    _log.warning("l3mon: CTFTIME_OAUTH_BASE is neither https nor this machine; CTFtime's own address is used")
    return API


def redirect_uri() -> str:
    explicit = os.environ.get("CTFTIME_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    host = os.environ.get("L3MON_PLATFORM_HOST", "").strip() or request.host
    return f"https://{host}/auth/ctftime/callback"


def safe_next(value):
    """A place on this site to go back to, or None. Only an absolute path: never another site, never a path that a browser reads as one."""
    if not isinstance(value, str) or not value.startswith("/") or value[1:2] in ("/", "\\") or "\\" in value or len(value) > 200:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


# ---- the start ----------------------------------------------------------------------------------------------------------------------

@oauth.route("/auth/ctftime")
@ratelimit(method="GET", limit=120, interval=60)
def start():
    if not configured():
        abort(404)
    wanted = safe_next(request.args.get("next"))
    if authed():
        return redirect(wanted or "/")
    state = secrets.token_hex(16)
    session[SESSION_KEY] = {"state": state, "at": int(time.time()), "next": wanted}
    query = urlencode({"response_type": "code", "client_id": client_id(), "redirect_uri": redirect_uri(), "scope": SCOPE, "state": state})
    response = redirect(f"{base()}/authorize?{query}")
    response.headers["Cache-Control"] = "no-store"
    return response


# ---- the callback -------------------------------------------------------------------------------------------------------------------

class Unavailable(Exception):
    """CTFtime did not answer as documented. The message says which step, for the log: it never holds a code, a token or a secret."""


def _json(response, step):
    if response.status_code != 200:
        raise Unavailable(f"{step} answered {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise Unavailable(f"{step} did not answer with JSON") from None
    if not isinstance(body, dict):
        raise Unavailable(f"{step} did not answer with an object")
    return body


def exchange(code) -> str:
    """The access token for an authorization code."""
    try:
        reply = requests.post(
            f"{base()}/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(), "client_id": client_id(), "client_secret": client_secret()},
            headers={"Accept": "application/json"}, timeout=TIMEOUT, allow_redirects=False,
        )
    except requests.RequestException as error:
        raise Unavailable(f"the token request failed ({type(error).__name__})") from None
    token = _json(reply, "the token request").get("access_token")
    if not isinstance(token, str) or not BEARER.fullmatch(token):
        raise Unavailable("the token answer holds no usable token")
    return token


def fetch_profile(token) -> dict:
    try:
        reply = requests.get(f"{base()}/user", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException as error:
        raise Unavailable(f"the profile request failed ({type(error).__name__})") from None
    return _json(reply, "the profile request")


def _positive_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdigit() and len(value) <= 9:
        value = int(value)
    return value if isinstance(value, int) and 0 < value < 2**31 else None


def parse_profile(data):
    """-> {id, name, email, team: {id, name} or None}, or None when the answer is not what CTFtime documents. Nothing is trusted: an
    email that is not an email address is no email, a team that is not an object is no team."""
    user_id = _positive_id(data.get("id"))
    if user_id is None:
        return None
    name = data.get("name") if isinstance(data.get("name"), str) else ""
    email = data.get("email").strip() if isinstance(data.get("email"), str) else ""
    if not email or len(email) > 128 or validators.validate_email(email) is not True:
        email = ""
    team = None
    if isinstance(data.get("team"), dict):
        team_id, team_name = _positive_id(data["team"].get("id")), data["team"].get("name")
        if team_id is not None and isinstance(team_name, str) and team_name.strip():
            team = {"id": team_id, "name": team_name}
    return {"id": user_id, "name": name, "email": email, "team": team}


def _name(raw, fallback, taken):
    """A name that is plain text and not taken: the CTFtime name as it is when it can be, else the part before an @, else the fallback."""
    candidate = raw.strip()
    if "@" in candidate:
        candidate = candidate.split("@", 1)[0]
    clean, problem = crew_text(candidate, NAME_LIMIT, required=True)
    if problem or not clean:
        clean = fallback
    if not taken(clean):
        return clean
    for number in range(2, 50):
        attempt = f"{clean[: NAME_LIMIT - 4]}-{number}"
        if not taken(attempt):
            return attempt
    return fallback


def _new_user(profile):
    """-> (user, None) or (None, the message for the player)."""
    if not registration_visible():
        return None, CLOSED
    limit = int(get_config("num_users", default=0) or 0)
    if limit and Users.query.filter_by(banned=False, hidden=False).count() >= limit:
        return None, FULL
    if not profile["email"]:
        return None, NO_EMAIL
    if Users.query.filter_by(email=profile["email"]).first() is not None:
        return None, EMAIL_USED
    name = _name(profile["name"], f"ctftime-{profile['id']}", lambda n: Users.query.filter_by(name=n).first() is not None)
    user = Users(name=name, email=profile["email"], oauth_id=profile["id"], verified=False)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:  # another request made it first (the same person, twice at once): the account that exists decides
        db.session.rollback()
        user = Users.query.filter_by(oauth_id=profile["id"]).first()
        return (user, None) if user is not None else (None, EMAIL_USED)
    clear_user_session(user_id=user.id)
    return user, None


def _studio(user, team):
    """Put the player in the studio of their CTFtime team (making it for the first one). Returns a note for the log, or None."""
    existing = Teams.query.filter_by(oauth_id=team["id"]).first()
    if existing is None:
        limit = int(get_config("num_teams", default=0) or 0)
        if limit and Teams.query.filter_by(banned=False, hidden=False).count() >= limit:
            return "the limit of studios is reached"
        name, problem = crew_text(team["name"].strip(), NAME_LIMIT, required=True)
        if problem or not name:
            name = f"ctftime-team-{team['id']}"
        if Teams.query.filter_by(name=name).first() is not None:
            return "another studio already has that name"
        existing = Teams(name=name, oauth_id=team["id"], captain_id=user.id)
        db.session.add(existing)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = Teams.query.filter_by(oauth_id=team["id"]).first()
            if existing is None:
                return "the studio could not be made"
        clear_team_session(team_id=existing.id)
    if existing.banned:
        return "that studio is suspended"
    size = int(get_config("team_size", default=0) or 0)
    if user not in existing.members:
        if size and len(existing.members) >= size:
            return "that studio is full"
        existing.members.append(user)
        db.session.commit()
        clear_user_session(user_id=user.id)
        clear_team_session(team_id=existing.id)
    return None


def _fail(message, note):
    from CTFd.utils.helpers import error_for  # (CTFd.utils.helpers needs an application at import time, so it is not imported with the module)

    log("logins", "[{date}] {ip} - CTFtime sign-in refused: {note}", note=note)
    error_for(endpoint="auth.login", message=message)
    return redirect(url_for("auth.login"))


@oauth.route("/auth/ctftime/callback")
@ratelimit(method="GET", limit=120, interval=60)
def callback():
    if not configured():
        abort(404)
    kept = session.pop(SESSION_KEY, None)
    given = request.args.get("state", "")
    if not isinstance(kept, dict) or not isinstance(given, str) or not isinstance(kept.get("state"), str):
        return _fail(STATE, "no sign-in was started")
    if not hmac.compare_digest(kept["state"].encode(), given.encode()) or int(time.time()) - int(kept.get("at", 0)) > STATE_SECONDS:
        return _fail(STATE, "the state did not match or was too old")
    if request.args.get("error"):
        return _fail(DENIED, "the player cancelled at CTFtime")
    code = request.args.get("code", "")
    if not code or len(code) > 1024:
        return _fail(STATE, "no code came back")
    try:
        profile = parse_profile(fetch_profile(exchange(code)))
    except Unavailable as problem:
        _log.warning("l3mon: CTFtime sign-in is unavailable: %s", problem)
        return _fail(UNAVAILABLE, str(problem))
    if profile is None:
        _log.warning("l3mon: CTFtime sign-in: the profile is not what CTFtime documents")
        return _fail(BAD_PROFILE, "the profile was not usable")

    user = Users.query.filter_by(oauth_id=profile["id"]).first()
    if user is None:
        user, message = _new_user(profile)
        if user is None:
            return _fail(message, "no account was made")
    if user.type == "admin":
        return _fail(REFUSED, "an administrator's account is never opened this way")
    if user.banned:
        return _fail(BANNED, "the account is suspended")
    if get_config("user_mode") == "teams" and user.team_id is None and profile["team"] is not None:
        note = _studio(user, profile["team"])
        if note:
            log("logins", "[{date}] {ip} - CTFtime sign-in: no studio for the player ({note})", note=note)

    session.regenerate()  # a new session id, so nothing a visitor held before can be the signed-in one
    login_user(user)
    log("logins", "[{date}] {ip} - {name} signed in with CTFtime", name=user.name)
    return redirect(safe_next(kept.get("next")) or "/")

