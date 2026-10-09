"""The tick the pages poll: {ver, notif_ver, own}.

`ver` is the platform's tick (one string for everybody); `own` counts what has happened to the signed-in studio, so a teammate's
solve reaches an open page even when the shared tick is held back (part 3.6). The tests move `own` with the real routes: a flag, a
hint, a bonus, a Revoke and a Restore.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
from types import SimpleNamespace

import pytest

from CTFd.models import Notifications, Teams, db
from board_world import T_END, T_LIVE, TICKS, clock, join_client, make_app, on_air, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user

SCORING = "/api/v1/l3mon/admin/scoring"


def attempt(client, challenge_id, flag):
    r = client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "submission": flag})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["status"]


def ticks(client):
    r = client.get(TICKS)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.headers["Cache-Control"] == "no-store"
    return r.get_json()["data"]


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "moth_cipher", "noodle_ledger", "dumpling_gate", "coffee_break")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob, team=lambda name: Teams.query.filter_by(name=name).first().id)
    destroy_ctfd(app)


def test_the_answer_has_exactly_the_three_keys(play):
    data = ticks(play.alice)
    assert set(data) == {"ver", "notif_ver", "own"} and isinstance(data["ver"], str) and data["own"] == 0


def test_own_goes_up_for_a_solve_a_hint_and_a_bonus_and_is_shared_by_the_whole_studio(play):
    assert ticks(play.alice)["own"] == 0
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    assert ticks(play.alice)["own"] == 1 and ticks(play.abe)["own"] == 1, "a teammate's open page learns about it"
    assert play.alice.post("/api/v1/unlocks", json={"target": 1, "type": "hints"}).status_code == 200
    after_hint = ticks(play.abe)["own"]
    assert after_hint > 1, "a hint unlock adds a row (and CTFd's award for its cost)"
    r = play.admin.post(f"{SCORING}/bonus", json={"team_id": play.team("studio-a"), "trp": 40, "message": "found a bug"})
    assert r.status_code == 200, r.get_data(as_text=True)
    after_bonus = ticks(play.alice)["own"]
    assert after_bonus >= after_hint + 2, "a bonus adds the award and a private line"
    assert ticks(play.bob)["own"] == 0, "another studio's events are not in it"


def test_a_revoke_and_a_restore_both_move_it_and_it_never_goes_down_for_them(play):
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    seen = [ticks(play.alice)["own"]]
    r = play.admin.post(f"{SCORING}/revoke", json={"challenge_id": play.ids.lantern, "reason": "the checker broke"})
    assert r.status_code == 200, r.get_data(as_text=True)
    seen.append(ticks(play.alice)["own"])
    r = play.admin.post(f"{SCORING}/restore", json={"challenge_id": play.ids.lantern})
    assert r.status_code == 200, r.get_data(as_text=True)
    seen.append(ticks(play.alice)["own"])
    assert seen[0] < seen[1] < seen[2], seen


def test_own_is_zero_for_the_crew_and_for_a_studio_with_nothing(play):
    assert ticks(play.admin)["own"] == 0 and ticks(play.bob)["own"] == 0


def test_the_crews_own_is_zero_even_when_rows_with_no_studio_exist(play):
    from CTFd.models import Awards, Users

    admin = Users.query.filter_by(name="admin").first()
    db.session.add(Awards(user_id=admin.id, team_id=None, name="stray", value=5, category="x"))
    db.session.commit()
    assert ticks(play.admin)["own"] == 0, "a row with no studio belongs to nobody's count"


def test_the_shared_tick_moves_when_something_is_committed_and_when_the_phase_changes_with_nothing_committed(play):
    ver = ticks(play.bob)["ver"]
    assert ticks(play.bob)["ver"] == ver, "asking does not move it"
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    moved = ticks(play.bob)["ver"]
    assert moved != ver, "a solve moves it for everybody"
    with clock(T_END + 60):
        assert ticks(play.bob)["ver"] != moved, "the end of the broadcast is a moment, not a write: the tick still tells the pages"


def test_the_news_version_changes_when_a_public_line_is_added(play):
    before = ticks(play.alice)["notif_ver"]
    db.session.add(Notifications(title="New on air", content="CH 2 has 1 new programme."))
    db.session.commit()
    assert ticks(play.alice)["notif_ver"] != before and ticks(play.bob)["notif_ver"] == ticks(play.alice)["notif_ver"]


def test_the_news_version_also_changes_when_an_older_line_is_taken_away_and_a_new_one_added_in_the_same_breath(play):
    for title in ("one", "two"):
        db.session.add(Notifications(title=title, content="x"))
    db.session.commit()
    older = Notifications.query.order_by(Notifications.id).first().id
    before = ticks(play.alice)["notif_ver"]
    assert play.admin.delete(f"/api/v1/notifications/{older}", json={}).status_code == 200
    taken = ticks(play.alice)["notif_ver"]
    assert taken != before, "the newest id is the same, the count is not"
    db.session.add(Notifications(title="three", content="x"))
    db.session.commit()
    assert ticks(play.alice)["notif_ver"] not in (before, taken), "same count as before, a newer id"


def test_asking_the_tick_writes_nothing(play):
    from CTFd.models import Tracking

    before = (Tracking.query.count(), Notifications.query.count())
    for _ in range(3):
        ticks(play.alice)
    assert (Tracking.query.count(), Notifications.query.count()) == before
