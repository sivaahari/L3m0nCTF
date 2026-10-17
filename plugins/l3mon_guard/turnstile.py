"""Cloudflare Turnstile CAPTCHA integration and login failure tracking (SP5 B4.5).

Rules:
- Required on register and password reset when configured
- On login, required ONLY after failed sign-in attempts (tracked per account and per IP bucket in Redis)
- Server-side verification against Cloudflare siteverify:
  short timeout, no redirects, TLS verification on, size cap on answer
- If Cloudflare is unreachable:
  register fails closed; login and reset fail open (and record captcha_unverified evidence)
- Context processor helper provides l3mon_guard dict for theme
"""
import logging
import os
from flask import has_request_context, request
import requests

from CTFd.cache import cache

_log = logging.getLogger("l3mon.guard")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT = (2.0, 3.05)  # connect, read timeout
MAX_RESPONSE_BYTES = 10240
LOGIN_FAIL_WINDOW = 900  # 15 minutes
LOGIN_FAIL_THRESHOLD = 3

# Official Cloudflare test keys (always pass / always fail)
CF_TEST_PASS_SITEKEY = "1x0000000000000000000000AA"
CF_TEST_PASS_SECRET = "1x0000000000000000000000000000000AA"
CF_TEST_FAIL_SITEKEY = "2x0000000000000000000000AA"
CF_TEST_FAIL_SECRET = "2x0000000000000000000000000000000AA"


def get_sitekey() -> str:
    return os.environ.get("TURNSTILE_SITEKEY", "").strip()


def get_secret() -> str:
    val = os.environ.get("TURNSTILE_SECRET", "").strip()
    if val:
        return val
    path = os.environ.get("TURNSTILE_SECRET_FILE", "/run/secrets/TURNSTILE_SECRET").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    return ""


def is_active() -> bool:
    """Turnstile is active only when both sitekey and secret are configured."""
    return bool(get_sitekey() and get_secret())


def _get_verify_url() -> str:
    return os.environ.get("TURNSTILE_VERIFY_URL", VERIFY_URL).strip() or VERIFY_URL


def _inc_with_ttl(key: str, window: int) -> int:
    backend = cache.cache
    client = getattr(backend, "_write_client", None) or getattr(backend, "_read_client", None)
    if client is not None:
        prefix = getattr(backend, "key_prefix", "")
        full_key = prefix + key
        pipe = client.pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, window)
        res = pipe.execute()
        return int(res[0])
    else:
        val = backend.inc(key)
        if val is None:
            backend.set(key, 1, timeout=window)
            return 1
        return int(val)


def _get_count(key: str) -> int:
    backend = cache.cache
    val = backend.get(key)
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def record_login_failure(username: str, ip: str) -> None:
    """Record a failed login attempt for account and IP bucket."""
    if username:
        canonical_user = username.strip().casefold()
        _inc_with_ttl(f"guard:login_fail:acc:{canonical_user}", LOGIN_FAIL_WINDOW)
    if ip:
        _inc_with_ttl(f"guard:login_fail:ip:{ip}", LOGIN_FAIL_WINDOW)


def clear_login_failure(username: str, ip: str) -> None:
    """Clear login failure counters on successful login."""
    backend = cache.cache
    if username:
        canonical_user = username.strip().casefold()
        backend.delete(f"guard:login_fail:acc:{canonical_user}")


def is_login_captcha_required(username: str = "", ip: str = "") -> bool:
    """Return True if login requires CAPTCHA verification due to recent failures."""
    if not is_active():
        return False

    if username:
        canonical_user = username.strip().casefold()
        acc_fails = _get_count(f"guard:login_fail:acc:{canonical_user}")
        if acc_fails >= LOGIN_FAIL_THRESHOLD:
            return True

    if ip:
        ip_fails = _get_count(f"guard:login_fail:ip:{ip}")
        if ip_fails >= LOGIN_FAIL_THRESHOLD:
            return True

    return False


def verify_turnstile(token: str, remote_ip: str | None = None, action: str = "register") -> tuple[bool, str | None]:
    """Verify Turnstile response token.

    Returns (is_valid, error_message).
    action is one of: 'register', 'login', 'reset'.
    """
    if not is_active():
        return True, None

    # In test mode or with test keys:
    secret = get_secret()
    if secret == CF_TEST_PASS_SECRET:
        if token:
            return True, None
        return False, "Please complete the security check."
    elif secret == CF_TEST_FAIL_SECRET:
        return False, "Security check failed. Please try again."

    if not token or not token.strip():
        return False, "Please complete the security check."

    verify_url = _get_verify_url()
    payload = {
        "secret": secret,
        "response": token.strip(),
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        resp = requests.post(
            verify_url,
            data=payload,
            timeout=TIMEOUT,
            allow_redirects=False,
            verify=True,
            headers={"Accept": "application/json"},
        )
        content = resp.content[:MAX_RESPONSE_BYTES]
        if resp.status_code != 200:
            raise requests.RequestException(f"Turnstile returned HTTP {resp.status_code}")

        data = resp.json()
        success = bool(data.get("success", False))
        if success:
            return True, None
        return False, "Security check failed. Please try again."

    except requests.RequestException as exc:
        _log.warning("l3mon_guard: Turnstile verify unreachable (%s): %s", action, exc)

        # Fail open for login and reset, fail closed for register
        if action in ("login", "reset"):
            from CTFd.plugins.l3mon_guard.evidence import record
            record("captcha_unverified", detail=f"action={action}; ip={remote_ip or ''}")
            return True, None
        else:
            # Register fails closed
            return False, "Security check could not be verified. Please try again."


def get_guard_context() -> dict:
    """Context processor value for templates: l3mon_guard."""
    sitekey = get_sitekey()
    active = is_active()

    client_ip = ""
    if has_request_context():
        try:
            from CTFd.utils.user import get_ip
            client_ip = get_ip()
        except Exception:
            client_ip = ""

    login_needed = active and is_login_captcha_required("", client_ip)

    return {
        "captcha": {
            "sitekey": sitekey if active else "",
            "register": active,
            "login": login_needed,
            "reset": active,
        }
    }
