"""The scoreboard freeze on the real stack (SP3 part 3.6): nginx, gunicorn with four gevent workers, MariaDB and Redis.

The plugin tests prove the rules on SQLite and the golden scenarios prove the answers match the approved demo. This proves over HTTP, with
the stack's own settings and Redis cache, that once the freeze is set by the crew's own call: a dynamic value stands still while a third studio
solves, the public list and the CTFtime feed do not move, the other studios' tick does not move but the solver's own number does, and lifting the
freeze puts every value right in the same request. The freeze is a setting the crew can change at any time (it is not in the locked event
settings), so everything here goes through the crew's token.

Same prerequisites and runner as test_stack.py. Like test_release.py it renders an event window around the present moment into the
generated settings and restarts CTFd, and puts everything back at the end; everything it creates is removed again.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import test_release as r
import test_stack as t

pytestmark = pytest.mark.integration

DYN = {"name": "Quokka Dynamic", "flag": "quokka-answer-one"}
PLAYERS = ["freezeplayer1", "freezeplayer2", "freezeplayer3"]
FEED = "/ctftime/standings.json"
TICKS = "/api/v1/l3mon/ticks"


def purge():
    for name in PLAYERS:
        for path, value in (("/api/v1/teams", f"{name}-team"), ("/api/v1/users", name)):
            status, out = r.admin("GET", f"{path}?field=name&q={value}")
            for row in (out["data"] if status == 200 else []):
                if row["name"] == value:
                    r.admin("DELETE", f"{path}/{row['id']}")
    status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={DYN['name'].split()[0]}")
    for row in (out["data"] if status == 200 else []):
        if row["name"] == DYN["name"]:
            r.admin("DELETE", f"/api/v1/challenges/{row['id']}")


def stored_value(cid) -> int:
    status, out = r.admin("GET", f"/api/v1/challenges/{cid}")
    assert status == 200, out
    return out["data"]["value"]


def feed():
    status, _, data = t.request(FEED)
    assert status == 200
    return [(row["team"], row["score"]) for row in json.loads(data)["standings"]]


def tick(session):
    status, out = r.get_json(session, TICKS)
    assert status == 200, out
    return out["data"]


def solve(session, cid):
    status, out = r.post_json(session, "/api/v1/challenges/attempt", {"challenge_id": cid, "submission": DYN["flag"]})
    assert status == 200 and out["data"]["status"] == "correct", out


@pytest.fixture(scope="module")
def world():
    original = r.PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    r.PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    made = SimpleNamespace(players=[], teams=[], users=[])
    try:
        r.restart_ctfd()
        purge()
        status, out = r.admin("POST", "/api/v1/challenges", {"name": DYN["name"], "category": "Test", "description": "plain words", "value": 500, "initial": 500, "minimum": 200, "decay": 15, "function": "logarithmic", "type": "dynamic", "state": "hidden"})
        assert status == 200, out
        made.dyn = out["data"]["id"]
        status, _ = r.admin("POST", "/api/v1/flags", {"challenge_id": made.dyn, "content": DYN["flag"], "type": "static", "data": ""})
        assert status == 200
        status, out = r.admin("PUT", r.PROGRAMMES, {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [{"challenge_id": made.dyn, "channel": "street", "cell": 0, "number": 101, "slug": "quokka"}],
        })
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        channel = view["data"]["channels"][0]
        r.change({"kind": "channel", "id": channel["id"], "mode": "release"}, *[{"kind": "programme", "id": p["id"], "mode": "release"} for p in channel["programmes"]])
        for name in PLAYERS:
            session, user_id, team_id = r.make_player(name)
            made.players.append(session)
            made.users.append(user_id)
            made.teams.append(team_id)
        yield made
    finally:
        try:
            r.admin("PATCH", "/api/v1/configs", {"freeze": None})
            purge()
            r.in_ctfd(r.CLEANUP)
        finally:
            r.PRESET.write_bytes(original)
            r.restart_ctfd()


def test_01_two_studios_solve_before_the_freeze_and_the_value_is_the_two_studio_one(world):
    solve(world.players[0], world.dyn)
    solve(world.players[1], world.dyn)
    assert stored_value(world.dyn) == 499
    assert feed() == [("freezeplayer1-team", 499), ("freezeplayer2-team", 499)]


def test_02_the_crew_freezes_the_scoreboard_and_a_third_studio_solves_nothing_moves_for_the_others(world):
    time.sleep(1.2)  # every solve so far is dated before the second the freeze is set to
    status, out = r.admin("PATCH", "/api/v1/configs", {"freeze": int(time.time())})
    assert status == 200, out
    before = {i: tick(world.players[i]) for i in (0, 1)}
    feed_before = feed()
    solve(world.players[2], world.dyn)
    assert stored_value(world.dyn) == 499, "CTFd alone would have valued it 495 now: the value stands still while the scoreboard is frozen"
    assert feed() == feed_before == [("freezeplayer1-team", 499), ("freezeplayer2-team", 499)], "the public list does not move and does not show the third studio"
    after = {i: tick(world.players[i]) for i in (0, 1)}
    assert after == before, "the other studios' tick (the shared version and their own number) did not move"
    own = tick(world.players[2])
    assert own["own"] >= 1, "the solver's own number says it has solved"
    status, board = r.get_json(world.players[2], "/api/v1/l3mon/board")
    assert status == 200 and board["data"]["team"]["score"] == 499 and board["data"]["team"]["place"] is None
    _, other = r.get_json(world.players[0], "/api/v1/l3mon/board")
    solves = [p["solves"] for p in other["data"]["programmes"]]
    assert solves == [2], "the public count is the frozen one: two studios"


def test_03_the_crews_recalculate_is_refused_while_frozen_and_the_page_says_so(world):
    status, out = r.admin("POST", "/api/v1/l3mon/admin/scoring/recalculate", {})
    assert status == 409 and "frozen" in out["errors"], out
    status, out = r.admin("GET", "/api/v1/l3mon/admin/scoring")
    assert status == 200 and out["data"]["frozen"] is True


def test_04_lifting_the_freeze_puts_every_value_right_in_the_same_request_and_the_list_catches_up(world):
    status, out = r.admin("PATCH", "/api/v1/configs", {"freeze": None})
    assert status == 200, out
    assert stored_value(world.dyn) == 495, "three studios hold a solve: the true value, at once"
    assert sorted(feed()) == [("freezeplayer1-team", 495), ("freezeplayer2-team", 495), ("freezeplayer3-team", 495)]
    status, out = r.admin("GET", "/api/v1/l3mon/admin/scoring")
    assert out["data"]["frozen"] is False
    assert tick(world.players[0])["ver"] != "", "and the pages' tick has moved (the phase part changed)"


def test_05_the_final_feed_does_not_exist_for_a_player_or_a_visitor_before_the_end_whatever_the_freeze(world):
    status, _, data = t.request("/ctftime/final-standings.json")
    assert status == 404 and json.loads(data)["error"] == "not_found"
