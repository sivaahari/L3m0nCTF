"""What the theme costs a request that is not one of its pages (SP6 slice 1, the rule of SP3 part 3.7).

The theme's context processor runs for every template CTFd renders, so it must read nothing from the database for a page
that is not ours. The stock figure is measured on a second app with stock CTFd (plugins off, the core theme); a request is
made five times and the cheapest count is taken, as the board plugin's test does.

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_theme_hook_cost.py
"""
from sqlalchemy import event

from CTFd.models import db
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

REPEATS = 5
PASSWORD = "a-long-test-password"  # made up, for the throwaway test app only

# The theme's own pages: gated or provided by our theme
THEME_PAGES = {
    "the sign-in page, a visitor": ("/login", "visitor", 0, 200),
    "the scoreboard, a visitor": ("/scoreboard", "visitor", 0, 302),
}

# name -> (path, who, how many statements more than stock CTFd are allowed)
# Other pages (not the theme's own): must cost nothing extra and preserve stock CTFd answer
OTHER_REQUESTS = {
    "a path nobody serves, a visitor": ("/themes/core/static/css/nothing-here.css", "visitor", 0),
    "the settings page, a player": ("/settings", "player", 0),
    "the stock challenge list, a player": ("/api/v1/challenges", "player", 0),
}
REQUESTS = OTHER_REQUESTS


def statements(fn):
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        response = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return response, seen


def cost_of_each(visitor, player):
    """-> {name: (status, the fewest statements in REPEATS tries)}."""
    out = {}
    all_reqs = {**OTHER_REQUESTS, **{k: (p, w, a) for k, (p, w, a, _) in THEME_PAGES.items()}}
    for name, (path, who, _) in all_reqs.items():
        client = player if who == "player" else visitor

        def ask(client=client, path=path):
            return client.get(path)

        statements(ask)  # the first request fills the caches
        tries = [statements(ask) for _ in range(REPEATS)]
        out[name] = (tries[0][0].status_code, min(len(seen) for _, seen in tries))
    return out


def measure(ours):
    if ours:
        app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    else:
        app = create_ctfd(enable_plugins=False, user_mode="teams")
    try:
        with app.app_context():
            register_user(app, name="viewer", email="viewer@example.com", password=PASSWORD)
            player = login_as_user(app, "viewer", PASSWORD)  # a signed-in client (CTFd's test helper)
            return cost_of_each(app.test_client(), player)
    finally:
        destroy_ctfd(app)


def test_the_theme_costs_a_request_that_is_not_its_page_nothing():
    stock = measure(ours=False)
    ours = measure(ours=True)
    for name, (_, _, allowed) in OTHER_REQUESTS.items():
        assert ours[name][0] == stock[name][0], f"{name}: the answer changed ({stock[name][0]} stock, {ours[name][0]} ours)"
        assert ours[name][1] <= stock[name][1] + allowed, f"{name}: {ours[name][1]} statements against {stock[name][1]} for stock CTFd (at most {allowed} more)"
    for name, (_, _, allowed, expected_status) in THEME_PAGES.items():
        assert ours[name][0] == expected_status, f"{name}: expected status {expected_status}, got {ours[name][0]}"
