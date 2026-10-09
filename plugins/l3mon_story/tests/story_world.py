"""Shared helpers for the cold-open comic's tests: a team-mode app on the day of the round, two channels (one with a programme on air, one
with nothing on air), a folder of made-up story bundles, and the folder wired into the store.

Not a test file. Imported by name from the tests next to it. Nothing here names a real event: the words are made up.
"""
import calendar
import datetime
import json
import os

from freezegun import freeze_time

from CTFd.models import db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.utils import set_config
from tests.helpers import create_ctfd, gen_challenge, login_as_user, register_user

PROGRAMMES = "/api/v1/l3mon/admin/programmes"
RELEASE = "/api/v1/l3mon/admin/release"
STORY = "/api/v1/l3mon/story"
T_START = calendar.timegm(datetime.datetime(2026, 11, 28, 3, 30, 0).timetuple())
T_END = T_START + 24 * 3600
T_LIVE = T_START + 3600
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#223"/><circle cx="80" cy="45" r="20" fill="#fc3"/></svg>'


def clock(t):
    return freeze_time(datetime.datetime.utcfromtimestamp(t))


def make_app():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    app.permanent_session_lifetime = datetime.timedelta(days=3650)
    return app


def started():
    set_config("start", T_START)
    set_config("end", T_END)


def bundle(slug="street", title="Moth Hour", panels=2, kicker=None):
    return {
        "v": 1, "slug": slug, "title": title, "kicker": kicker or f"CH 01 · {slug.title()}", "lang": "en",
        "panels": [
            {
                "id": f"p{i + 1}", "ms": 4000, "enter": "static" if i == 0 else "slide", "alt": f"Panel {i + 1}: a round sun over a dark field.",
                "layers": [{"art": "sun", "x": 0, "y": 0, "w": 100, "h": 100, "z": 0, "from": {"x": 0, "y": 0, "s": 1.0}, "to": {"x": -2, "y": 0, "s": 1.1}}],
                "bubbles": [{"kind": "say", "who": "Tara", "text": f"Line {i + 1} & \"quoted\" it's fine.", "x": 8, "y": 8, "w": 40, "tail": "bl", "at": 500}],
                "sfx": [{"cue": "whoosh", "at": 0}],
            }
            for i in range(panels)
        ],
        "art": {"sun": SVG},
    }


def write_story(folder, slug, obj=None, **kw):
    path = os.path.join(folder, f"{slug}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj if obj is not None else bundle(slug, **kw), handle)
    return path


def load_plan(admin):
    """Two channels: `street` with a programme (to be put on air) and `snack` with a programme that stays withheld."""
    a = gen_challenge(db, name="Lantern Walk", state="hidden", value=100)
    b = gen_challenge(db, name="Noodle Ledger", state="hidden", value=200)
    r = admin.put(PROGRAMMES, json={
        "channels": [
            {"slug": "street", "name": "Mighty Street", "position": 1, "synopsis": "A loud street."},
            {"slug": "snack", "name": "Snack Square", "position": 2, "synopsis": "A hungry square."},
        ],
        "programmes": [
            {"challenge_id": a.id, "channel": "street", "cell": 0, "number": 101, "slug": "lantern_walk"},
            {"challenge_id": b.id, "channel": "snack", "cell": 0, "number": 201, "slug": "noodle_ledger"},
        ],
    })
    assert r.status_code == 200, r.get_data(as_text=True)


def release_street(admin):
    street = Channel.query.filter_by(slug="street").one().id
    programme = Programme.query.filter_by(slug="lantern_walk").one().id
    r = admin.put(RELEASE, json={"changes": [{"kind": "channel", "id": street, "mode": "release"}, {"kind": "programme", "id": programme, "mode": "release"}]})
    assert r.status_code == 200, r.get_data(as_text=True)


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def team_client(app, user, team):
    register_user(app, name=user, email=f"{user}@example.com")
    client = login_as_user(app, user)
    client.get("/team")
    r = client.post("/teams/new", data={"name": team, "password": "password", "nonce": nonce_of(client)})
    assert r.status_code == 302, r.status_code
    return client


def player_without_studio(app, user):
    register_user(app, name=user, email=f"{user}@example.com")
    return login_as_user(app, user)
