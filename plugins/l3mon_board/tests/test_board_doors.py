"""The doors of the board: a programme that is not on air does not exist in anything the board part sends, and nothing secret ever does.

A world with programmes held back (their names, slugs, categories, difficulties and flags are markers) is asked as the studio, another
studio, a visitor and the crew, on every route this part adds or decorates. No marker may appear anywhere; a CONTROL (the programmes on
air are named in the same answers) proves the scan can see; and the scan is run once with the guard switched off and must then find the
markers, so a test that cannot fail does not stand in for one that can.

The board's keys are an allow-list: a field added later is a decision, not an accident.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import json
from types import SimpleNamespace

import pytest

from CTFd.models import Challenges
from board_world import BOARD, T_LIVE, TICKS, clock, make_app, on_air, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user

MARKERS = ("Hush Alley", "hush_alley", "forensics", "hush-answer", "Steam Tunnel", "steam_tunnel", "insane", "steam-answer", "Dumpling Gate", "dumpling_gate")
CONTROL = ("Lantern Walk", "lantern_walk", "Moth Cipher", "moth_cipher", "Noodle Ledger")
FLAG_SHAPE = "-answer"

TOP_KEYS = {"ver", "phase", "team", "channels", "programmes", "notif_id", "notif_ver", "show_coming"}
CHANNEL_KEYS = {"id", "no", "slug", "name", "synopsis", "accent", "picture", "total", "solved", "signal", "on_air", "coming", "sponsor", "cold_open"}
PROGRAMME_KEYS = {"id", "slug", "number", "channel", "order", "cell", "name", "category", "difficulty", "value", "solves", "solved_by_me", "first_blood_open", "live", "instance"}
TEAM_KEYS = {"id", "name", "score", "place", "of"}


def leaks(text, words=MARKERS):
    return [m for m in words if m.lower() in text.lower()]


def all_keys(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            all_keys(inner, found)
    elif isinstance(value, list):
        for inner in value:
            all_keys(inner, found)
    return found


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "moth_cipher", "noodle_ledger", "coffee_break")  # the hush alley, the steam tunnel and the dumpling stay off air
            alice = team_client(app, "alice", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            # alice has used the board a lot: solved, got a hint, failed once
            alice.post("/api/v1/challenges/attempt", json={"challenge_id": ids.lantern, "submission": "lantern-answer"})
            alice.post("/api/v1/unlocks", json={"target": 1, "type": "hints"})
            alice.post("/api/v1/challenges/attempt", json={"challenge_id": ids.moth, "submission": "wrong"})
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, bob=bob, visitor=app.test_client())
    destroy_ctfd(app)


def routes(play):
    return [BOARD, TICKS, f"/api/v1/challenges/{play.ids.lantern}", f"/api/v1/challenges/{play.ids.moth}"]


def scan(play, viewer, words=MARKERS):
    text = ""
    for path in routes(play):
        text += viewer.get(path).get_data(as_text=True)
    for cid in (play.ids.lantern, play.ids.moth):
        text += viewer.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "wrong"}).get_data(as_text=True)
    return leaks(text, words)


@pytest.mark.parametrize("who", ["alice", "bob", "visitor", "admin"])
def test_no_marker_of_a_programme_that_is_not_on_air_is_in_any_answer(play, who):
    assert scan(play, getattr(play, who)) == []


@pytest.mark.parametrize("who", ["alice", "bob"])
def test_control_the_programmes_on_air_are_named_in_the_same_answers(play, who):
    viewer = getattr(play, who)
    assert len(leaks("".join(viewer.get(p).get_data(as_text=True) for p in routes(play)), CONTROL)) >= 4


def test_the_scan_finds_the_markers_when_the_guard_is_switched_off(play, monkeypatch):
    """Show the scan can fail: a board built from every challenge, not from the ones that may be seen, leaks."""
    from CTFd.plugins.l3mon_board import board

    monkeypatch.setattr(board, "visible_challenge_ids", lambda **kw: {cid for (cid,) in Challenges.query.with_entities(Challenges.id).all()})
    assert len(leaks(play.alice.get(BOARD).get_data(as_text=True))) >= 3


def test_no_answer_holds_a_flag_and_no_key_is_called_story(play):
    for viewer in (play.alice, play.bob, play.admin):
        for path in routes(play):
            body = viewer.get(path).get_data(as_text=True)
            assert FLAG_SHAPE not in body, path
        for path in (BOARD, TICKS):
            assert "story" not in all_keys(viewer.get(path).get_json()), "the board JSON never carries a field called story"
    reply = play.bob.post("/api/v1/challenges/attempt", json={"challenge_id": play.ids.lantern, "submission": "lantern-answer"}).get_data(as_text=True)
    assert reply.count(FLAG_SHAPE) == 0, "the reply to a flag does not repeat it"


def test_the_boards_keys_are_an_allow_list(play):
    for viewer in (play.alice, play.admin):
        data = viewer.get(BOARD).get_json()["data"]
        assert set(data) == TOP_KEYS
        assert all(set(c) == CHANNEL_KEYS for c in data["channels"])
        assert all(set(p) == PROGRAMME_KEYS for p in data["programmes"])
    assert set(play.alice.get(BOARD).get_json()["data"]["team"]) == TEAM_KEYS
    assert play.admin.get(BOARD).get_json()["data"]["team"] is None


def test_a_withheld_programme_answers_the_panel_and_the_flag_box_exactly_like_an_id_that_never_existed(play):
    missing = 987654
    for viewer in (play.alice, play.bob):
        a, b = viewer.get(f"/api/v1/challenges/{play.ids.hush}"), viewer.get(f"/api/v1/challenges/{missing}")
        assert a.status_code == b.status_code == 404
        assert a.get_data(as_text=True).replace(str(play.ids.hush), "ID") == b.get_data(as_text=True).replace(str(missing), "ID"), "the same words, the echoed address aside"
        a = viewer.post("/api/v1/challenges/attempt", json={"challenge_id": play.ids.hush, "submission": "hush-answer"})
        b = viewer.post("/api/v1/challenges/attempt", json={"challenge_id": missing, "submission": "hush-answer"})
        assert a.status_code == b.status_code and a.get_json() == b.get_json(), "a right flag for a held-back programme is refused like any other"
        assert "l3mon" not in json.dumps(a.get_json())


def test_the_numbers_of_a_flag_reply_do_not_count_what_is_off_air(play):
    reply = play.bob.post("/api/v1/challenges/attempt", json={"challenge_id": play.ids.coffee, "submission": "coffee-answer"}).get_json()["data"]["l3mon"]
    assert reply["reel"] == 1 and reply["channel_signal"] == 1
    reply = play.bob.post("/api/v1/challenges/attempt", json={"challenge_id": play.ids.noodle, "submission": "noodle-answer"}).get_json()["data"]["l3mon"]
    assert reply["reel"] == 2 and reply["channel_signal"] == 0.33, "the snack square has three cells; one is clear; two are not on air"
