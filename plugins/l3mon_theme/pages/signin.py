"""Sign-in integration for Google OAuth, reset message mapping, and theme values (SP6 Stream C)."""
import logging
from flask import redirect, url_for
from CTFd.plugins.l3mon_guard.policies.password import (
    V_MSG_PASS_EMPTY,
    V_MSG_PASS_SHORT,
    V_MSG_PASS_LONG,
    V_MSG_PASS_COMMON,
    V_MSG_PASS_USER,
    V_MSG_PASS_EMAIL,
)
from CTFd.plugins.l3mon_theme.registry import value

logger = logging.getLogger(__name__)

class _ResetMessageTargets(dict):
    """Mapping of reset error messages to target field ('password', 'email', or None for summary)."""

    def get(self, key, default=None):
        if key in self:
            return super().get(key, default)
        if isinstance(key, str) and key.startswith("Password must be at least ") and key.endswith(" characters"):
            return "password"
        return default

    def __getitem__(self, key):
        if key in self:
            return super().__getitem__(key)
        if isinstance(key, str) and key.startswith("Password must be at least ") and key.endswith(" characters"):
            return "password"
        raise KeyError(key)

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        if isinstance(key, str) and key.startswith("Password must be at least ") and key.endswith(" characters"):
            return True
        return False


# Exact error messages that can occur on the reset password flow, mapped to:
# - 'password': password field error (f-password)
# - 'email': email field error (f-email)
# - None: summary only (no field attachment)
# Any message not in this table also defaults to summary only.
RESET_MESSAGE_TARGETS = _ResetMessageTargets({
    # Guard password policy messages (password field)
    V_MSG_PASS_EMPTY: "password",
    V_MSG_PASS_SHORT: "password",
    V_MSG_PASS_LONG: "password",
    V_MSG_PASS_COMMON: "password",
    V_MSG_PASS_USER: "password",
    V_MSG_PASS_EMAIL: "password",
    # Guard Turnstile CAPTCHA messages (summary only)
    "Please complete the security check.": None,
    "Security check failed. Please try again.": None,
    # CTFd core auth.py reset_password messages (summary only)
    "This CTF is not configured to send email.<br> Please contact an organizer to have your password reset.": None,
    "Your reset link is invalid, please generate a new one": None,
    "Your account was registered via an authentication provider and does not have an associated password. Please login via your authentication provider.": None,
    # CTFd core auth.py reset_password messages (password field)
    "Please pick a longer password": "password",
})


@value("reset_message_targets", default={})
def reset_message_targets():
    return RESET_MESSAGE_TARGETS


@value("google_on", default=False)
def google_on():
    """True when l3mon_auth has Google OAuth configured."""
    try:
        from CTFd.plugins.l3mon_auth.oauth import configured
        return configured()
    except (ImportError, AttributeError, Exception) as exc:
        logger.debug("Google OAuth is not configured: %s", exc)
        return False


def install(app):
    """Register GET-only /reset and /reset/<data> redirect routes so no alias rate-limit bucket exists."""
    existing = {rule.rule for rule in app.url_map.iter_rules()}
    if "/reset" not in existing:
        app.add_url_rule(
            "/reset",
            endpoint="l3mon_theme_reset_redirect",
            view_func=lambda: redirect(url_for("auth.reset_password"), 302),
            methods=["GET"],
        )
    if "/reset/<data>" not in existing:
        app.add_url_rule(
            "/reset/<data>",
            endpoint="l3mon_theme_reset_data_redirect",
            view_func=lambda data: redirect(url_for("auth.reset_password", data=data), 302),
            methods=["GET"],
        )
