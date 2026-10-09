"""CTFd memoizes the standings, a team's score and the challenge list. After a crew action the very next read must show the new
numbers, whether or not a dynamic value happened to change (found by the independent audit: a fixed challenge, or a dynamic one with a
single solver, left every cache stale for up to five minutes, while the tick told clients to refetch at once).

The caches are warmed through real player requests and read again through the same routes. Nothing here calls clear_standings() or
clear_challenges() by hand, which is what the older helpers do and why they could not see this.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
from types import SimpleNamespace

import pytest

from scoring_world import attempt, dynamic, fixed, make_app, team_client
from tests.helpers import destroy_ctfd, login_as_user

BASE = "/api/v1/l3mon/admin/scoring"


@pytest.fixture(params=["fixed", "dynamic with one solver"])
def world(request):
    app = make_app()
    with app.app_context():
        admin = login_as_user(app, "admin")
        challenge = fixed("fix", value=100, flag="F") if request.param == "fixed" else dynamic("dyn", flag="F")
        cid, worth = challenge.id, (100 if request.param == "fixed" else 500)
        a = team_client(app, "alice", "studio-a")
        assert attempt(a, cid, "F") == "correct"
        yield SimpleNamespace(app=app, admin=admin, a=a, cid=cid, worth=worth)
    destroy_ctfd(app)


def read(world):
    """What the signed-in studio sees on the routes CTFd caches."""
    board = {row["name"]: row["score"] for row in world.a.get("/api/v1/scoreboard").get_json()["data"]}
    me = world.a.get("/api/v1/teams/me").get_json()["data"]
    listed = {c["id"]: c for c in world.a.get("/api/v1/challenges").get_json()["data"]}[world.cid]
    return board, me["score"], listed["solved_by_me"], listed["solves"]


def test_revoke_and_restore_are_visible_on_the_very_next_read_even_when_the_value_does_not_move(world):
    assert read(world) == ({"studio-a": world.worth}, world.worth, True, 1), "the caches are warm now"
    r = world.admin.post(f"{BASE}/revoke", json={"challenge_id": world.cid, "reason": "the checker broke"})
    assert r.status_code == 200 and r.get_json()["data"]["value_before"] == r.get_json()["data"]["value_after"], "the value did not move"
    assert read(world) == ({}, 0, False, 0), "the board, the studio's score and the challenge list all show it at once"
    r = world.admin.post(f"{BASE}/restore", json={"challenge_id": world.cid})
    assert r.status_code == 200
    assert read(world) == ({"studio-a": world.worth}, world.worth, True, 1)
