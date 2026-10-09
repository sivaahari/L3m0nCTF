"""Every score word a player reads is TRP. CTFd's hint error is the one place its API says "points".

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import pytest

from CTFd.models import db
from CTFd.plugins.l3mon_scoring import wording
from scoring_world import fixed, make_app, team_client
from tests.helpers import destroy_ctfd, gen_hint, login_as_user


@pytest.fixture()
def world():
    app = make_app()
    with app.app_context():
        challenge = fixed("fix", value=100)
        cheap = gen_hint(db, challenge.id, content="cheap", cost=0)
        dear = gen_hint(db, challenge.id, content="dear", cost=999999)
        ids = (cheap.id, dear.id)
        player = team_client(app, "alice", "team-alice")
        yield player, ids
    destroy_ctfd(app)


def unlock(player, hint_id, kind="hints"):
    return player.post("/api/v1/unlocks", json={"target": hint_id, "type": kind})


def test_a_hint_the_studio_cannot_afford_says_trp(world):
    player, (cheap, dear) = world
    r = unlock(player, dear)
    assert r.status_code == 400
    assert r.get_json() == {"success": False, "errors": {"score": "You do not have enough TRP to unlock this hint"}}
    assert "point" not in r.get_data(as_text=True).lower()


def test_nothing_else_about_the_answer_changes(world):
    player, (cheap, dear) = world
    r = unlock(player, cheap)
    assert r.status_code == 200 and r.get_json()["success"] is True
    again = unlock(player, cheap)
    assert again.status_code == 400 and again.get_json()["errors"] == {"target": "You've already unlocked this target"}
    assert unlock(player, 99999).status_code == 404
    assert player.post("/api/v1/unlocks", json={"type": "hints"}).get_json()["errors"] == {"target": "Missing target or type"}


def test_without_the_hook_ctfd_still_says_points(world):
    """The switch-off proof: the sentence above is ours, not CTFd's."""
    player, (cheap, dear) = world
    wording.ON["unlocks"] = False
    try:
        assert "points" in unlock(player, dear).get_json()["errors"]["score"]
    finally:
        wording.ON["unlocks"] = True


def test_only_that_sentence_on_that_route_is_rewritten(world):
    from flask import jsonify

    player, ids = world
    app = player.application
    stock = {"success": False, "errors": {"score": wording.STOCK}}
    cases = [
        ("/api/v1/unlocks", "POST", 400, stock, True),
        ("/api/v1/unlocks/", "POST", 400, stock, True),
        ("/api/v1/unlocks", "GET", 400, stock, False),
        ("/api/v1/unlocks", "POST", 200, stock, False),
        ("/api/v1/other", "POST", 400, stock, False),
        ("/api/v1/unlocks", "POST", 400, {"success": False, "errors": {"score": "something else"}}, False),
        ("/api/v1/unlocks", "POST", 400, {"success": False, "errors": {"target": wording.STOCK}}, False),
        ("/api/v1/unlocks", "POST", 400, {"success": False, "errors": "text"}, False),
    ]
    for path, method, status, body, rewritten in cases:
        with app.test_request_context(path, method=method):
            response = jsonify(body)
            response.status_code = status
            out = wording._rewrite(response)
            assert (out.get_json() == {"success": False, "errors": {"score": wording.TRP}}) == rewritten, (path, method, status, body)
            if not rewritten:
                assert out.get_json() == body
