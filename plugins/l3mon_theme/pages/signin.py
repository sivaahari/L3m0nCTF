"""Sign-in integration for Google OAuth and theme values (SP6 Stream C)."""
import logging
from flask import redirect, url_for
from CTFd.plugins.l3mon_theme.registry import value

logger = logging.getLogger(__name__)


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
