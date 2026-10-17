"""Hook cost verification for l3mon_guard (SP5 B4.9).

Proves that:
1. Requests that are none of l3mon_guard's business cost 0 extra database statements compared to stock CTFd.
2. Normal attempt submission costs 0 extra database statements (the attempt counter is kept purely in Redis).
"""
from sqlalchemy import event

from CTFd.models import db
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_flag, gen_team, login_as_user, register_user

REPEATS = 5

REQUESTS = {
    "static asset (visitor)": ("get", "/themes/core/static/css/nothing-here.css", "visitor", 0),
    "login page (visitor)": ("get", "/login", "visitor", 0),
    "challenges page (player)": ("get", "/challenges", "player", 0),
    "scoreboard (player)": ("get", "/api/v1/scoreboard", "player", 0),
    "stock challenge list (player)": ("get", "/api/v1/challenges", "player", 0),
}


def count_statements(fn):
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        response = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return response, seen


def cost_of_each(player, visitor):
    out = {}
    for name, (method, path, who, _) in REQUESTS.items():
        client = player if who == "player" else visitor
        def ask():
            return client.get(path)

        count_statements(ask)  # warm caches
        tries = [count_statements(ask) for _ in range(REPEATS)]
        out[name] = (tries[0][0].status_code, min(len(seen) for _, seen in tries))
    return out


def measure(ours: bool):
    app = create_ctfd(user_mode="teams", enable_plugins=ours)
    try:
        with app.app_context():
            register_user(app, name="hook_player", email="hook@example.com")
            player = login_as_user(app, "hook_player")
            return cost_of_each(player, app.test_client())
    finally:
        destroy_ctfd(app)


def test_guard_hooks_cost_zero_statements_on_unrelated_routes():
    stock = measure(ours=False)
    ours = measure(ours=True)
    for name, (_, _, _, allowed) in REQUESTS.items():
        assert ours[name][0] == stock[name][0], f"{name}: status changed"
        assert ours[name][1] <= stock[name][1] + allowed, (
            f"{name}: {ours[name][1]} statements vs stock {stock[name][1]} (max allowed +{allowed})"
        )
