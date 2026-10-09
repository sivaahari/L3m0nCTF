"""Shared helpers for the board tests: a team-mode app on the day of the round, a made-up world (three channels, seven programmes of
both kinds, studios and players), the crew's real calls to load and release it, and the numbers CTFd itself reports.

Not a test file. Imported by name from the tests next to it. Nothing here names a real event: the words are made up.
"""
import calendar
import datetime
from types import SimpleNamespace

from freezegun import freeze_time

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Challenges, Solves, Teams, Users, db
from CTFd.plugins.dynamic_challenges import DynamicChallenge
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.utils import set_config
from tests.helpers import create_ctfd, gen_challenge, gen_flag, gen_hint, login_as_user, register_user

PROGRAMMES = "/api/v1/l3mon/admin/programmes"
RELEASE = "/api/v1/l3mon/admin/release"
BOARD = "/api/v1/l3mon/board"
TICKS = "/api/v1/l3mon/ticks"
T_START = calendar.timegm(datetime.datetime(2026, 11, 28, 3, 30, 0).timetuple())  # 09:00 IST
T_END = T_START + 24 * 3600
T_LIVE = T_START + 3600  # 10:00 IST
T_LATER = T_LIVE + 2 * 3600  # 12:00 IST
SOLVED_AT = datetime.datetime.utcfromtimestamp(T_LIVE)


def clock(t):
    return freeze_time(datetime.datetime.utcfromtimestamp(t))


def make_app():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    app.permanent_session_lifetime = datetime.timedelta(days=3650)  # a sign-in made today must still be good on the day of the round
    return app


def started():
    set_config("start", T_START)
    set_config("end", T_END)


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def team_client(app, user, team):
    """A signed-in player who created a studio through the real routes."""
    register_user(app, name=user, email=f"{user}@example.com")
    client = login_as_user(app, user)
    client.get("/team")
    r = client.post("/teams/new", data={"name": team, "password": "password", "nonce": nonce_of(client)})
    assert r.status_code == 302, r.status_code
    return client


def join_client(app, user, team, password="password"):
    """A second player who joins an existing studio."""
    register_user(app, name=user, email=f"{user}@example.com")
    client = login_as_user(app, user)
    client.get("/team")
    r = client.post("/teams/join", data={"name": team, "password": password, "nonce": nonce_of(client)})
    assert r.status_code == 302, r.status_code
    return client


def fixed(name, value=100, category="web", flag=None, state="hidden", **kw):
    challenge = gen_challenge(db, name=name, value=value, category=category, state=state, **kw)
    if flag:
        gen_flag(db, challenge.id, content=flag)
    return challenge


def dynamic(name, initial=500, minimum=200, decay=15, category="crypto", flag=None, state="hidden", function="logarithmic"):
    challenge = DynamicChallenge(
        name=name, category=category, description="d", initial=initial, minimum=minimum, decay=decay, function=function,
        state=state, type="dynamic",
    )
    db.session.add(challenge)
    db.session.commit()
    if flag:
        gen_flag(db, challenge.id, content=flag)
    clear_challenges()
    return challenge


def world(app, admin):
    """The made-up world, loaded the way the author kit's sync will load it (nothing on air yet). Returns names -> ids."""
    street = [
        fixed("Lantern Walk", 100, "web", flag="lantern-answer", attribution="Asha"),
        dynamic("Moth Cipher", 500, 200, 15, "crypto", flag="moth-answer"),
        fixed("Hush Alley", 300, "forensics", flag="hush-answer"),
    ]
    snack = [
        fixed("Noodle Ledger", 200, "osint", flag="noodle-answer"),
        fixed("Steam Tunnel", 400, "pwn", flag="steam-answer"),
        fixed("Dumpling Gate", 150, "misc", flag="dumpling-answer"),
    ]
    brk = [fixed("Coffee Break", 50, "misc", flag="coffee-answer")]
    gen_hint(db, street[0].id, content="look left", cost=10)
    gen_hint(db, street[0].id, content="look right", cost=25)
    db.session.commit()
    dumpling = Challenges.query.get(snack[2].id)
    dumpling.requirements = {"prerequisites": [street[0].id]}
    db.session.commit()
    ids = SimpleNamespace(
        lantern=street[0].id, moth=street[1].id, hush=street[2].id, noodle=snack[0].id, steam=snack[1].id, dumpling=snack[2].id, coffee=brk[0].id,
    )
    plan = {
        "channels": [
            {"slug": "street", "name": "Mighty Street", "position": 1, "accent": "ch1", "picture_key": "street", "synopsis": "A loud street."},
            {"slug": "snack", "name": "Snack Square", "position": 2, "accent": "ch2", "picture_key": "snack", "synopsis": "A hungry square."},
            {"slug": "break", "name": "Sponsored Break", "position": 7, "kind": "sponsored", "sponsor_name": "Acme", "sponsor_logo": "acme.svg", "picture_key": "break"},
        ],
        "programmes": [
            {"challenge_id": ids.lantern, "channel": "street", "cell": 0, "number": 101, "slug": "lantern_walk", "difficulty": "warmup"},
            {"challenge_id": ids.moth, "channel": "street", "cell": 1, "number": 102, "slug": "moth_cipher", "difficulty": "easy"},
            {"challenge_id": ids.hush, "channel": "street", "cell": 2, "number": 103, "slug": "hush_alley", "difficulty": "hard"},
            {"challenge_id": ids.noodle, "channel": "snack", "cell": 0, "number": 201, "slug": "noodle_ledger", "difficulty": "medium"},
            {"challenge_id": ids.steam, "channel": "snack", "cell": 1, "number": 202, "slug": "steam_tunnel", "difficulty": "insane", "delivery": "live_single"},
            {"challenge_id": ids.dumpling, "channel": "snack", "cell": 2, "number": 203, "slug": "dumpling_gate", "difficulty": "medium"},
            {"challenge_id": ids.coffee, "channel": "break", "cell": 0, "number": 701, "slug": "coffee_break", "difficulty": "easy"},
        ],
    }
    r = admin.put(PROGRAMMES, json=plan)
    assert r.status_code == 200, r.get_data(as_text=True)
    return ids


def on_air(admin, *slugs, channels=("street", "snack", "break")):
    """Put the named channels and programmes on air now (the caller holds the clock)."""
    programmes = {p.slug: p.id for p in Programme.query.all()}
    chans = {c.slug: c.id for c in Channel.query.all()}
    changes = [{"kind": "channel", "id": chans[c], "mode": "release"} for c in channels]
    changes += [{"kind": "programme", "id": programmes[s], "mode": "release"} for s in slugs]
    r = admin.put(RELEASE, json={"changes": changes})
    assert r.status_code == 200, r.get_data(as_text=True)


def withhold(admin, *slugs):
    programmes = {p.slug: p.id for p in Programme.query.all()}
    r = admin.put(RELEASE, json={"changes": [{"kind": "programme", "id": programmes[s], "mode": "withhold"} for s in slugs], "reason": "test"})
    assert r.status_code == 200, r.get_data(as_text=True)


def solve(team_id, user_id, challenge_id, at=None):
    """A correct submission recorded straight in the database, as the crew's tools and the tests do (CTFd's own route is covered by the attempt tests)."""
    row = Solves(user_id=user_id, team_id=team_id, challenge_id=challenge_id, ip="127.0.0.1", provided="right", date=at or SOLVED_AT)
    db.session.add(row)
    db.session.commit()
    clear_standings()
    clear_challenges()
    return row


def ids_of(*names):
    out = []
    for name in names:
        team = Teams.query.filter_by(name=name).first()
        out.append((team.id, sorted(u.id for u in team.members)))
    return out


def user_id(name):
    return Users.query.filter_by(name=name).first().id


def get(client, path, **kw):
    r = client.get(path, **kw)
    return r, (r.get_json(silent=True) or {})
