"""The Guide, the scoreboard and the bell on the real stack (SP3 part 3.5): nginx, gunicorn with four gevent workers, MariaDB and Redis.

The plugin tests prove the rules on SQLite and the golden scenarios prove the answers match the approved demo. This proves the same answers
arrive over HTTP through nginx with the stack's own settings and Redis cache: a visitor is told to sign in, two studios get their own Guide and
grid, the scoreboard is the CTFtime feed row for row, the bell carries a studio's private line to that studio alone, CTFd's own open notification
routes are closed to players, and the answers give a 304 to their own ETag.

Same prerequisites and runner as test_board.py, whose world (an event window around the present moment, one channel, three programmes of which one
is held back, two studios) it reuses. Everything it creates is removed again.
"""
from __future__ import annotations

import json
import re
import time

import pytest

import test_release as r
import test_stack as t
from test_board import SHOWN, ask, world  # noqa: F401  (the fixture is the point)

pytestmark = pytest.mark.integration

GUIDE, EPG = "/api/v1/l3mon/guide", "/api/v1/l3mon/guide/epg"
SCOREBOARD, ROWS = "/api/v1/l3mon/scoreboard", "/api/v1/l3mon/scoreboard/rows"
BELL = "/api/v1/notifications"
BONUS = "/api/v1/l3mon/admin/scoring/bonus"
PRIVATE = "Thanks for the careful write-up"


def raw(session, path, headers=None):
    """GET as a signed-in session: (status, headers, text)."""
    response = session.open(path, headers=headers)
    return response.status, dict(response.headers.items()), response.read().decode("utf-8")


def test_01_a_visitor_is_told_to_sign_in_and_a_studio_gets_its_guide(world):
    for path in (GUIDE, EPG, SCOREBOARD, ROWS, BELL):
        status, _, data = t.request(path)
        assert status == 401 and json.loads(data)["error"] == "auth_required", path
    status, headers, body = ask(world.players[0], GUIDE)
    assert status == 200 and body["success"] is True
    assert headers["Cache-Control"] == "private, no-cache" and re.fullmatch(r'"g[A-Za-z0-9_-]{22}"', headers["ETag"]) and "Cookie" in headers.get("Vary", "")
    data = body["data"]
    assert list(data) == ["ver", "phase", "banner", "epg_sig", "team", "story"]
    team = data["team"]
    assert (team["score"], team["place"], team["solves"], team["hints_used"], team["instances_live"], team["bonus"], team["notes"]) == (0, None, 0, 0, 0, 0, [])
    assert [(m["name"], m["captain"], m["you"], m["solves"], m["trp"]) for m in team["members"]] == [("boardplayer1", True, True, 0, 0)]
    assert [(c["name"], c["solved"], c["total"], c["sponsor"]) for c in team["by_channel"]] == [("Marsh Channel", 0, 3, None)]
    assert data["story"] == {"reels": 0, "reels_needed": 1, "on_air": 0}
    text = json.dumps(body)
    assert "Egret" not in text and "egret_secret" not in text and SHOWN["flag"] not in text


def test_02_the_grid_shows_what_is_on_air_and_only_counts_what_is_not(world):
    status, headers, text = raw(world.players[0], EPG)
    assert status == 200 and headers["Content-Type"].startswith("text/html") and headers["Cache-Control"] == "private, no-cache"
    assert "Heron Ledger" in text and "Ibis Lantern" in text and "Marsh Channel" in text and "CH 03" in text
    assert "1 coming up" in text and "2 on air" in text
    assert "Egret" not in text and "egret_secret" not in text and "insane" not in text.lower()
    _, _, guide = ask(world.players[0], GUIDE)
    assert headers["ETag"] == f'"e{guide["data"]["epg_sig"]}"'
    status, again, empty = raw(world.players[0], EPG, {"If-None-Match": headers["ETag"]})
    assert status == 304 and empty == "" and again.get("ETag") == headers["ETag"]


def test_03_a_solve_moves_the_guide_and_the_scoreboard_and_the_scoreboard_is_the_feed(world):
    status, out = r.post_json(world.players[0], "/api/v1/challenges/attempt", {"challenge_id": world.ids.shown, "submission": SHOWN["flag"]})
    assert status == 200 and out["data"]["status"] == "correct"
    _, headers, body = ask(world.players[0], GUIDE)
    team = body["data"]["team"]
    assert (team["score"], team["solves"], team["place"]) == (150, 1, 1)
    assert [(m["trp"], m["solves"], m["pct"]) for m in team["members"]] == [(150, 1, 100)]
    assert body["data"]["story"]["reels"] == 1
    status, _, _ = ask(world.players[0], GUIDE, {"If-None-Match": headers["ETag"]})
    assert status == 304
    _, _, other = ask(world.players[1], GUIDE)
    assert other["data"]["team"]["score"] == 0 and other["data"]["team"]["members"][0]["name"] == "boardplayer2", "a different studio, its own Guide"
    assert "boardplayer1" not in json.dumps(other)
    # the scoreboard
    _, headers, body = ask(world.players[0], SCOREBOARD)
    data = body["data"]
    assert re.fullmatch(r'"s[A-Za-z0-9_-]{22}"', headers["ETag"]) and data["me"] == {"name": "boardplayer1-team", "score": 150, "pos": 1, "of": data["total"]}
    status, _, markup = raw(world.players[0], ROWS)
    rows = re.findall(r'<li class="sb-row[^"]*"><b class="sb-pos">(\d+)</b><span class="sb-name"><bdi>(.*?)</bdi>.*?<b>(-?\d+)</b> TRP', markup)
    deadline = time.time() + 40  # the feed may be kept for 15 seconds by a cache in front of it; the scoreboard never is
    while True:
        status_feed, _, feed = t.request("/ctftime/standings.json")
        assert status_feed == 200
        standings = [(str(row["pos"]), row["team"], str(row["score"])) for row in json.loads(feed)["standings"]]
        if rows == standings or time.time() > deadline:
            break
        time.sleep(2)
    assert rows == standings and rows[0] == ("1", "boardplayer1-team", "150"), "the scoreboard is the CTFtime feed, row for row"
    assert "Egret" not in markup and "@" not in markup
    status, _, empty = raw(world.players[0], ROWS, {"If-None-Match": f'"r{data["rows_sig"]}"'})
    assert status == 304 and empty == ""


def test_04_the_bell_carries_a_private_line_to_its_studio_alone_and_the_stock_doors_are_closed(world):
    status, out = r.admin("POST", BONUS, {"team_id": world.teams[0], "trp": 25, "message": PRIVATE})
    assert status == 200, out
    status, _, mine = ask(world.players[0], BELL)
    assert status == 200 and [line["content"] for line in mine["data"]][-1] == PRIVATE
    assert all(set(line) == {"id", "title", "content"} for line in mine["data"]), "no date, no studio, no account"
    _, _, theirs = ask(world.players[1], BELL)
    assert PRIVATE not in json.dumps(theirs)
    newest = mine["data"][-1]["id"]
    _, _, nothing = ask(world.players[0], f"{BELL}?since_id={newest}")
    assert nothing["data"] == []
    status, _, _ = ask(world.players[0], f"{BELL}?since_id=abc")
    assert status == 400
    _, _, guide = ask(world.players[0], GUIDE)
    assert guide["data"]["team"]["bonus"] == 25 and guide["data"]["team"]["notes"][0]["content"] == PRIVATE
    _, _, other_guide = ask(world.players[1], GUIDE)
    assert PRIVATE not in json.dumps(other_guide)
    status, _, text = raw(world.players[0], "/notifications")
    assert status == 404, "CTFd's own page lists every notification; it is closed to players"
    for odd in ("1", "+1", "1.0", "1x"):
        status, _, text = raw(world.players[0], f"{BELL}/{odd}")
        assert status == 404 and "boardplayer" not in text and "team_id" not in text, odd
    status, _, text = raw(world.players[0], "/events")
    assert status == 404, "the event stream would push a line meant for one studio to everyone"
    # the crew keeps CTFd's own list, and the studio's private line is not in it
    status, out = r.admin("GET", BELL)
    assert status == 200 and PRIVATE not in json.dumps(out)
    # the tick follows the bell, for the studio that was told and for nobody else
    _, _, tick_one = ask(world.players[0], "/api/v1/l3mon/ticks")
    _, _, tick_two = ask(world.players[1], "/api/v1/l3mon/ticks")
    assert tick_one["data"]["notif_ver"] != tick_two["data"]["notif_ver"]


def test_05_a_bought_hint_tells_the_studio_its_score_and_what_it_cost(world):
    status, out = r.admin("POST", "/api/v1/hints", {"challenge_id": world.ids.shown, "content": "look closer", "cost": 20})
    assert status == 200, out
    status, answer = r.post_json(world.players[0], "/api/v1/unlocks", {"target": out["data"]["id"], "type": "hints"})
    assert status == 200, answer
    assert answer["data"]["l3mon"] == {"score": 150 + 25 - 20, "cost": 20}, answer
    assert "look closer" not in json.dumps(answer)
    _, _, guide = ask(world.players[0], GUIDE)
    assert guide["data"]["team"]["hints_used"] == 1 and guide["data"]["team"]["score"] == 155


def test_06_none_of_the_new_routes_can_write(world):
    for path in (GUIDE, EPG, SCOREBOARD, ROWS):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            status, _, _ = t.request(path, method=method, headers={"Content-Type": "application/json"}, body=b"{}")
            assert status in (401, 403, 404, 405), (method, path, status)
