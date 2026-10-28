"""Username policy (SP6): the prototype's `vUsername` (`platform-ui/src/js/validate.js`) enforced on the server.

3 to 24 characters: ASCII letters, digits, dot, dash and underscore, nothing else (no space, no accent, no invisible character, no look-alike letter of
another alphabet). A reserved name answers "taken", like a name in use, so the list is not revealed. A name that differs from another only by case is the
same name. The crew is exempt (it may give any account any name).

The messages are the prototype's words (`V_MSG`); a page that maps an error to its field imports `ALL_MESSAGES` instead of copying the text.
Apply it where a name is **chosen or changed** (sign-up, the settings page, the API's `PATCH /users/me`), not where an existing name is only shown or saved
back unchanged: accounts made before this rule may have a name it would refuse.
"""
import re

USER_RE = re.compile(r"[A-Za-z0-9._-]{3,24}")  # used with fullmatch: `$` would also accept a trailing line break

# Names nobody may take, compared ignoring case (the prototype's RESERVED_NAMES)
RESERVED_NAMES = frozenset({
    "admin", "administrator", "root", "crew", "staff", "support", "system", "moderator", "owner", "security",
    "l3mon", "l3m0n", "lemon", "l3monctf", "l3m0nctf", "lemonctf", "ctfd", "organiser", "organizer", "director", "guest", "anonymous", "null", "undefined",
})

MSG_EMPTY = "Choose a username."
MSG_SHAPE = "Usernames are 3 to 24 characters: letters, numbers, dots, dashes and underscores."
MSG_TAKEN = "That username is taken. Try another."
ALL_MESSAGES = (MSG_EMPTY, MSG_SHAPE, MSG_TAKEN)


def validate_username(
    name: str,
    *,
    is_crew: bool = False,
    exclude_user_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Returns (name, error_message): the name as it is kept (the ends trimmed), or (None, why it is refused)."""
    value = name.strip() if isinstance(name, str) else ""
    if not value:
        return None, MSG_EMPTY
    if is_crew:
        return value, None
    if not USER_RE.fullmatch(value):
        return None, MSG_SHAPE
    if value.lower() in RESERVED_NAMES:
        return None, MSG_TAKEN

    from flask import has_app_context
    if has_app_context():
        from sqlalchemy import func

        from CTFd.models import Users, db

        query = db.session.query(Users.id).filter(func.lower(Users.name) == value.lower())
        if exclude_user_id is not None:
            query = query.filter(Users.id != exclude_user_id)
        if query.first() is not None:
            return None, MSG_TAKEN
    return value, None
