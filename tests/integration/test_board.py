"""The board on the real stack (SP3 part 3.4): nginx, gunicorn with four gevent workers, MariaDB and Redis.

The plugin tests prove the rules on SQLite and the golden scenarios prove the answers match the approved demo. This proves the same
answers arrive over HTTP through nginx, with the stack's own settings and Redis cache: two studios and a visitor ask the board and
the tick; a withheld programme is absent; the ETag gives a 304 and one studio's board is never served to another; a correct flag
carries the numbers of the reply; the panel carries its block.

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

BOARD = "/api/v1/l3mon/board"
TICKS = "/api/v1/l3mon/ticks"
SHOWN = {"name": "Heron Ledger", "flag": "heron-answer-one", "value": 150}
SECOND = {"name": "Ibis Lantern", "flag": "ibis-answer-two", "value": 250}
HELD = {"name": "Egret Secret", "flag": "egret-answer-three", "value": 350}
PLAYERS = ["boardplayer1", "boardplayer2"]


def purge():
    for name in PLAYERS:
        for path, value in (("/api/v1/teams", f"{name}-team"), ("/api/v1/users", name)):
            status, out = r.admin("GET", f"{path}?field=name&q={value}")
            for row in (out["data"] if status == 200 else []):
                if row["name"] == value:
                    r.admin("DELETE", f"{path}/{row['id']}")
    for spec in (SHOWN, SECOND, HELD):
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={spec['name'].split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == spec["name"]:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")


def ask(session, path, headers=None):
    """GET as a signed-in session: (status, headers, parsed body or None)."""
    response = session.open(path, headers=headers)
    raw = response.read()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return response.status, dict(response.headers.items()), body


@pytest.fixture(scope="module")
def world():
    original = r.PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    r.PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    made = SimpleNamespace(players=[], users=[], teams=[])
    try:
        r.restart_ctfd()
        purge()
        shown, second, held = (r.make_challenge(spec) for spec in (SHOWN, SECOND, HELD))
        made.ids = SimpleNamespace(shown=shown, second=second, held=held)
        status, out = r.admin("PUT", r.PROGRAMMES, {
            "channels": [{"slug": "marsh", "name": "Marsh Channel", "position": 3, "synopsis": "Reeds and quiet water.", "accent": "ch3", "picture_key": "marsh"}],
            "programmes": [
                {"challenge_id": shown, "channel": "marsh", "cell": 0, "number": 301, "slug": "heron_ledger", "difficulty": "easy"},
                {"challenge_id": second, "channel": "marsh", "cell": 1, "number": 302, "slug": "ibis_lantern", "difficulty": "hard", "delivery": "live_single"},
                {"challenge_id": held, "channel": "marsh", "cell": 2, "number": 303, "slug": "egret_secret", "difficulty": "insane"},
            ],
        })
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        channel = view["data"]["channels"][0]
        slugs = {p["slug"]: p["id"] for p in channel["programmes"]}
        r.change({"kind": "channel", "id": channel["id"], "mode": "release"}, {"kind": "programme", "id": slugs["heron_ledger"], "mode": "release"}, {"kind": "programme", "id": slugs["ibis_lantern"], "mode": "release"})
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


def test_01_a_visitor_is_told_to_sign_in_in_json_and_a_studio_gets_its_board(world):
    status, _, data = t.request(BOARD)
    assert status == 401 and json.loads(data)["error"] == "auth_required" and json.loads(data)["request_id"]
    status, headers, body = ask(world.players[0], BOARD)
    assert status == 200 and body["success"] is True
    data = body["data"]
    assert headers["Cache-Control"] == "private, no-cache" and headers["ETag"].startswith('"b') and headers["X-Request-Id"]
    assert "Cookie" in headers.get("Vary", ""), "the answer is about one studio: nothing may share it"
    assert data["phase"] == {"state": "live", "frozen": False}
    assert [c["name"] for c in data["channels"]] == ["Marsh Channel"] and data["channels"][0]["no"] == 3
    assert [p["slug"] for p in data["programmes"]] == ["heron_ledger", "ibis_lantern"], "the held-back programme is absent"
    assert data["team"]["name"] == "boardplayer1-team" and data["team"]["score"] == 0 and data["team"]["place"] is None
    channel = data["channels"][0]
    assert (channel["total"], channel["on_air"], channel["coming"]) == (3, 2, 1)
    ibis = [p for p in data["programmes"] if p["slug"] == "ibis_lantern"][0]
    assert (ibis["difficulty"], ibis["live"], ibis["value"]) == ("hard", True, 250)
    text = json.dumps(body)
    assert "Egret" not in text and "egret_secret" not in text and "story" not in text and SHOWN["flag"] not in text


def test_02_the_board_answers_304_to_its_own_etag_through_nginx_and_never_to_another_studios(world):
    _, headers, _ = ask(world.players[0], BOARD)
    etag = headers["ETag"]
    status, again, _ = ask(world.players[0], BOARD, {"If-None-Match": etag})
    assert status == 304 and again.get("ETag") == etag
    status, other_headers, other = ask(world.players[1], BOARD, {"If-None-Match": etag})
    assert status == 200 and other["data"]["team"]["name"] == "boardplayer2-team", "a different studio gets its own board, not the first one's tag"


def test_03_a_correct_flag_carries_the_numbers_of_the_reply_and_the_board_follows(world):
    status, out = r.post_json(world.players[0], "/api/v1/challenges/attempt", {"challenge_id": world.ids.shown, "submission": SHOWN["flag"]})
    assert status == 200 and out["data"]["status"] == "correct"
    extra = out["data"]["l3mon"]
    assert extra == {"value": 150, "reel": 1, "reels_needed": 1, "channel_signal": 0.33, "channel_complete": False, "first_blood": True, "solves": 1}, extra
    status, out = r.post_json(world.players[1], "/api/v1/challenges/attempt", {"challenge_id": world.ids.shown, "submission": SHOWN["flag"]})
    assert out["data"]["l3mon"]["first_blood"] is False and out["data"]["l3mon"]["solves"] == 2
    _, _, one = ask(world.players[0], BOARD)
    assert one["data"]["team"]["score"] == 150 and [p["solved_by_me"] for p in one["data"]["programmes"]] == [True, False]
    assert one["data"]["team"]["place"] in (1, 2)


def test_04_a_wrong_flag_and_the_panel(world):
    status, out = r.post_json(world.players[0], "/api/v1/challenges/attempt", {"challenge_id": world.ids.second, "submission": "nope"})
    assert status == 200 and out["data"]["status"] == "incorrect" and "l3mon" not in out["data"], "this challenge does not limit tries"
    status, _, body = ask(world.players[0], f"/api/v1/challenges/{world.ids.second}")
    assert status == 200
    block = body["data"]["l3mon"]
    assert (block["slug"], block["difficulty"], block["live"], block["channel_name"], block["score"]) == ("ibis_lantern", "hard", True, "Marsh Channel", 150)
    status, _, body = ask(world.players[0], f"/api/v1/challenges/{world.ids.held}")
    assert status == 404, "a held-back programme is a 404 for a studio"


def test_05_the_tick_moves_for_everybody_and_own_only_for_the_studio_that_solved(world):
    _, _, one = ask(world.players[0], TICKS)
    _, _, two = ask(world.players[1], TICKS)
    assert set(one["data"]) == {"ver", "notif_ver", "own"}
    assert one["data"]["own"] >= 1 and two["data"]["own"] >= 1
    before = one["data"]["ver"]
    status, out = r.post_json(world.players[1], "/api/v1/challenges/attempt", {"challenge_id": world.ids.second, "submission": SECOND["flag"]})
    assert status == 200 and out["data"]["status"] == "correct"
    _, _, one_after = ask(world.players[0], TICKS)
    _, _, two_after = ask(world.players[1], TICKS)
    assert one_after["data"]["ver"] != before, "every studio's page is told to ask again"
    assert one_after["data"]["own"] == one["data"]["own"] and two_after["data"]["own"] > two["data"]["own"]


def test_06_a_hint_cannot_be_bought_by_a_player_after_the_crew_pauses_the_event(world):
    status, out = r.admin("POST", "/api/v1/hints", {"challenge_id": world.ids.shown, "content": "a hint", "cost": 1})
    assert status == 200, out
    hint = out["data"]["id"]
    status, _ = r.admin("PATCH", "/api/v1/configs", {"paused": True})
    assert status == 200
    try:
        status, answer = r.post_json(world.players[0], "/api/v1/unlocks", {"target": hint, "type": "hints"})
        assert status == 403 and answer["error"] == "phase_closed" and answer["phase"] == "paused", (status, answer)
        status, answer = r.post_json(world.players[0], "/api/v1/challenges/attempt", {"challenge_id": world.ids.second, "submission": "x"})
        assert status == 403 and answer["data"]["l3mon"] == {"reason": "paused"}, (status, answer)
    finally:
        r.admin("PATCH", "/api/v1/configs", {"paused": False})
    status, answer = r.post_json(world.players[0], "/api/v1/unlocks", {"target": hint, "type": "hints"})
    assert status == 200, answer


def test_07_the_board_part_adds_no_way_to_write(world):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, _, _ = t.request(BOARD, method=method, headers={"Content-Type": "application/json"}, body=b"{}")
        assert status in (401, 403, 404, 405), (method, status)
