"""Email normalization and disguised address detection (SP5 B4.3, F1).

Normalizes emails by:
- Rejecting zero-width and bidirectional control characters
- NFKC Unicode normalization
- Lowercase
- Stripping '+' tags (e.g., user+tag@example.com -> user@example.com)
- Removing dots for gmail.com and googlemail.com, and treating googlemail.com as gmail.com
- Comparing against existing accounts in normalized form via indexed l3mon_email_norm table
"""
import logging
import re
import unicodedata

_log = logging.getLogger("l3mon.guard")

# Zero-width and bidirectional control characters to refuse
BIDI_ZERO_WIDTH = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\xad\u061c\U000e0000-\U000e007f]"
)

_LISTENERS_REGISTERED = False


def normalize_email(email: str) -> tuple[str | None, str | None]:
    """Normalize an email address.

    Returns (normalized_email, error_message).
    If invalid or contains disallowed control characters, returns (None, error_message).
    """
    if not email or not isinstance(email, str):
        return None, "Email address is required."

    raw = email.strip()
    if BIDI_ZERO_WIDTH.search(raw):
        return None, "Email address contains invalid control characters."

    # NFKC normalisation
    norm = unicodedata.normalize("NFKC", raw)
    if BIDI_ZERO_WIDTH.search(norm):
        return None, "Email address contains invalid control characters."

    if norm.count("@") != 1:
        return None, "Please provide a valid email address."

    user, domain = norm.split("@", 1)
    user = user.strip().lower()
    domain = domain.strip().lower()

    if not user or not domain:
        return None, "Please provide a valid email address."

    # Remove part after '+'
    user = user.split("+", 1)[0]

    # For Gmail / Googlemail: dots removed, googlemail.com -> gmail.com
    if domain in ("gmail.com", "googlemail.com"):
        domain = "gmail.com"
        user = user.replace(".", "")

    if not user:
        return None, "Please provide a valid email address."

    return f"{user}@{domain}", None


def register_email_norm_listeners() -> None:
    """Register SQLAlchemy lifecycle listeners on Users to sync l3mon_email_norm."""
    global _LISTENERS_REGISTERED
    if _LISTENERS_REGISTERED:
        return
    _LISTENERS_REGISTERED = True

    from sqlalchemy import event
    from CTFd.models import Users
    from CTFd.plugins.l3mon_guard.models import EmailNorm

    @event.listens_for(Users, "after_insert")
    def _on_user_insert(mapper, connection, target):
        try:
            email = getattr(target, "email", None)
            if not email:
                return
            norm, _ = normalize_email(email)
            uid = getattr(target, "id", None)
            if not norm or not uid:
                return
            connection.execute(
                EmailNorm.__table__.insert().values(user_id=uid, norm=norm)
            )
        except Exception as exc:
            _log.warning("l3mon_guard: failed to insert email norm for user %s: %s", getattr(target, "id", None), exc)

    @event.listens_for(Users, "after_update")
    def _on_user_update(mapper, connection, target):
        try:
            email = getattr(target, "email", None)
            uid = getattr(target, "id", None)
            if not email or not uid:
                return
            norm, _ = normalize_email(email)
            if not norm:
                return
            res = connection.execute(
                EmailNorm.__table__.update().where(
                    EmailNorm.__table__.c.user_id == uid
                ).values(norm=norm)
            )
            if res.rowcount == 0:
                connection.execute(
                    EmailNorm.__table__.insert().values(user_id=uid, norm=norm)
                )
        except Exception as exc:
            _log.warning("l3mon_guard: failed to update email norm for user %s: %s", getattr(target, "id", None), exc)

    @event.listens_for(Users, "after_delete")
    def _on_user_delete(mapper, connection, target):
        try:
            uid = getattr(target, "id", None)
            if uid:
                connection.execute(
                    EmailNorm.__table__.delete().where(EmailNorm.__table__.c.user_id == uid)
                )
        except Exception as exc:
            _log.warning("l3mon_guard: failed to delete email norm for user %s: %s", getattr(target, "id", None), exc)


def backfill_email_norm(batch_size: int = 500) -> int:
    """Lazily backfill missing email normalizations in batches."""
    from CTFd.models import Users, db
    from CTFd.plugins.l3mon_core.claim import claim
    from CTFd.plugins.l3mon_guard.models import EmailNorm

    if not claim("l3mon_email_norm_backfill", 10.0):
        return 0

    try:
        missing_users = (
            db.session.query(Users.id, Users.email)
            .outerjoin(EmailNorm, Users.id == EmailNorm.user_id)
            .filter(EmailNorm.user_id.is_(None))
            .filter(Users.email.isnot(None), Users.email != "")
            .limit(batch_size)
            .all()
        )
        if not missing_users:
            return 0

        inserted = 0
        for uid, raw_email in missing_users:
            norm, _ = normalize_email(raw_email)
            if not norm:
                continue
            existing = EmailNorm.query.filter(
                (EmailNorm.user_id == uid) | (EmailNorm.norm == norm)
            ).first()
            if not existing:
                row = EmailNorm(user_id=uid, norm=norm)
                db.session.add(row)
                inserted += 1

        if inserted:
            db.session.commit()
        return inserted
    except Exception as exc:
        db.session.rollback()
        _log.warning("l3mon_guard: lazy backfill failed: %s", exc)
        return 0


def is_email_disguise_used(email: str, exclude_user_id: int | None = None) -> bool:
    """Check if any existing registered account has the same normalized email form.
    
    Uses indexed l3mon_email_norm table for O(1) performance (SP5 F1).
    """
    target_norm, err = normalize_email(email)
    if not target_norm:
        return True

    from CTFd.plugins.l3mon_guard.models import EmailNorm

    try:
        backfill_email_norm()
    except Exception as exc:
        _log.warning("l3mon_guard: backfill failed: %s", exc)

    query = EmailNorm.query.filter_by(norm=target_norm)
    if exclude_user_id is not None:
        query = query.filter(EmailNorm.user_id != exclude_user_id)

    return query.first() is not None


# Auto-register listeners on module import
try:
    register_email_norm_listeners()
except Exception as _e:
    _log.warning("l3mon_guard: could not register listeners on import: %s", _e)
