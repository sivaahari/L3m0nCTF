"""Scoring on the real stack (SP3 part 3.3): nginx, gunicorn with four gevent workers, MariaDB and Redis.

The plugin tests prove the rules on SQLite and on a real MariaDB. This proves the same behaviour end to end, over HTTP, with the stack's
own settings and its Redis cache, where a cache that is cleared too early or never would show: three studios solve a dynamic and a fixed
challenge; a ban and an unban move the dynamic value at once; Revoke takes the TRP off every studio and Restore returns the exact
totals; a bonus counts and only its title is public; the hint error says TRP; and the crew's calls are refused to a player.

Same prerequisites and runner as test_stack.py. Like test_release.py it renders an event window around the present moment into the
generated settings and restarts CTFd, and puts everything back at the end; everything it creates is removed again.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import test_release as r
import test_stack as t

pytestmark = pytest.mark.integration

SCORING = "/api/v1/l3mon/admin/scoring"
DYN = {"name": "Wombat Dynamic", "flag": "wombat-answer-one"}
FIX = {"name": "Numbat Fixed", "flag": "numbat-answer-two", "value": 100}
PLAYERS = ["scoreplayer1", "scoreplayer2", "scoreplayer3"]
REASON = "The checker accepted a wrong answer"
MESSAGE = "Found a bug in the lobby"


def purge():
    for name in PLAYERS:
        for path, value in (("/api/v1/teams", f"{name}-team"), ("/api/v1/users", name)):
            status, out = r.admin("GET", f"{path}?field=name&q={value}")
            for row in (out["data"] if status == 200 else []):
                if row["name"] == value:
                    r.admin("DELETE", f"{path}/{row['id']}")
    for spec in (DYN, FIX):
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={spec['name'].split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == spec["name"]:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")


def crew(path: str, body=None, method="POST"):
    return r.admin(method, f"{SCORING}{path}", body)


def scores(session) -> dict:
    status, out = r.get_json(session, "/api/v1/scoreboard")
    assert status == 200, out
    return {row["name"]: row["score"] for row in out["data"]}


def value_of(session, cid) -> int:
    status, out = r.get_json(session, f"/api/v1/challenges/{cid}")
    assert status == 200, out
    return out["data"]["value"]


@pytest.fixture(scope="module")
def world():
    original = r.PRESET.read_bytes()
    window = json.loads(original)
    import time

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
        made.fix = r.make_challenge({"name": FIX["name"], "flag": FIX["flag"], "value": FIX["value"]})
        status, _ = r.admin("POST", "/api/v1/flags", {"challenge_id": made.dyn, "content": DYN["flag"], "type": "static", "data": ""})
        assert status == 200
        status, out = r.admin("PUT", r.PROGRAMMES, {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {"challenge_id": made.dyn, "channel": "street", "cell": 0, "number": 101, "slug": "wombat"},
                {"challenge_id": made.fix, "channel": "street", "cell": 1, "number": 102, "slug": "numbat"},
            ],
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
            purge()
            r.in_ctfd(r.CLEANUP)
        finally:
            r.PRESET.write_bytes(original)
            r.restart_ctfd()


def solve(session, cid, flag):
    status, out = r.post_json(session, "/api/v1/challenges/attempt", {"challenge_id": cid, "submission": flag})
    assert status == 200 and out["data"]["status"] == "correct", out


def test_01_three_studios_solve_and_every_one_is_paid_the_same_falling_value(world):
    seen = []
    for session in world.players:
        solve(session, world.dyn, DYN["flag"])
        seen.append(value_of(world.players[0], world.dyn))
    assert seen == [500, 499, 495]
    solve(world.players[0], world.fix, FIX["flag"])
    assert scores(world.players[0]) == {"scoreplayer1-team": 595, "scoreplayer2-team": 495, "scoreplayer3-team": 495}


def test_02_a_ban_and_an_unban_move_the_value_in_the_same_request_even_through_the_cache(world):
    team = world.teams[2]
    status, out = r.admin("PATCH", f"/api/v1/teams/{team}", {"banned": True})
    assert status == 200, out
    assert value_of(world.players[0], world.dyn) == 499, "two studios count now, and the player's cached challenge list was cleared"
    assert scores(world.players[0]) == {"scoreplayer1-team": 599, "scoreplayer2-team": 499}
    status, out = r.admin("PATCH", f"/api/v1/teams/{team}", {"banned": False})
    assert status == 200, out
    assert value_of(world.players[0], world.dyn) == 495
    assert scores(world.players[0]) == {"scoreplayer1-team": 595, "scoreplayer2-team": 495, "scoreplayer3-team": 495}
    status, view = crew("", method="GET")
    assert status == 200 and not [a for a in view["data"]["audit"] if a["action"] == "scoring.heal"], "the request fixed it; the minute check had nothing to do"


def test_03_revoke_takes_the_trp_off_every_studio_and_nothing_public_says_why(world):
    before = scores(world.players[0])
    status, out = crew("/revoke", {"challenge_id": world.dyn, "reason": REASON})
    assert status == 200 and out["data"]["voided"] == 3 and (out["data"]["value_before"], out["data"]["value_after"]) == (495, 500), out
    assert value_of(world.players[0], world.dyn) == 500
    after = scores(world.players[0])
    assert after == {"scoreplayer1-team": 100}, after  # only the fixed challenge is left; CTFd's board lists studios with something
    for session in world.players:
        status, notifications = r.get_json(session, "/api/v1/notifications")
        assert [line["content"] for line in notifications["data"]].count(REASON) == 1, "each studio that lost a solve reads the crew's words, in its own bell and nowhere else"
        status, mine = r.get_json(session, "/api/v1/teams/me/solves")
        assert DYN["name"] not in json.dumps(mine)
    status, _, public = t.request("/api/v1/notifications")
    assert status == 401 and REASON not in public.decode("utf-8", "replace"), "a visitor gets nothing"
    status, stock = r.admin("GET", "/api/v1/notifications")
    assert status == 200 and REASON not in json.dumps(stock), "the crew's own list is CTFd's public one: the studios' lines are not in it"
    status, view = crew("", method="GET")
    assert [v["outcome"] for v in view["data"]["voids"]] == ["open"] * 3 and view["data"]["audit"][0]["action"] == "scoring.revoke"
    assert before != after


def test_04_restore_returns_the_exact_totals(world):
    status, out = crew("/restore", {"challenge_id": world.dyn, "reason": "the checker is fixed"})
    assert status == 200 and out["data"]["restored"] == 3, out
    assert value_of(world.players[0], world.dyn) == 495
    assert scores(world.players[0]) == {"scoreplayer1-team": 595, "scoreplayer2-team": 495, "scoreplayer3-team": 495}
    status, again = crew("/restore", {"challenge_id": world.dyn})
    assert status == 409, again


def test_05_a_bonus_counts_and_only_its_title_is_public(world):
    status, out = crew("/bonus", {"team_id": world.teams[1], "trp": 50, "message": MESSAGE})
    assert status == 200 and out["data"]["title"] == "Bonus +50 TRP", out
    assert scores(world.players[0]) == {"scoreplayer1-team": 595, "scoreplayer2-team": 545, "scoreplayer3-team": 495}
    for number, session in enumerate(world.players):
        status, awards = r.get_json(session, f"/api/v1/teams/{world.teams[1]}/awards")
        assert status == 200
        assert [(a["name"], a["value"], a["description"]) for a in awards["data"]] == [("Bonus +50 TRP", 50, "")]
        assert MESSAGE not in json.dumps(awards)
        status, notifications = r.get_json(session, "/api/v1/notifications")
        assert (MESSAGE in json.dumps(notifications)) == (number == 1), "only the studio that was given the bonus reads the message, in its own bell"
    status, _, public = t.request("/api/v1/notifications")
    assert MESSAGE not in public.decode("utf-8", "replace")
    status, again = crew("/bonus", {"team_id": world.teams[1], "trp": 50, "message": MESSAGE})
    assert status == 409, again


def test_06_the_hint_error_says_trp(world):
    status, out = r.admin("POST", "/api/v1/hints", {"challenge_id": world.fix, "content": "a hint", "cost": 99999})
    assert status == 200, out
    status, answer = r.post_json(world.players[2], "/api/v1/unlocks", {"target": out["data"]["id"], "type": "hints"})
    assert status == 400 and answer["errors"]["score"] == "You do not have enough TRP to unlock this hint", answer


def test_07_the_crews_calls_and_page_are_refused_to_a_player(world):
    session = world.players[0]
    status, answer = r.post_json(session, f"{SCORING}/revoke", {"challenge_id": world.dyn, "reason": REASON})
    assert status in (302, 403), (status, answer)
    status, _ = r.get_json(session, SCORING)
    assert status in (302, 403)
    response = session.open("/admin/l3mon/scoring")
    assert response.status in (302, 403)
    status, view = crew("", method="GET")
    assert [v["outcome"] for v in view["data"]["voids"]] == ["restored"] * 3, "nothing changed"
