"""What our request hooks cost a request that is not ours (SP3 part 3.7).

Every plugin of ours hangs hooks on every request (the share door, the minute check, the lift watcher, the ratings door, the tick, the release
scheduler, the notification doors). A hook that asks the database before it has looked at the request makes every page, picture and script pay for
something only a few requests need: at 250 to 400 requests a second that is a thousand statements a minute for nothing, on the one machine that cannot
be made bigger. The rule: a request our plugins do not answer costs the database what stock CTFd costs. (The doors that must look at the plan, the challenge
list and the scoreboard among them, read it from the caches CTFd's own request already filled; if one ever has to ask, give it a stated allowance in REQUESTS.)

The stock figure is measured on a second app made in CTFd's safe mode (no plugins of ours). A request is made five times and the cheapest count is
taken: CTFd's own caches now and then add a statement to any request (that is not ours and not the point here).

The first version of this test found the share door asking "is release control in use" on every request, before it had looked at the path.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board/test_hook_cost.py
"""
from sqlalchemy import event

from CTFd.models import db
from board_world import T_LIVE, clock, fixed, make_app, on_air, started, team_client, world
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user

REPEATS = 5

# name -> (method, path, who, how many statements more than stock CTFd are allowed)
REQUESTS = {
    "a path nobody serves, a visitor": ("get", "/themes/core/static/css/nothing-here.css", "visitor", 0),
    "the login page, a visitor": ("get", "/login", "visitor", 0),
    "the challenges page, a player": ("get", "/challenges", "player", 0),
    "the player's own studio": ("get", "/api/v1/teams/me", "player", 0),
    "a wrong flag": ("post", "/api/v1/challenges/attempt", "player", 0),
    "the stock challenge list": ("get", "/api/v1/challenges", "player", 0),
    "the stock scoreboard": ("get", "/api/v1/scoreboard", "player", 0),
}


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


def cost_of_each(player, visitor, challenge_id):
    """-> {name: (status, the fewest statements in REPEATS tries)}."""
    out = {}
    for name, (method, path, who, _) in REQUESTS.items():
        client = player if who == "player" else visitor
        if method == "get":
            def ask():
                return client.get(path)
        else:
            with client.session_transaction() as sess:
                nonce = sess.get("nonce")

            def ask():
                return client.post(path, json={"challenge_id": challenge_id, "submission": "wrong"}, headers={"CSRF-Token": nonce})
        statements(ask)  # the first request fills the caches
        tries = [statements(ask) for _ in range(REPEATS)]
        out[name] = (tries[0][0].status_code, min(len(seen) for _, seen in tries))
    return out


def measure(ours):
    app = make_app() if ours else create_ctfd(user_mode="teams", enable_plugins=False)
    try:
        with app.app_context():
            started()
            with clock(T_LIVE):
                if ours:
                    admin = login_as_user(app, "admin")
                    ids = world(app, admin)
                    on_air(admin, "lantern_walk", "hush_alley", "noodle_ledger", "steam_tunnel", "coffee_break")
                    challenge_id = ids.lantern
                else:
                    challenge_id = fixed("Lantern Walk", 100, "web", flag="lantern-answer", state="visible").id
                player = team_client(app, "viewer", "viewer-studio")
                return cost_of_each(player, app.test_client(), challenge_id)
    finally:
        destroy_ctfd(app)


def test_a_request_that_is_not_ours_costs_what_stock_ctfd_costs_and_only_the_doors_add_one():
    stock = measure(ours=False)
    ours = measure(ours=True)
    for name, (_, _, _, allowed) in REQUESTS.items():
        assert ours[name][0] == stock[name][0], f"{name}: the answer changed ({stock[name][0]} stock, {ours[name][0]} ours)"
        assert ours[name][1] <= stock[name][1] + allowed, f"{name}: {ours[name][1]} statements against {stock[name][1]} for stock CTFd (at most {allowed} more)"
