"""Team name policy (SP5 B4.4).

Enforces:
- Removal of zero-width and bidirectional control characters
- Collapsing of confusable whitespace
- Case-insensitive comparison via Unicode NFKC + case folding
- Length limits (1 to 64 chars)
- Prohibition of organizational impersonation words (whole-word match)
- Crew / admin exemption
- Collision prevention against existing teams
"""
import re
import unicodedata

# Zero-width, bidi, and invisible control characters
BIDI_ZERO_WIDTH = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\xad\u061c\U000e0000-\U000e007f\u3164\u115f\u1160\uffa0\u2800\u034f]"
)

# Collapsible whitespace (including unicode spaces)
WHITESPACE = re.compile(r"[\s\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]+")

# Reserved words indicating organizational impersonation (matched as whole words, case-folded)
RESERVED_WORDS = {
    "admin",
    "administrator",
    "organiser",
    "organizer",
    "staff",
    "moderator",
    "official",
    "ctfd",
    "l3mon",
    "crew",
    "support",
    "root",
}

NAME_MIN = 1
NAME_MAX = 64


def clean_team_name(name: str) -> str:
    """Clean a team name by stripping bidi/zero-width chars and collapsing whitespace."""
    if not name or not isinstance(name, str):
        return ""
    # Strip bidi / zero-width
    no_bidi = BIDI_ZERO_WIDTH.sub("", name)
    # Collapse confusable whitespace
    collapsed = WHITESPACE.sub(" ", no_bidi).strip()
    return unicodedata.normalize("NFKC", collapsed)


def canonical_team_name(name: str) -> str:
    """Return canonical form for case-insensitive and confusable comparison."""
    clean = clean_team_name(name)
    return clean.casefold()


def check_impersonation(canonical_name: str) -> bool:
    """Return True if canonical_name contains any reserved word as a whole word."""
    words = set(re.findall(r"\b[a-z0-9]+\b", canonical_name))
    return bool(words & RESERVED_WORDS)


def validate_team_name(
    name: str,
    *,
    is_crew: bool = False,
    exclude_team_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Validate a team name against policy.

    Returns (cleaned_name, error_message).
    If valid, error_message is None.
    """
    if not name or not isinstance(name, str):
        return None, "Team name cannot be empty."

    clean = clean_team_name(name)
    if len(clean) < NAME_MIN or len(clean) > NAME_MAX:
        return None, f"Team name must be between {NAME_MIN} and {NAME_MAX} characters."

    canonical = canonical_team_name(clean)

    # Impersonation check (crew is exempt)
    if not is_crew and check_impersonation(canonical):
        return None, "That team name is reserved."

    # Collision check against existing teams in canonical form
    from flask import has_app_context
    if has_app_context():
        from CTFd.models import Teams, db

        query = db.session.query(Teams.id, Teams.name).filter(Teams.name.isnot(None))
        if exclude_team_id is not None:
            query = query.filter(Teams.id != exclude_team_id)

        for tid, existing_name in query.all():
            if canonical_team_name(existing_name) == canonical:
                return None, "That team name is already taken."

    return clean, None
