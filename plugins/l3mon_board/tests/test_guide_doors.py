"""The doors of the Guide, the scoreboard and the bell (SP3 part 3.5): nothing about a programme that is not on air, nothing of another studio, no
dates, no ids that name a studio's account, and no flag is in anything these routes send.

A world with programmes held back (their names, slugs, categories, difficulties and flags are markers) and a studio with private lines (a
marker in the crew's words) is asked as the studio, another studio, a visitor and the crew. No marker may appear in a place it does not belong; a CONTROL
(the programmes on air and the studios' own words are in the same answers) proves the scan can see; and the scan is run once with the guard switched off and
must then find the markers, so a test that cannot fail does not stand in for one that can.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import re
from types import SimpleNamespace

import pytest

from CTFd.models import Challenges
from board_world import T_LIVE, clock, join_client, make_app, on_air, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user

GUIDE, EPG = "/api/v1/l3mon/guide", "/api/v1/l3mon/guide/epg"
SCOREBOARD, ROWS = "/api/v1/l3mon/scoreboard", "/api/v1/l3mon/scoreboard/rows"
BELL = "/api/v1/notifications"
PATHS = [GUIDE, EPG, SCOREBOARD, ROWS, BELL]

MARKERS = ("Hush Alley", "hush_alley", "forensics", "hush-answer", "Steam Tunnel", "steam_tunnel", "insane", "steam-answer", "Dumpling Gate", "dumpling_gate")
CONTROL = ("Lantern Walk", "lantern_walk", "Moth Cipher", "Noodle Ledger", "Coffee Break")
PRIVATE = "MARKERBONUS-only-for-studio-b"
PRIVATE_A = "MARKERNOTE-only-for-studio-a"

GUIDE_KEYS = {"ver", "phase", "banner", "epg_sig", "team", "story"}
TEAM_KEYS = {"score", "place", "of", "solves", "hints_used", "instances_live", "members", "bonus", "notes", "by_channel"}
MEMBER_KEYS = {"id", "name", "captain", "you", "solves", "trp", "pct"}
SCOREBOARD_KEYS = {"ver", "phase", "banner", "total", "shown", "rows_sig", "me"}
ME_KEYS = {"name", "score", "pos", "of"}
NEVER_KEYS = {"date", "created", "created_at", "at", "time", "email", "team_id", "user_id", "captain_id", "ip", "password", "secret", "oauth_id", "website", "affiliation",
              "country", "flag", "flags", "description", "hint", "hints", "content_html", "html", "type"}
DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}|T\d{2}:\d{2}")


def leaks(text, words=MARKERS):
    return [m for m in words if m.lower() in text.lower()]


def keys_of(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            keys_of(inner, found)
    elif isinstance(value, list):
        for inner in value:
            keys_of(inner, found)
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
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            from CTFd.models import Teams

            team = {t.name: t.id for t in Teams.query.all()}
            alice.post("/api/v1/challenges/attempt", json={"challenge_id": ids.lantern, "submission": "lantern-answer"})
            alice.post("/api/v1/unlocks", json={"target": 1, "type": "hints"})
            bob.post("/api/v1/challenges/attempt", json={"challenge_id": ids.coffee, "submission": "coffee-answer"})
            for who, trp, message in (("studio-b", 30, PRIVATE), ("studio-a", 20, PRIVATE_A)):
                r = admin.post("/api/v1/l3mon/admin/scoring/bonus", json={"team_id": team[who], "trp": trp, "message": message})
                assert r.status_code == 200, r.get_data(as_text=True)
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob, visitor=app.test_client())
    destroy_ctfd(app)


def everything(viewer):
    return "".join(viewer.get(path).get_data(as_text=True) for path in PATHS)


@pytest.mark.parametrize("who", ["alice", "bob", "visitor", "admin"])
def test_no_marker_of_a_programme_that_is_not_on_air_is_in_any_answer(play, who):
    assert leaks(everything(getattr(play, who))) == []


@pytest.mark.parametrize("who", ["alice", "bob"])
def test_control_the_programmes_on_air_and_the_studios_own_words_are_named_in_the_same_answers(play, who):
    text = everything(getattr(play, who))
    assert len(leaks(text, CONTROL)) >= 4
    assert leaks(text, ("Mighty Street", "studio-a", "studio-b", "Acme"))


def test_the_scan_finds_the_markers_when_the_guard_is_switched_off(play, monkeypatch):
    """Show the scan can fail: a grid built from every challenge, not from the ones that may be seen, leaks."""
    from CTFd.plugins.l3mon_board import plan

    monkeypatch.setattr(plan, "visible_challenge_ids", lambda **kw: {cid for (cid,) in Challenges.query.with_entities(Challenges.id).all()})
    assert len(leaks(play.alice.get(EPG).get_data(as_text=True))) >= 3


def test_a_studios_private_lines_are_read_by_that_studio_alone(play):
    for path in (GUIDE, BELL):
        mine, theirs = play.bob.get(path).get_data(as_text=True), play.alice.get(path).get_data(as_text=True)
        assert PRIVATE in mine and PRIVATE not in theirs and PRIVATE_A in theirs and PRIVATE_A not in mine, path
    for path in PATHS + ["/api/v1/l3mon/ticks"]:
        assert PRIVATE not in play.visitor.get(path).get_data(as_text=True) and PRIVATE_A not in play.visitor.get(path).get_data(as_text=True)
        assert PRIVATE not in play.admin.get(path).get_data(as_text=True), "the crew's own bell is CTFd's public list, not a studio's"


def test_another_studios_members_are_never_listed_and_the_bonus_is_not_told_to_the_others(play):
    for text in (play.bob.get(GUIDE).get_data(as_text=True), play.bob.get(BELL).get_data(as_text=True), play.bob.get(ROWS).get_data(as_text=True)):
        assert not re.search(r"(alice|abe)", text), "bob's answers never name studio A's players"
    assert '"bonus":20' not in play.bob.get(GUIDE).get_data(as_text=True)
    guide = play.alice.get(GUIDE).get_json()["data"]["team"]
    assert guide["bonus"] == 20 and {m["name"] for m in guide["members"]} == {"alice", "abe"}


def test_the_answers_keys_are_an_allow_list(play):
    for viewer in (play.alice, play.admin):
        data = viewer.get(GUIDE).get_json()["data"]
        assert set(data) == GUIDE_KEYS
        scoreboard = viewer.get(SCOREBOARD).get_json()["data"]
        assert set(scoreboard) == SCOREBOARD_KEYS
    team = play.alice.get(GUIDE).get_json()["data"]["team"]
    assert set(team) == TEAM_KEYS and all(set(m) == MEMBER_KEYS for m in team["members"])
    assert set(play.alice.get(SCOREBOARD).get_json()["data"]["me"]) == ME_KEYS
    assert play.admin.get(GUIDE).get_json()["data"]["team"] is None and play.admin.get(SCOREBOARD).get_json()["data"]["me"] is None


def test_no_answer_has_a_date_a_studios_account_number_an_address_or_a_key_that_gives_one_away(play):
    for viewer in (play.alice, play.bob, play.admin):
        for path in (GUIDE, SCOREBOARD):
            body = viewer.get(path).get_json()["data"]
            assert not (keys_of(body) & NEVER_KEYS), (path, keys_of(body) & NEVER_KEYS)
            assert not DATE_SHAPE.search(viewer.get(path).get_data(as_text=True)), path
        rows = viewer.get(ROWS).get_data(as_text=True)
        assert not DATE_SHAPE.search(rows) and "@" not in rows
    for viewer in (play.alice, play.bob):  # the crew keeps CTFd's own list, with its dates, to manage the news
        lines = viewer.get(BELL).get_json()["data"]
        assert lines and all(set(line) == {"id", "title", "content"} for line in lines)
        assert not DATE_SHAPE.search(viewer.get(BELL).get_data(as_text=True))


def test_no_answer_holds_a_flag(play):
    for viewer in (play.alice, play.bob, play.admin, play.visitor):
        assert "-answer" not in everything(viewer)


def test_the_stock_notifications_page_and_the_old_door_for_one_notification_are_closed_to_players(play):
    for viewer in (play.alice, play.bob):
        assert viewer.get("/notifications").status_code == 404
        assert viewer.get("/api/v1/notifications/1").status_code in (403, 404)
    assert play.visitor.get("/api/v1/notifications/1").status_code in (401, 403, 404)


def test_every_new_route_is_only_a_read(play):
    for path in (GUIDE, EPG, SCOREBOARD, ROWS):
        for method in ("post", "put", "patch", "delete"):
            r = getattr(play.alice, method)(path, json={})
            assert r.status_code in (404, 405), (method, path, r.status_code)
