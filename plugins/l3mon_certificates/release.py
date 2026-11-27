"""When players see their certificates, the one notification, and the card on a player's studio page.

The state is three settings in CTFd's own table (so it is in every backup):

    l3mon_certificates_release_at   the moment the certificates show (epoch seconds), or absent: hidden
    l3mon_certificates_announced    the moment the one notification was sent, or absent: not sent yet
    l3mon_certificates_base_url     the portal's address the codes were exported for (set by the import)

Released means: a release moment is set and it has come. Nothing has to run at that moment: every page works it out when it is drawn. The
notification is the one thing that is sent: the first ordinary request after the moment sends it, once, behind the plan's lock (two workers
cannot both send it), as a [TOAST] line (a short pop-up and the bell, never the blocking announcement). It is sent only once EVER: hiding
the certificates again and releasing them later sends no second one (the crew page says so before it is pressed).
"""
import functools
import logging

from flask import current_app, request

from CTFd.models import Notifications, db
from CTFd.plugins.l3mon_certificates import codes
from CTFd.plugins.l3mon_certificates.models import Certificate
from CTFd.plugins.l3mon_certificates.qr import svg
from CTFd.plugins.l3mon_core.clock import now
from CTFd.utils import _get_config, get_config, set_config

KEY_AT = "l3mon_certificates_release_at"
KEY_ANNOUNCED = "l3mon_certificates_announced"
KEY_BASE_URL = "l3mon_certificates_base_url"
EVENT = "L3m0n CTF 2026 Qualifiers"
TITLE = "[TOAST] Certificates are ready"
CONTENT = ("The certificates of the L3m0n CTF 2026 Qualifiers are out. If your studio scored, open Your studio: your own verification code "
           "and a link to your certificate are at the top of the page.")

_log = logging.getLogger("l3mon.certificates")
_SKIP_PREFIXES = ("/themes/", "/plugins/", "/files/", "/api/v1/l3mon/ticks")


def _epoch(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _fresh(key):
    """A setting read from the database itself, past CTFd's cache (for the decisions taken behind the lock)."""
    value = _get_config.__wrapped__(key)
    return None if value is KeyError else value


def release_at():
    return _epoch(get_config(KEY_AT))


def announced_at():
    return _epoch(get_config(KEY_ANNOUNCED))


def released(t=None) -> bool:
    at = release_at()
    return at is not None and (now() if t is None else t) >= at


def base_url() -> str:
    value = str(get_config(KEY_BASE_URL) or "")
    return value if codes.BASE_URL_RE.match(value) else codes.DEFAULT_BASE_URL


def count() -> int:
    return db.session.query(Certificate.id).count()


# ---- the one notification ------------------------------------------------------------------------------------------------------------

def announce(t=None):
    """Send the notification if the certificates are released and it was never sent. Returns the notification, or None."""
    t = int(now() if t is None else t)  # whole seconds: stored as a setting below
    if not released(t) or announced_at() is not None:
        return None  # the cheap answer of almost every request (CTFd's settings are cached)
    from CTFd.plugins.l3mon_core.locks import serialize

    serialize()  # the plan's lock: one worker decides, the others wait and then see that it was sent
    if _epoch(_fresh(KEY_ANNOUNCED)) is not None or _epoch(_fresh(KEY_AT)) is None or t < _epoch(_fresh(KEY_AT)) or count() == 0:
        # count() == 0: a release set for later whose codes were then removed; the one notification is kept for when codes are loaded
        db.session.commit()  # ends the locked transaction
        return None
    note = Notifications(title=TITLE, content=CONTENT)
    db.session.add(note)
    set_config(KEY_ANNOUNCED, str(t))  # commits, in the same transaction as the notification
    db.session.commit()
    try:
        from CTFd.schemas.notifications import NotificationSchema

        data = NotificationSchema().dump(note).data
        data["type"] = "toast"
        current_app.events_manager.publish(data=data, type="notification")
    except Exception:  # noqa: BLE001  (the line is in the bell already; a live push that fails only means it shows at the next look)
        _log.warning("l3mon: the certificates' notification is saved but could not be pushed live", exc_info=True)
    return note


RETRY_AFTER = 30  # seconds: a notification that failed is tried again by this worker at most this often (each try takes the plan's lock)
_failed_at = None


def _on_request():
    global _failed_at
    path = request.path
    if request.method == "OPTIONS" or path.startswith(_SKIP_PREFIXES):
        return None
    t = now()
    if _failed_at is not None and t - _failed_at < RETRY_AFTER:
        return None
    try:
        announce()
        _failed_at = None
    except Exception:  # noqa: BLE001  (a page must never fail because the notification could not be sent; a later request tries again)
        _failed_at = t
        db.session.rollback()
        _log.warning("l3mon: the certificates' notification could not be sent", exc_info=True)
    return None


def install(app):
    if "l3mon_certificates_release" not in app.extensions:
        app.extensions["l3mon_certificates_release"] = True
        app.before_request(_on_request)


# ---- the card ------------------------------------------------------------------------------------------------------------------------

QR_PATH = "/api/v1/l3mon/certificate/qr.svg"  # the card's picture: the signed-in player's own QR code (api.py), never inside the page


@functools.lru_cache(maxsize=4096)
def _qr_svg(url: str) -> str:
    return svg(url, dark="#000000", light="#ffffff", label="QR code of your certificate's address")


def _own(user):
    """The player's own certificate row and its address, or (None, None): not released, no code, or not a player."""
    if user is None or getattr(user, "id", None) is None or not released():
        return None, None
    row = Certificate.query.filter_by(user_id=user.id).first()
    if row is None or not codes.CODE_RE.match(row.code or ""):
        return None, None
    return row, f"{base_url()}/{row.code}"


def card_for(user):
    """What the studio page shows this player: {code, url, site, event, qr}, or None (not released, no code, or not a player).

    `qr` is the address of the picture, not the picture: a QR code drawn inline put the studio page over its size budget (independent review of
    27 Nov 2026), and a separate image also needs no `data:` images in a future Content-Security-Policy."""
    row, url = _own(user)
    if row is None:
        return None
    base = base_url()
    return {"code": row.code, "url": url, "site": base[len("https://"):], "event": EVENT, "qr": QR_PATH}


def qr_svg_for(user):
    """The SVG of this player's own QR code, or None (the same rule as the card: released, and a code of their own)."""
    row, url = _own(user)
    return None if row is None else _qr_svg(url)
