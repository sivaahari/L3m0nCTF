"""Studio name policy (SP5 B4.4; made the same as the prototype's `vStudioName` and `studioKey` in SP6, `platform-ui/src/js/validate.js`).

Enforces:
- A name with a zero-width, bidirectional-control, control, format, private-use or unassigned character in it is REFUSED (never silently cleaned: what is checked must be what is stored)
- Confusable white space is collapsed and the compatibility forms are composed (NFKC); the cleaned name is the one that is kept, and the guard's wrappers write it back into the request so CTFd stores exactly it
- Letters of two of Latin, Cyrillic and Greek in one name are refused (the usual trick for an impostor)
- Length 2 to 32 characters, counted as the browser counts them (characters, not bytes)
- Prohibition of organisational impersonation words (whole-word match), looking through look-alike letters
- Comparison ignoring case, compatibility forms (NFKC) and look-alike letters of another alphabet, against the studios that exist
- Crew / admin exemption from the reserved words

The messages are the prototype's words (`V_MSG`), so the wizard, the studio page and the crew's API speak alike. They are module constants so a page that
maps an error to its field imports them (`ALL_MESSAGES`) instead of copying the text. A reserved word answers "taken" like a name in use, so the list is not
revealed.
"""
import re
import unicodedata

# Zero-width, bidi, and invisible control characters
BIDI_ZERO_WIDTH = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\xad\u061c\U000e0000-\U000e007f\u3164\u115f\u1160\uffa0\u2800\u034f]"
)

# Collapsible whitespace (including unicode spaces)
WHITESPACE = re.compile(r"[\s\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]+")

# The white space the browser's own cleaning knows (JavaScript's \s, without the byte order mark, which is refused here as invisible). Python's \s also takes
# the control characters 0x1c to 0x1f and 0x85 for spaces; those are control characters and are refused.
BROWSER_SPACE = re.compile(r"[\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")

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

NAME_MIN = 2
NAME_MAX = 32

MSG_EMPTY = "Choose a studio name."
MSG_LENGTH = f"Studio names are {NAME_MIN} to {NAME_MAX} characters."
MSG_CHARS = "Use letters, numbers, spaces and punctuation only."
MSG_SCRIPTS = "Do not mix letters from different alphabets in one name."
MSG_TAKEN = "That studio name is taken."
ALL_MESSAGES = (MSG_EMPTY, MSG_LENGTH, MSG_CHARS, MSG_SCRIPTS, MSG_TAKEN)

# Letters of another alphabet that are drawn like a Latin letter (the common Cyrillic and Greek ones), as the prototype's table (written as escapes so no
# foreign glyph sits in the source)
LOOKALIKES = {
    "\u0430": "a", "\u0432": "b", "\u0435": "e", "\u043a": "k", "\u043c": "m", "\u043d": "h", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0442": "t",
    "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s", "\u0501": "d", "\u04cf": "l", "\u051b": "q", "\u051d": "w", "\u03bf": "o",
    "\u03b1": "a", "\u03bd": "v", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u", "\u03c7": "x", "\u03b9": "i", "\u03ba": "k", "\u03b5": "e", "\u03b2": "b",
    "\u03b7": "n", "\u03bc": "u", "\u03b6": "z",
}
_SCRIPTS = ("LATIN", "CYRILLIC", "GREEK")
_BAD_CATEGORIES = {"Cc", "Cf", "Co", "Cn", "Zl", "Zp"}


def clean_team_name(name: str) -> str:
    """Clean a team name by stripping bidi/zero-width chars and collapsing whitespace."""
    if not name or not isinstance(name, str):
        return ""
    # A tab or a line break between two words is a space (the browser's cleaning does the same), so it is turned into one before the invisible characters go
    spaced = WHITESPACE.sub(" ", name)
    # Strip bidi / zero-width
    no_bidi = BIDI_ZERO_WIDTH.sub("", spaced)
    # Collapse confusable whitespace
    collapsed = WHITESPACE.sub(" ", no_bidi).strip()
    return unicodedata.normalize("NFKC", collapsed)


def canonical_team_name(name: str) -> str:
    """Return canonical form for case-insensitive and confusable comparison: cleaned, case-folded, look-alike letters read as the Latin letter."""
    clean = clean_team_name(name)
    # full case folding can take a letter apart (a Greek letter with two accents becomes the letter and its accents), so the result is composed again
    folded = unicodedata.normalize("NFKC", clean.casefold())
    return "".join(LOOKALIKES.get(c, c) for c in folded)


def check_impersonation(canonical_name: str) -> bool:
    """Return True if canonical_name contains any reserved word as a whole word."""
    words = set(re.findall(r"\b[a-z0-9]+\b", canonical_name))
    return bool(words & RESERVED_WORDS)


def has_invisible_characters(name: str) -> bool:
    """True when a character that draws nothing (or nothing known) is left in the name."""
    return any(unicodedata.category(c) in _BAD_CATEGORIES for c in name)


def mixes_alphabets(name: str) -> bool:
    """True when letters of two of Latin, Cyrillic and Greek are mixed in one name."""
    found = set()
    for c in name:
        if c.isalpha():
            first = unicodedata.name(c, "").split(" ", 1)[0]
            if first in _SCRIPTS:
                found.add(first)
    return len(found) > 1


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
        return None, MSG_EMPTY

    # Looked at as it was typed (a tab or a line break is a space, as the browser reads it): an invisible character is refused, not removed, so the name
    # that was checked is the name that is stored
    spaced = BROWSER_SPACE.sub(" ", name)
    if not spaced.strip():
        return None, MSG_EMPTY
    if BIDI_ZERO_WIDTH.search(spaced) or has_invisible_characters(spaced):
        return None, MSG_CHARS
    clean = clean_team_name(name)
    if mixes_alphabets(clean):
        return None, MSG_SCRIPTS
    if len(clean) < NAME_MIN or len(clean) > NAME_MAX:
        return None, MSG_LENGTH

    canonical = canonical_team_name(clean)

    # Impersonation check (crew is exempt)
    if not is_crew and check_impersonation(canonical):
        return None, MSG_TAKEN

    # Collision check against existing teams in canonical form
    from flask import has_app_context
    if has_app_context():
        from CTFd.models import Teams, db

        query = db.session.query(Teams.id, Teams.name).filter(Teams.name.isnot(None))
        if exclude_team_id is not None:
            query = query.filter(Teams.id != exclude_team_id)

        for tid, existing_name in query.all():
            if canonical_team_name(existing_name) == canonical:
                return None, MSG_TAKEN

    return clean, None
