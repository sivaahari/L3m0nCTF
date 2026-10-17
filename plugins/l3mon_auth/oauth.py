"""Continue with Google (OpenID Connect with PKCE): `GET /auth/google` and `GET /auth/google/callback` (SP8 B5).

Rules:
- Off until configured: 404 when GOOGLE_CLIENT_ID, secret or L3MON_PLATFORM_HOST are missing.
- Authorization code flow with PKCE (S256), state (16 bytes hex), and nonce (16 bytes hex).
- Redirect URI is fixed, built from platform host: https://<platform_host>/auth/google/callback.
- RS256 ID token verification using standard cryptography library.
- Account mapping strictly by (provider, subject) in l3mon_identity, NEVER by email.
- New accounts created only if no other account uses the normalized email address.
- Created accounts are marked verified=True.
- Never links to existing email/password account (refuses with plain sentence).
- Admin accounts and banned accounts refused.
- Fail-soft on network or token error: generic plain sentence, no session, log step.
- Generous rate limiting per address (120/min).
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import unicodedata
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, abort, redirect, request, session, url_for
import requests
from sqlalchemy.exc import IntegrityError

from CTFd.cache import clear_user_session
from CTFd.models import Users, db
from CTFd.plugins.l3mon_auth.jwt import TokenError, verify_id_token
from CTFd.plugins.l3mon_auth.models import Identity
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_core.text import crew_text
from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used, normalize_email
from CTFd.utils import get_config, validators
from CTFd.utils.config.visibility import registration_visible
from CTFd.utils.decorators import ratelimit
from CTFd.utils.email import check_email_is_blacklisted, check_email_is_whitelisted
from CTFd.utils.logging import log
from CTFd.utils.security.auth import login_user
from CTFd.utils.user import authed

_log = logging.getLogger("l3mon.auth")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPE = "openid email profile"
STATE_SECONDS = 600
TIMEOUT = (2.0, 4.0)
SESSION_KEY = "l3mon_auth_google"
NAME_LIMIT = 64
BLANKS = set("\u3164\u115f\u1160\uffa0\u2800\u034f")

DENIED = "Google sign-in was cancelled."
STATE_ERR = "That Google sign-in could not be checked (it may have taken too long). Please start again."
UNAVAILABLE = "Google sign-in is unavailable right now. Use the normal sign-in instead."
BAD_TOKEN = "Google did not send a usable credential. Use the normal sign-in instead."
NO_EMAIL = "Google did not share an email address. Register with your email and password instead."
EMAIL_EXISTS = "This email address cannot be used here. Sign in with your email and password instead."
CLOSED = "Registration is closed."
NEEDS_CODE = "Registration needs a code. Register with your email and password instead."
NO_NAME = "No free name could be found for this account. Register with your email and password instead."
FULL = "The platform has reached its limit of accounts."
REFUSED = "This account cannot sign in with Google."
BANNED = "This account has been suspended."

oauth = Blueprint("l3mon_auth_google", __name__)


def client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def client_secret() -> str:
    val = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if val:
        return val
    path = os.environ.get("GOOGLE_CLIENT_SECRET_FILE", "/run/secrets/GOOGLE_CLIENT_SECRET").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    return ""


def platform_host() -> str:
    return os.environ.get("L3MON_PLATFORM_HOST", "").strip() or request.host


def configured() -> bool:
    return bool(client_id() and client_secret() and platform_host())


def is_active() -> bool:
    return configured()


def redirect_uri() -> str:
    host = platform_host()
    return f"https://{host}/auth/google/callback"


def _auth_url() -> str:
    if os.environ.get("L3MON_AUTH_TEST_MODE", "").strip() == "1":
        custom = os.environ.get("GOOGLE_AUTH_URL", "").strip()
        if custom:
            return custom
        base = os.environ.get("GOOGLE_OAUTH_BASE", "").strip().rstrip("/")
        if base:
            return f"{base}/o/oauth2/v2/auth"
    return GOOGLE_AUTH_URL


def _token_url() -> str:
    if os.environ.get("L3MON_AUTH_TEST_MODE", "").strip() == "1":
        custom = os.environ.get("GOOGLE_TOKEN_URL", "").strip()
        if custom:
            return custom
        base = os.environ.get("GOOGLE_OAUTH_BASE", "").strip().rstrip("/")
        if base:
            return f"{base}/token"
    return GOOGLE_TOKEN_URL


def safe_next(value):
    """Validate relative redirect path."""
    if not isinstance(value, str) or not value.startswith("/") or value[1:2] in ("/", "\\") or "\\" in value or len(value) > 200:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _readable(text: str) -> bool:
    if any(char in BLANKS for char in text) or any(unicodedata.category(char) == "Cf" and char not in "\u200c\u200d" for char in text):
        return False
    return any(char.isalnum() for char in text)


def _plain(raw: str) -> str | None:
    clean, problem = crew_text(raw.strip(), NAME_LIMIT, required=True)
    return clean if not problem and clean and _readable(clean) else None


def _format_unique_name(raw: str, sub: str) -> str | None:
    candidate = raw.strip()
    if "@" in candidate:
        candidate = candidate.split("@", 1)[0]
    fallback = f"google-{sub[:12]}"

    for base in dict.fromkeys([_plain(candidate) or fallback, fallback]):
        for attempt in [base] + [f"{base[:NAME_LIMIT - 5]}-{num}" for num in range(2, 50)]:
            if not Users.query.filter_by(name=attempt).first():
                return attempt
    return None


def _fail(message: str, note: str):
    from CTFd.utils.helpers import error_for
    log("logins", "[{date}] {ip} - Google sign-in refused: {note}", note=note)
    error_for(endpoint="auth.login", message=message)
    return redirect(url_for("auth.login"))


@oauth.route("/auth/google")
@ratelimit(method="GET", limit=120, interval=60)
@ratelimit(method="HEAD", limit=120, interval=60)
def start():
    if not configured():
        abort(404)

    wanted = safe_next(request.args.get("next"))
    if authed():
        return redirect(wanted or "/")

    state = secrets.token_hex(16)
    nonce = secrets.token_hex(16)
    verifier = secrets.token_urlsafe(32)

    # Compute code_challenge = base64url(sha256(verifier)) without padding
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    session[SESSION_KEY] = {
        "state": state,
        "nonce": nonce,
        "code_verifier": verifier,
        "at": int(now()),
        "next": wanted,
    }

    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{_auth_url()}?{urlencode(params)}"
    response = redirect(url)
    response.headers["Cache-Control"] = "no-store"
    return response


def _check_email_collision(email: str) -> bool:
    """Check if normalized email already belongs to any registered user."""
    norm_incoming, _ = normalize_email(email)
    for u in Users.query.all():
        if u.email:
            norm_existing, _ = normalize_email(u.email)
            if norm_incoming == norm_existing:
                return True
    return False


def _create_google_user(sub: str, email: str, name_hint: str) -> tuple[Users | None, str | None]:
    if not registration_visible():
        return None, CLOSED
    if get_config("registration_code"):
        return None, NEEDS_CODE

    limit = int(get_config("num_users", default=0) or 0)
    if limit and Users.query.filter_by(banned=False, hidden=False).count() >= limit:
        return None, FULL

    if not email or validators.validate_email(email) is not True:
        return None, NO_EMAIL

    if check_email_is_whitelisted(email) is False or check_email_is_blacklisted(email) is True:
        return None, EMAIL_EXISTS

    if _check_email_collision(email):
        return None, EMAIL_EXISTS

    name = _format_unique_name(name_hint, sub)
    if not name:
        return None, NO_NAME

    user = Users(name=name, email=email, verified=True)
    db.session.add(user)
    try:
        db.session.flush()
        identity = Identity(provider="google", subject=sub, user_id=user.id)
        db.session.add(identity)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Concurrent registration or race
        ident = Identity.query.filter_by(provider="google", subject=sub).first()
        if ident:
            return Users.query.filter_by(id=ident.user_id).first(), None
        return None, EMAIL_EXISTS

    clear_user_session(user_id=user.id)
    return user, None


@oauth.route("/auth/google/callback")
@ratelimit(method="GET", limit=120, interval=60)
@ratelimit(method="HEAD", limit=120, interval=60)
def callback():
    if not configured():
        abort(404)

    if authed():
        session.pop(SESSION_KEY, None)
        return redirect("/")

    kept = session.pop(SESSION_KEY, None)
    given_state = request.args.get("state", "")

    if not isinstance(kept, dict) or not isinstance(given_state, str) or not isinstance(kept.get("state"), str):
        return _fail(STATE_ERR, "no sign-in was started")

    if not hmac.compare_digest(kept["state"].encode(), given_state.encode()):
        return _fail(STATE_ERR, "state mismatch")

    if int(now()) - int(kept.get("at", 0)) > STATE_SECONDS:
        return _fail(STATE_ERR, "state expired")

    if request.args.get("error"):
        return _fail(DENIED, "player cancelled at Google")

    code = request.args.get("code", "")
    if not code or len(code) > 1024:
        return _fail(STATE_ERR, "missing or invalid code")

    token_url = _token_url()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": client_id(),
        "client_secret": client_secret(),
        "code_verifier": kept["code_verifier"],
    }

    try:
        resp = requests.post(
            token_url,
            data=payload,
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
            allow_redirects=False,
            verify=True,
        )
        if resp.status_code != 200:
            _log.warning("l3mon_auth: token endpoint HTTP %d: %s", resp.status_code, resp.text[:200])
            return _fail(UNAVAILABLE, f"token endpoint HTTP {resp.status_code}")
        body = resp.json()
        if not isinstance(body, dict):
            return _fail(UNAVAILABLE, "token response not an object")
    except Exception as exc:
        _log.warning("l3mon_auth: token exchange failed: %s", exc)
        return _fail(UNAVAILABLE, str(exc))

    id_token = body.get("id_token")
    if not id_token or not isinstance(id_token, str):
        return _fail(BAD_TOKEN, "missing id_token in token response")

    try:
        claims = verify_id_token(id_token, client_id(), kept["nonce"])
    except TokenError as exc:
        _log.warning("l3mon_auth: ID token verification failed: %s", exc)
        return _fail(BAD_TOKEN, str(exc))

    sub = claims["sub"]
    email = claims["email"]
    name_hint = claims.get("name", "")

    # Look up existing identity
    ident = Identity.query.filter_by(provider="google", subject=sub).first()
    if ident:
        user = Users.query.filter_by(id=ident.user_id).first()
        if not user:
            return _fail(UNAVAILABLE, "dangling identity without user")
    else:
        # Create new account
        user, err_msg = _create_google_user(sub, email, name_hint)
        if not user:
            return _fail(err_msg or UNAVAILABLE, err_msg or "failed to create user")

    if user.type == "admin":
        return _fail(REFUSED, "administrator account cannot sign in via Google")

    if user.banned:
        return _fail(BANNED, "account is suspended")

    session.regenerate()
    login_user(user)
    log("logins", "[{date}] {ip} - {name} signed in with Google", name=user.name)

    return redirect(safe_next(kept.get("next")) or "/")
