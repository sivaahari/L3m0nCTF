"""Whether the sign-up rules apply to this app: the rules box (stream B) and the password rule (`l3mon_guard`).

On a real platform they always apply. CTFd's own test helper (`tests.helpers.register_user`) registers a player with a bare form (no rules box) and the
default password "password", so a CTFd *test* app (`app.testing`) skips them unless a test turns them on with `app.config["L3MON_SIGNUP_RULES"] = True`
(the tests of the rules themselves do). Nothing in a real deployment sets `L3MON_SIGNUP_RULES` (CTFd reads its settings from `config.ini` and the
environment, and it does not read this name from either), and `app.testing` is false there, so a real platform cannot switch the rules off.
"""
from flask import current_app


def enforced() -> bool:
    forced = current_app.config.get("L3MON_SIGNUP_RULES")
    return bool(forced) if forced is not None else not current_app.testing
