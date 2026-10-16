"""The cost of a player's request in database statements (SP3 part 3.7).

The event is 250 to 400 requests a second against one MariaDB, so the number of statements a request makes matters more than anything else in it. These
tests count them (a SQLAlchemy event on the engine) for every player endpoint and fix three rules:

  * the count does not grow with the number of studios (an endpoint that asks the database once for each studio is an N+1 and would make the biggest
    event the slowest);
  * it does not grow with the number of programmes on air, nor with the size of the viewer's own studio (members, solves);
  * each endpoint stays under a ceiling a little above what it costs today, so a change that doubles it is noticed.

Each endpoint is measured cold (the caches emptied: the first request after a change) and warm (the same request again), three times each, and the
cheapest count is taken (CTFd's own caches now and then add a statement to any request). A growth of two statements is allowed; an N+1 over 25 more
studios or 32 more programmes would add 25 or 32.

The studios are made straight in the database: CTFd limits sign-ins, and a frozen clock never lets the limit pass.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board/test_query_budget.py
"""
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import event

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from board_world import PROGRAMMES, RELEASE, T_LIVE, clock, fixed, make_app, solve, started, team_client
from tests.helpers import destroy_ctfd, login_as_user

API = "/api/v1/l3mon"
ENDPOINTS = {
    "ticks": f"{API}/ticks",
    "board": f"{API}/board",
    "guide": f"{API}/guide",
    "guide/epg": f"{API}/guide/epg",
    "scoreboard": f"{API}/scoreboard",
    "scoreboard/rows": f"{API}/scoreboard/rows",
    "bell": "/api/v1/notifications?since_id=0",
    "stories": f"{API}/story",
}
# (cold, warm) ceilings, each a little above what the endpoint costs today with 8 programmes and 4 studios (measured 2026-10-16: ticks 12/8, board 17/13,
# guide 20/16, epg 10/10, scoreboard 8/5, rows 8/5, bell 6/6, stories 9/9)
CEILING = {
    "ticks": (15, 11), "board": (20, 16), "guide": (23, 19), "guide/epg": (13, 13),
    "scoreboard": (11, 8), "scoreboard/rows": (11, 8), "bell": (9, 9), "stories": (12, 12),
}
ALLOWED_GROWTH = 2
# CTFd works out a studio's score member by member (two statements each: their solves and their awards) whenever its cache is cold, and a studio has at most
# four players (the superiors' rule), so a full studio costs at most this much more on a cold request. Accepted, said plainly: it is bounded and not an N+1.
MEMBERS = 4
COLD_COST_OF_A_MEMBER = 2
CHANNELS = ["street", "snack", "gadget", "ninja"]


def statements_of(client, path):
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        response = client.get(path)
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return response, seen


def measure(client, path):
    """-> (cold, warm): the fewest statements in three tries for the first request after the caches were emptied, and for the same request again."""
    colds, warms = [], []
    for _ in range(3):
        clear_standings()
        clear_challenges()
        first, cold = statements_of(client, path)
        second, warm = statements_of(client, path)
        assert first.status_code == 200 and second.status_code == 200, (path, first.status_code, second.status_code)
        colds.append(len(cold))
        warms.append(len(warm))
    return min(colds), min(warms)


def measure_all(client):
    return {name: measure(client, path) for name, path in ENDPOINTS.items()}


def make_programmes(admin, count, first=0):
    """`count` more programmes (a challenge each) spread over four channels, all on air. Returns their challenge ids."""
    made = [(n, fixed(f"Extra {n}", 100 + n, "misc", flag=f"extra-{n}-answer").id) for n in range(first, first + count)]
    channels = [
        {"slug": slug, "name": slug.title(), "position": 1 + i, "accent": f"ch{1 + i}", "picture_key": slug, "synopsis": "A made-up channel."}
        for i, slug in enumerate(CHANNELS)
    ]
    existing = [
        {"challenge_id": p.challenge_id, "channel": Channel.query.get(p.channel_id).slug, "cell": p.cell, "number": p.number, "slug": p.slug, "difficulty": p.difficulty}
        for p in Programme.query.order_by(Programme.id).all()
    ]
    added = [
        {"challenge_id": cid, "channel": CHANNELS[n % len(CHANNELS)], "cell": 100 + n, "number": 1000 + n, "slug": f"extra_{n}", "difficulty": "easy"}
        for n, cid in made
    ]
    r = admin.put(PROGRAMMES, json={"channels": channels, "programmes": existing + added})
    assert r.status_code == 200, r.get_data(as_text=True)
    chans = {c.slug: c.id for c in Channel.query.all()}
    progs = {p.slug: p.id for p in Programme.query.all()}
    changes = [{"kind": "channel", "id": chans[c], "mode": "release"} for c in CHANNELS]
    changes += [{"kind": "programme", "id": progs[f"extra_{n}"], "mode": "release"} for n, _ in made]
    r = admin.put(RELEASE, json={"changes": changes})
    assert r.status_code == 200, r.get_data(as_text=True)
    return [cid for _, cid in made]


def make_player(name, team=None):
    """A player made directly in the database; with no `team` the player founds a studio of the same name."""
    user = Users(name=name, email=f"{name}@example.com", password="password")
    db.session.add(user)
    db.session.flush()
    if team is None:
        team = Teams(name=f"studio-{name}", password="password", captain_id=user.id)
        db.session.add(team)
        db.session.flush()
    user.team_id = team.id
    db.session.commit()
    return user


def add_studios(count, first, challenge_ids):
    """`count` more studios, each with one player who solves two of the programmes."""
    for n in range(first, first + count):
        user = make_player(f"p{n}")
        for cid in (challenge_ids[n % len(challenge_ids)], challenge_ids[(n + 3) % len(challenge_ids)]):
            solve(user.team_id, user.id, cid)


def solve_all(user, challenge_ids):
    for cid in challenge_ids:
        solve(user.team_id, user.id, cid)


@pytest.fixture(scope="module")
def measured():
    """The endpoints' costs at four sizes: 4 studios and 8 programmes; 29 studios; 40 programmes with the viewer having solved 30 of them; the viewer's studio with 5 players."""
    app = make_app()
    try:
        with app.app_context():
            started()
            with clock(T_LIVE):
                admin = login_as_user(app, "admin")
                ids = make_programmes(admin, 8)
                viewer = team_client(app, "viewer", "viewer-studio")
                add_studios(3, 0, ids)
                sizes = SimpleNamespace(base=measure_all(viewer))
                add_studios(25, 3, ids)
                sizes.studios = measure_all(viewer)
                ids += make_programmes(admin, 32, first=8)
                me = Users.query.filter_by(name="viewer").one()
                solve_all(me, ids[:30])
                sizes.programmes = measure_all(viewer)
                for n in range(4):
                    solve_all(make_player(f"mate{n}", Teams.query.get(me.team_id)), ids[30 + 2 * n : 32 + 2 * n])
                sizes.members = measure_all(viewer)
        return sizes
    finally:
        destroy_ctfd(app)


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_no_endpoint_asks_the_database_more_for_more_studios(measured, name):
    for when in (0, 1):
        assert measured.studios[name][when] <= measured.base[name][when] + ALLOWED_GROWTH, (
            f"{name}: {measured.base[name][when]} statements with 4 studios, {measured.studios[name][when]} with 29 ({'cold' if when == 0 else 'warm'})")


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_no_endpoint_asks_the_database_more_for_more_programmes_on_air_and_more_solves(measured, name):
    for when in (0, 1):
        assert measured.programmes[name][when] <= measured.studios[name][when] + ALLOWED_GROWTH, (
            f"{name}: {measured.studios[name][when]} statements with 8 programmes, {measured.programmes[name][when]} with 40 and 30 solves of the viewer ({'cold' if when == 0 else 'warm'})")


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_a_bigger_studio_adds_only_what_a_cold_score_costs_for_its_members(measured, name):
    for when in (0, 1):
        allowed = ALLOWED_GROWTH + (COLD_COST_OF_A_MEMBER * MEMBERS if when == 0 else 0)
        assert measured.members[name][when] <= measured.programmes[name][when] + allowed, (
            f"{name}: {measured.programmes[name][when]} statements with one player, {measured.members[name][when]} with five ({'cold' if when == 0 else 'warm'}, at most {allowed} more)")


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_every_endpoint_stays_under_its_ceiling_at_every_size(measured, name):
    for size in ("base", "studios", "programmes", "members"):
        cold, warm = getattr(measured, size)[name]
        room = COLD_COST_OF_A_MEMBER * MEMBERS if size == "members" else 0
        assert cold <= CEILING[name][0] + room and warm <= CEILING[name][1], f"{name} at size {size}: {cold} cold and {warm} warm, ceilings {CEILING[name]} (and {room} cold for the members)"


# ---- what the polled endpoints do not need ------------------------------------------------------------------------------------------------------

def test_the_tick_and_the_bell_do_not_read_which_programmes_the_studio_solved(measured_app):
    """The tick is asked by every player every 15 seconds and the bell with it; neither draws a programme, so neither needs the studio's solved list
    (the board, the Guide, the grid and the stories do, and read it when they need it)."""
    viewer = measured_app
    for name in ("ticks", "bell"):
        for _ in range(2):
            response, seen = statements_of(viewer, ENDPOINTS[name])
            assert response.status_code == 200
        reads = [s for s in seen if "solves.challenge_id AS solves_challenge_id" in s]
        assert reads == [], f"{name} read the studio's solves: {reads[:1]}"
    response, seen = statements_of(viewer, ENDPOINTS["board"])
    assert any("solves.challenge_id AS solves_challenge_id" in s for s in seen), "the board still reads them (this test would otherwise prove nothing)"


@pytest.fixture()
def measured_app():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = make_programmes(admin, 6)
            viewer = team_client(app, "viewer", "viewer-studio")
            solve_all(Users.query.filter_by(name="viewer").one(), ids[:2])
            yield viewer
    destroy_ctfd(app)


# ---- the tick is read before the data it is sent with --------------------------------------------------------------------------------------------

DATA = ("l3mon_", "FROM submissions", "FROM challenges", "FROM notifications", "FROM awards", "FROM unlocks", "account_id")


@pytest.mark.parametrize("name,module", [("board", "board"), ("guide", "guide"), ("scoreboard", "scoreboard")])
def test_the_tick_a_page_carries_is_read_before_the_data_it_is_sent_with(measured_app, name, module):
    """MariaDB's default isolation gives a request the view of the database it had at its first statement. If a solve commits and moves the tick after
    that and the page reads the tick last, it carries the NEW tick with the OLD data, and a page that follows the tick never fetches again until the next
    change (found by the audit of 3.7). Read first, the worst case is one fetch more."""
    import importlib

    target = importlib.import_module(f"CTFd.plugins.l3mon_board.{module}")
    seen, at = [], []
    real = target.signature

    def noted():
        at.append(len(seen))
        return real()

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        with mock.patch.object(target, "signature", noted):
            response = measured_app.get(ENDPOINTS[name])
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    assert response.status_code == 200 and len(at) == 1
    early = [s for s in seen[: at[0]] if any(word in s for word in DATA)]
    assert early == [], f"{name} read data before it read the tick: {' '.join(early[0].split())[:90] if early else ''}"
