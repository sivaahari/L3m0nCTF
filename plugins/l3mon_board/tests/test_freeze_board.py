"""What a studio is told while the scoreboard is frozen (SP3 part 3.6): the same still picture as before the freeze, except its own news.

Other studios' solves after the freeze must not show anywhere a studio can look: not in a count, a value, a rank, a score, the tick or the
tag of any answer. A studio always sees its own solves (its board marks them, its score includes them, and the count of a programme it solved
after the freeze includes it). The freeze time is real time here, minutes before now: CTFd dates a solve with the real clock (the column's
default is bound at import), and the rest of the platform runs on the test clock.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import datetime
import time
from types import SimpleNamespace

import pytest

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Teams, db
from CTFd.plugins.l3mon_scoring import values
from CTFd.utils import set_config
from board_world import BOARD, TICKS, T_LIVE, clock, join_client, make_app, on_air, solve, started, team_client, user_id, world
from tests.helpers import destroy_ctfd, login_as_user

REAL_NOW = int(time.time())  # read at import, before any test clock starts
FROZEN_FROM = REAL_NOW - 60
GUIDE, EPG = "/api/v1/l3mon/guide", "/api/v1/l3mon/guide/epg"
SCOREBOARD, ROWS = "/api/v1/l3mon/scoreboard", "/api/v1/l3mon/scoreboard/rows"
FEED = "/ctftime/standings.json"
SCORING = "/api/v1/l3mon/admin/scoring"


def long_ago():
    return datetime.datetime.utcfromtimestamp(REAL_NOW - 7200)


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "moth_cipher", "hush_alley", "noodle_ledger", "coffee_break")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            cara = team_client(app, "cara", "studio-c")
            hal = team_client(app, "hal", "studio-h")
            Teams.query.filter_by(name="studio-h").one().hidden = True
            db.session.commit()
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob, cara=cara, hal=hal, visitor=app.test_client())
    destroy_ctfd(app)


def team_id(name):
    return Teams.query.filter_by(name=name).one().id


def before_the_freeze(play, studio, member, challenge):
    solve(team_id(studio), user_id(member), challenge, at=long_ago())


def values_in_place():
    values.recalculate()
    db.session.commit()
    clear_challenges()
    clear_standings()


def send(client, challenge_id, flag):
    r = client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "submission": flag})
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "correct", r.get_data(as_text=True)
    return r.get_json()["data"]


def freeze_now():
    set_config("freeze", FROZEN_FROM)
    clear_standings()
    clear_challenges()


def board(client):
    data = client.get(BOARD).get_json()["data"]
    return {p["slug"]: p for p in data["programmes"]}, data


def two_early_solvers(play):
    """alpha and bravo solved the fixed and the dynamic programme before the freeze; the dynamic one is worth 499 (two solvers)."""
    for studio, member in (("studio-a", "alice"), ("studio-b", "bob")):
        before_the_freeze(play, studio, member, play.ids.lantern)
        before_the_freeze(play, studio, member, play.ids.moth)
    values_in_place()


# ---- counts and first blood ---------------------------------------------------------------------------------------------------------------------

def test_a_studios_counts_are_the_publics_frozen_ones_plus_its_own_later_solves(play):
    two_early_solvers(play)
    freeze_now()
    send(play.cara, play.ids.moth, "moth-answer")      # a late solve of a programme two studios solved before the freeze
    send(play.alice, play.ids.hush, "hush-answer")     # a late solve of a programme nobody solved
    send(play.hal, play.ids.noodle, "noodle-answer")   # a hidden studio's late solve: it never counts
    seen_by_bob, _ = board(play.bob)
    assert (seen_by_bob["moth_cipher"]["solves"], seen_by_bob["hush_alley"]["solves"], seen_by_bob["noodle_ledger"]["solves"]) == (2, 0, 0)
    assert [seen_by_bob[s]["first_blood_open"] for s in ("moth_cipher", "hush_alley", "noodle_ledger")] == [False, True, True]
    seen_by_alice, _ = board(play.alice)
    assert seen_by_alice["hush_alley"]["solves"] == 1, "alice sees her own late solve counted"
    assert seen_by_alice["hush_alley"]["solved_by_me"] is True and seen_by_alice["hush_alley"]["first_blood_open"] is False
    assert seen_by_alice["moth_cipher"]["solves"] == 2
    seen_by_cara, _ = board(play.cara)
    assert seen_by_cara["moth_cipher"]["solves"] == 3, "the two public solves and her own"
    seen_by_hal, _ = board(play.hal)
    assert seen_by_hal["noodle_ledger"]["solves"] == 0 and seen_by_hal["noodle_ledger"]["solved_by_me"] is True, "a hidden studio is never counted, not even for itself"


def test_the_programme_panel_says_the_same(play):
    two_early_solvers(play)
    freeze_now()
    send(play.cara, play.ids.moth, "moth-answer")
    send(play.alice, play.ids.hush, "hush-answer")
    got = {who: play_client.get(f"/api/v1/challenges/{play.ids.moth}").get_json()["data"] for who, play_client in (("bob", play.bob), ("cara", play.cara))}
    assert (got["bob"]["solves"], got["cara"]["solves"]) == (2, 3)
    assert play.alice.get(f"/api/v1/challenges/{play.ids.hush}").get_json()["data"]["solves"] == 1
    assert play.bob.get(f"/api/v1/challenges/{play.ids.hush}").get_json()["data"]["solves"] == 0


def test_when_ctfd_hides_the_solve_counts_the_panel_keeps_them_hidden(play):
    """CTFd answers `solves: null` when the scores are hidden; the panel must not turn that into a number (the review found it did: 0 and an open first blood)."""
    two_early_solvers(play)
    set_config("score_visibility", "hidden")
    clear_challenges()
    data = play.bob.get(f"/api/v1/challenges/{play.ids.moth}").get_json()["data"]
    assert data["solves"] is None, data["solves"]
    assert data["l3mon"]["first_blood_open"] is False
    freeze_now()
    data = play.bob.get(f"/api/v1/challenges/{play.ids.moth}").get_json()["data"]
    assert data["solves"] is None and data["l3mon"]["first_blood_open"] is False, "frozen as well"


def test_the_story_meter_counts_what_the_viewer_may_know(play):
    set_config("l3mon_story_air_target", 20)
    two_early_solvers(play)
    freeze_now()
    send(play.alice, play.ids.hush, "hush-answer")
    bob = play.bob.get(GUIDE).get_json()["data"]["story"]["on_air"]
    alice = play.alice.get(GUIDE).get_json()["data"]["story"]["on_air"]
    assert bob == 0.2 and alice == 0.25, "four public solves over twenty; alice also counts her own late one"


# ---- the values --------------------------------------------------------------------------------------------------------------------------------

def test_a_dynamic_value_stands_still_on_every_answer_and_the_own_score_is_made_from_it(play):
    two_early_solvers(play)
    freeze_now()
    send(play.cara, play.ids.moth, "moth-answer")
    seen, data = board(play.bob)
    assert seen["moth_cipher"]["value"] == 499 and data["team"]["score"] == 100 + 499
    assert play.bob.get(f"/api/v1/challenges/{play.ids.moth}").get_json()["data"]["value"] == 499
    cara, cara_data = board(play.cara)
    assert cara_data["team"]["score"] == 499, "her own solve counts at the value that stands still"
    reply = send(play.alice, play.ids.hush, "hush-answer")["l3mon"]
    assert reply["value"] == 300


# ---- nothing moves for the others ----------------------------------------------------------------------------------------------------------------

def everything_bob_can_read(play):
    out = {}
    for name, path in (("board", BOARD), ("ticks", TICKS), ("guide", GUIDE), ("epg", EPG), ("scoreboard", SCOREBOARD), ("rows", ROWS),
                       ("panel_moth", f"/api/v1/challenges/{play.ids.moth}"), ("panel_hush", f"/api/v1/challenges/{play.ids.hush}"),
                       ("challenges", "/api/v1/challenges"), ("notifications", "/api/v1/notifications")):
        r = play.bob.get(path)
        out[name] = (r.status_code, r.headers.get("ETag"), r.get_data(as_text=True))
    feed = play.visitor.get(FEED)
    out["feed"] = (feed.status_code, None, feed.get_data(as_text=True))
    return out


def test_when_others_solve_after_the_freeze_nothing_a_studio_can_read_changes_at_all(play):
    """The whole point: a byte-for-byte comparison of everything one studio can read, before and after three other studios solve."""
    two_early_solvers(play)
    freeze_now()
    before = everything_bob_can_read(play)
    for client, challenge, flag in ((play.cara, play.ids.moth, "moth-answer"), (play.cara, play.ids.lantern, "lantern-answer"),
                                    (play.alice, play.ids.hush, "hush-answer"), (play.abe, play.ids.noodle, "noodle-answer"),
                                    (play.hal, play.ids.moth, "moth-answer")):
        send(client, challenge, flag)
    after = everything_bob_can_read(play)
    for name in before:
        assert after[name] == before[name], f"{name} moved when other studios solved after the freeze"


def test_a_studios_own_solve_changes_what_it_reads_and_nobody_elses(play):
    two_early_solvers(play)
    freeze_now()
    before = everything_bob_can_read(play)
    send(play.bob, play.ids.hush, "hush-answer")
    after = everything_bob_can_read(play)
    assert after["board"] != before["board"] and after["guide"] != before["guide"] and after["ticks"] != before["ticks"], "bob learns of his own solve"
    assert after["scoreboard"][2] != before["scoreboard"][2] or after["scoreboard"][1] != before["scoreboard"][1], "his own line follows"
    assert after["feed"] == before["feed"] and after["rows"][2].count("<li") == before["rows"][2].count("<li"), "the public list stands still"


def test_when_the_freeze_is_lifted_everything_catches_up_at_once(play):
    two_early_solvers(play)
    freeze_now()
    before = everything_bob_can_read(play)
    send(play.cara, play.ids.moth, "moth-answer")
    send(play.alice, play.ids.hush, "hush-answer")
    assert play.admin.patch("/api/v1/configs", json={"freeze": None}).status_code == 200
    seen, data = board(play.bob)
    assert seen["moth_cipher"]["solves"] == 3 and seen["hush_alley"]["solves"] == 1
    assert seen["moth_cipher"]["value"] == values.formula(values.decaying_rows([play.ids.moth])[0], 3) < 499
    assert data["team"]["place"] is not None
    assert everything_bob_can_read(play)["feed"] != before["feed"]


# ---- the crew's actions during the freeze ------------------------------------------------------------------------------------------------------------

def test_a_revoke_and_a_restore_during_the_freeze_move_the_public_count_of_pre_freeze_solves_only(play):
    two_early_solvers(play)
    freeze_now()
    send(play.cara, play.ids.moth, "moth-answer")
    held_at = board(play.bob)[0]["moth_cipher"]["value"]
    r = play.admin.post(f"{SCORING}/revoke", json={"challenge_id": play.ids.moth, "reason": "the checker broke"})
    assert r.status_code == 200, r.get_data(as_text=True)
    after = board(play.bob)[0]["moth_cipher"]
    assert after["solves"] == 0 and after["value"] == held_at and after["solved_by_me"] is False
    assert board(play.cara)[0]["moth_cipher"]["solves"] == 0, "and the late solver's own solve is set aside as well"
    r = play.admin.post(f"{SCORING}/restore", json={"challenge_id": play.ids.moth})
    assert r.status_code == 200, r.get_data(as_text=True)
    back = board(play.bob)[0]["moth_cipher"]
    assert back["solves"] == 2 and back["value"] == held_at and back["solved_by_me"] is True, "the public count is the frozen one again"
    assert board(play.cara)[0]["moth_cipher"]["solves"] == 3


def public_rows(client):
    import re

    markup = client.get(ROWS).get_data(as_text=True)
    return [(name, int(score)) for name, score in re.findall(r"<bdi>(.*?)</bdi>.*?<b>(-?\d+)</b> TRP", markup)]


def test_the_public_list_is_made_from_the_data_so_a_crew_action_on_pre_freeze_solves_shows_at_once_a_ban_included(play):
    """The demo keeps the ranking as a picture; the platform computes it, so a moderator's action on what happened BEFORE the freeze
    shows at once (a banned cheater leaves the public list) and tells nobody anything about the solves after it."""
    two_early_solvers(play)
    freeze_now()
    assert public_rows(play.bob) == [("studio-a", 599), ("studio-b", 599)]
    send(play.cara, play.ids.moth, "moth-answer")  # a late solve, invisible to the public
    assert public_rows(play.bob) == [("studio-a", 599), ("studio-b", 599)]
    assert play.admin.post(f"{SCORING}/revoke", json={"challenge_id": play.ids.moth, "reason": "the checker broke"}).status_code == 200
    assert public_rows(play.bob) == [("studio-a", 100), ("studio-b", 100)], "the voided TRP comes off the public list"
    assert play.admin.post(f"{SCORING}/restore", json={"challenge_id": play.ids.moth}).status_code == 200
    assert public_rows(play.bob) == [("studio-a", 599), ("studio-b", 599)]
    team_a = team_id("studio-a")
    assert play.admin.patch(f"/api/v1/teams/{team_a}", json={"banned": True}).status_code == 200
    assert public_rows(play.bob) == [("studio-b", 599)], "a ban during the freeze removes the studio from the public list at once"
