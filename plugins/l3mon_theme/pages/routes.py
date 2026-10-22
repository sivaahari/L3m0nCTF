"""The prototype's addresses that CTFd does not have (SP6 plumbing).

The prototype's pages (and its scripts, which are used as they were built) link to /board, /guide and /scoreboard. CTFd's own
addresses are /challenges and /scoreboard, and it has no Guide. Until the slice that builds a page answers its prototype address
itself, the address sends a visitor to the page that answers today (values.ROUTES says which). A slice that builds the page registers
its own view in its own module (pages/board.py, pages/guide.py ...): the modules are imported in the order of their names, so its view is
there before this one looks, and the alias steps aside by itself (see install). Nobody edits another slice's lines.

An alias is a plain redirect (302, so a browser never caches a destination that is about to change), for GET and HEAD only.
"""
from flask import redirect

from CTFd.plugins.l3mon_theme.values import ROUTES

# prototype address -> the name of the route (values.ROUTES) that answers today
ALIASES = {"/board": "channels", "/guide": "guide"}


def install(app):
    existing = {rule.rule for rule in app.url_map.iter_rules()}
    for path, route in sorted(ALIASES.items()):
        if path in existing:
            continue  # a slice (or CTFd) answers this address itself: no alias

        def make(target):
            def alias():
                return redirect(target, code=302)

            return alias

        app.add_url_rule(path, endpoint=f"l3mon_theme_alias_{route}_{path.strip('/')}", view_func=make(ROUTES[route]), methods=["GET", "HEAD"])
