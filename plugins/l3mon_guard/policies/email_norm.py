"""Email normalization and disguised address detection (SP5 B4.3).

Normalizes emails by:
- Rejecting zero-width and bidirectional control characters
- NFKC Unicode normalization
- Lowercase
- Stripping '+' tags (e.g., user+tag@example.com -> user@example.com)
- Removing dots for gmail.com and googlemail.com, and treating googlemail.com as gmail.com
- Comparing against existing accounts in normalized form
"""
import re
import unicodedata

# Zero-width and bidirectional control characters to refuse
BIDI_ZERO_WIDTH = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\xad\u061c\U000e0000-\U000e007f]"
)


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


def is_email_disguise_used(email: str, exclude_user_id: int | None = None) -> bool:
    """Check if any existing registered account has the same normalized email form."""
    target_norm, err = normalize_email(email)
    if not target_norm:
        return True

    from CTFd.models import Users, db

    query = db.session.query(Users.id, Users.email).filter(Users.email.isnot(None))
    if exclude_user_id is not None:
        query = query.filter(Users.id != exclude_user_id)

    # Compare against normalized form of all existing users
    for uid, existing_email in query.all():
        if not existing_email:
            continue
        cand_norm, _ = normalize_email(existing_email)
        if cand_norm == target_norm:
            return True

    return False
