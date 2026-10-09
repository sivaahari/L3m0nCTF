"""Release control on the real stack (SP3 part 3.2): nginx, gunicorn with four gevent workers, MariaDB and Redis.

The plugin tests prove the rules on SQLite and on a real MariaDB. This proves the same behaviour end to end, over HTTP, with the
stack's own settings: nothing is on air until the crew says so, a release shows exactly that and announces it once, a scheduled
drop appears for the first request after its second (even when many arrive together) and is announced once, and a pulled-back
programme disappears from every list while its flag records nothing.

Same prerequisites and runner as test_stack.py. The event window is a preset that CTFd's admin API cannot change, so the module
renders a window around the present moment into the generated settings and restarts CTFd, and puts the original back (and restarts
again) at the end; everything it creates (challenges, players, the plan, the announcements) is removed again.
"""
from __future__ import annotations

import concurrent.futures
import json
import secrets as pysecrets
import subprocess
import time
from types import SimpleNamespace

import pytest

import test_stack as t

pytestmark = pytest.mark.integration

PRESET = t.ROOT / "deploy" / "compose" / "generated" / "preset_configs.json"
PROGRAMMES = "/api/v1/l3mon/admin/programmes"
RELEASE = "/api/v1/l3mon/admin/release"
FIRST = {"name": "Quokka Ledger", "flag": "quokka-answer-one", "value": 200}
SECOND = {"name": "Numbat Lantern", "flag": "numbat-answer-two", "value": 300}


def admin(method: str, path: str, body=None):
    headers = {"Authorization": f"Token {t.secret('PRESET_ADMIN_TOKEN')}", "Content-Type": "application/json"}
    status, _, data = t.request(path, method=method, headers=headers, body=json.dumps(body).encode() if body is not None else None)
    try:
        return status, json.loads(data)
    except ValueError:
        return status, data


def get_json(session: t.Session, path: str):
    response = session.open(path)
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode("utf-8", "replace")


def post_json(session: t.Session, path: str, body: dict):
    """A signed-in player's JSON write: CTFd wants its CSRF token in a header."""
    import urllib.request

    token = session.nonce("/challenges")
    request = urllib.request.Request(
        f"http://127.0.0.1:{t.PORT}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Host": t.HOST, "Content-Type": "application/json", "CSRF-Token": token},
    )
    try:
        response = session.opener.open(request, timeout=20)
    except Exception as error:  # an HTTP error status is a normal answer here
        response = error
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode("utf-8", "replace")


def in_ctfd(code: str) -> str:
    out = subprocess.run(["docker", "exec", t.container_id("ctfd"), "l3mon-run", "/opt/venv/bin/python", "-c", code], capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-600:]
    return out.stdout


CLEANUP = "\n".join([
    "from CTFd import create_app",
    "from CTFd.models import Notifications, db",
    "from CTFd.plugins.l3mon_core.models import Audit, Channel",
    "from CTFd.cache import clear_challenges, clear_standings, cache",
    "app = create_app()",
    "with app.app_context():",
    "    Channel.query.delete()",
    "    Notifications.query.filter(Notifications.title == 'New on air').delete()",
    "    Audit.query.delete()",
    "    db.session.commit()",
    "    clear_challenges(); clear_standings(); cache.delete('l3mon:release:next')",
    "    print('CLEANED')",
])


def make_challenge(spec: dict) -> int:
    status, out = admin("POST", "/api/v1/challenges", {"name": spec["name"], "category": "Test", "description": "plain words", "value": spec["value"], "state": "hidden", "type": "standard"})
    assert status == 200, out
    cid = out["data"]["id"]
    status, out = admin("POST", "/api/v1/flags", {"challenge_id": cid, "content": spec["flag"], "type": "static", "data": ""})
    assert status == 200, out
    return cid


def make_player(name: str):
    password = pysecrets.token_urlsafe(18)
    status, out = admin("POST", "/api/v1/users", {"name": name, "email": f"{name}@example.com", "password": password, "type": "user", "verified": True})
    assert status == 200, out
    user_id = out["data"]["id"]
    session = t.Session()
    answer = session.open("/login", {"name": name, "password": password, "_submit": "Submit", "nonce": session.nonce("/login")})
    assert answer.status == 302, f"sign-in answered {answer.status}"
    answer = session.open("/teams/new", {"name": f"{name}-team", "password": pysecrets.token_urlsafe(12), "_submit": "Create", "nonce": session.nonce("/teams/new")})
    assert answer.status == 302, f"team creation answered {answer.status}: {answer.read()[:300]!r}"
    status, who = admin("GET", f"/api/v1/users/{user_id}")
    return session, user_id, who["data"]["team_id"]


def purge():
    """Remove whatever an earlier or half-finished run left behind (by name), so a run always starts clean."""
    for path, field, name in (("/api/v1/teams", "name", "relplayer-team"), ("/api/v1/users", "name", "relplayer")):
        status, out = admin("GET", f"{path}?field={field}&q={name}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == name:
                admin("DELETE", f"{path}/{row['id']}")
    for spec in (FIRST, SECOND):
        status, out = admin("GET", f"/api/v1/challenges?view=admin&field=name&q={spec['name'].split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == spec["name"]:
                admin("DELETE", f"/api/v1/challenges/{row['id']}")


def change(*changes, reason="integration test"):
    status, out = admin("PUT", RELEASE, {"changes": list(changes), "reason": reason})
    assert status == 200, out
    return out["data"]


def restart_ctfd():
    t.compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "ctfd", timeout=400)
    t.wait_healthy("ctfd", 180)


@pytest.fixture(scope="module")
def world():
    original = PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    made = SimpleNamespace(challenges=[], users=[], player=None)
    try:
        restart_ctfd()
        purge()
        first, second = make_challenge(FIRST), make_challenge(SECOND)
        made.challenges = [first, second]
        status, out = admin("PUT", PROGRAMMES, {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {"challenge_id": first, "channel": "street", "cell": 0, "number": 101, "slug": "quokka"},
                {"challenge_id": second, "channel": "street", "cell": 1, "number": 102, "slug": "numbat"},
            ],
        })
        assert status == 200, out
        session, user_id, team_id = make_player("relplayer")
        made.users = [user_id]
        made.ids = SimpleNamespace(first=first, second=second)
        made.session, made.user_id, made.team_id = session, user_id, team_id
        status, view = admin("GET", RELEASE)
        made.channel_id = view["data"]["channels"][0]["id"]
        made.programme = {p["slug"]: p["id"] for p in view["data"]["channels"][0]["programmes"]}
        yield made
    finally:
        try:
            purge()
            in_ctfd(CLEANUP)
        finally:
            PRESET.write_bytes(original)
            restart_ctfd()


def visible_names(session) -> set:
    status, out = get_json(session, "/api/v1/challenges")
    assert status == 200, out
    return {c["name"] for c in out["data"]}


def new_on_air(session) -> list:
    status, out = get_json(session, "/api/v1/notifications")
    assert status == 200, out
    return [n["content"] for n in out["data"] if n["title"] == "New on air"]


def test_01_a_loaded_plan_puts_nothing_on_air(world):
    assert visible_names(world.session) == set()
    for cid in (world.ids.first, world.ids.second):
        status, _ = get_json(world.session, f"/api/v1/challenges/{cid}")
        assert status == 404
    assert new_on_air(world.session) == []


def test_02_releasing_a_channel_and_one_programme_shows_exactly_that_and_announces_it_once(world):
    data = change({"kind": "channel", "id": world.channel_id, "mode": "release"}, {"kind": "programme", "id": world.programme["quokka"], "mode": "release"})
    assert data["result"]["shown"] == 1
    assert visible_names(world.session) == {FIRST["name"]}
    assert new_on_air(world.session) == ["CH 1 · Mighty Street has 1 new programme."]
    again = change({"kind": "channel", "id": world.channel_id, "mode": "release"}, {"kind": "programme", "id": world.programme["quokka"], "mode": "release"})
    assert again["result"] == {"changed": 0, "shown": 0, "hidden": 0}
    assert len(new_on_air(world.session)) == 1


def test_03_the_player_solves_it_and_the_withheld_one_is_still_a_404(world):
    status, out = post_json(world.session, "/api/v1/challenges/attempt", {"challenge_id": world.ids.first, "submission": FIRST["flag"]})
    assert status == 200 and out["data"]["status"] == "correct", out
    status, _ = get_json(world.session, f"/api/v1/challenges/{world.ids.second}")
    assert status == 404


def test_04_a_scheduled_drop_appears_for_the_first_request_after_its_second_and_is_announced_once_however_many_arrive_together(world):
    due = int(time.time()) + 9
    change({"kind": "programme", "id": world.programme["numbat"], "mode": "schedule", "at": due})
    assert visible_names(world.session) == {FIRST["name"]}, "scheduled is not yet on air"
    while time.time() < due - 0.3:
        time.sleep(0.1)

    def look(_):
        end = time.time() + 4
        seen = False
        while time.time() < end and not seen:
            seen = SECOND["name"] in visible_names(world.session)
        return seen

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(look, range(12)))
    assert all(results), "every one of the watchers saw it within four seconds of its second"
    notes = new_on_air(world.session)
    assert notes.count("CH 1 · Mighty Street has 1 new programme.") == 2, notes  # the first release and this drop: one line each, not twelve
    status, view = admin("GET", RELEASE)
    drops = [l for l in view["data"]["audit"] if l["action"] == "release.drop" and l["actor"] == "system"]
    assert len(drops) == 1, view["data"]["audit"]


def test_05_a_programme_pulled_back_leaves_every_list_and_its_flag_records_nothing(world):
    status, before = admin("GET", f"/api/v1/submissions?challenge_id={world.ids.first}")
    change({"kind": "programme", "id": world.programme["quokka"], "mode": "withhold"}, reason="found a leak")
    assert visible_names(world.session) == {SECOND["name"]}
    for path in (f"/api/v1/teams/{world.team_id}/solves", f"/api/v1/users/{world.user_id}/solves", "/api/v1/teams/me/solves", "/api/v1/users/me/solves", f"/teams/{world.team_id}", "/team"):
        status, body = get_json(world.session, path)
        assert status == 200
        assert FIRST["name"] not in (json.dumps(body) if not isinstance(body, str) else body), path
    status, answer = post_json(world.session, "/api/v1/challenges/attempt", {"challenge_id": world.ids.first, "submission": FIRST["flag"]})
    status_gone, answer_gone = post_json(world.session, "/api/v1/challenges/attempt", {"challenge_id": 99999, "submission": FIRST["flag"]})
    assert status == status_gone and answer == answer_gone
    status, after = admin("GET", f"/api/v1/submissions?challenge_id={world.ids.first}")
    assert after["meta"]["pagination"]["total"] == before["meta"]["pagination"]["total"], "nothing was recorded"
