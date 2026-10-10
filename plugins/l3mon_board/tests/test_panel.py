"""The programme panel: CTFd's own answer for one challenge, with one key added, `l3mon`, and nothing taken away.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import datetime
import json
from types import SimpleNamespace

import pytest

from CTFd.models import Challenges, Fails, Teams, db
from CTFd.plugins.l3mon_core.models import Programme
from CTFd.utils import set_config
from board_world import T_LIVE, T_START, clock, join_client, make_app, on_air, started, team_client, world
from tests.helpers import destroy_ctfd, gen_challenge, login_as_user, register_user

LIST = "/api/v1/challenges"


def detail(client, challenge_id):
    r = client.get(f"{LIST}/{challenge_id}")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]


def attempt(client, challenge_id, flag):
    r = client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "submission": flag})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["status"]


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
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob)
    destroy_ctfd(app)


def test_a_fixed_programme_gets_its_block_and_keeps_every_key_of_ctfds_own_answer(play):
    data = detail(play.alice, play.ids.lantern)
    street_id = Programme.query.filter_by(slug="lantern_walk").first().channel_id
    assert data["l3mon"] == {
        "slug": "lantern_walk", "number": 101, "channel": street_id, "channel_name": "Mighty Street", "sponsor": None, "difficulty": "warmup",
        "author": "Asha", "live": False, "decay": False, "start": None, "floor": None, "first_blood_open": True, "tries_left": None, "score": 0,
        "phase": {"state": "live", "frozen": False},
    }
    stock = {"attempts", "attribution", "category", "connection_info", "decay", "description", "files", "function", "hints", "id", "initial", "logic",
             "max_attempts", "minimum", "name", "next_id", "position", "rating", "ratings", "solution_id", "solution_state", "solved_by_me", "solves",
             "state", "tags", "type", "type_data", "value", "view"}
    assert set(data) == stock | {"l3mon"}, "one key added, none taken away"
    assert data["value"] == 100 and data["attribution"] == "Asha" and data["name"] == "Lantern Walk"


def test_a_dynamic_programme_says_where_it_started_and_where_it_stops(play):
    block = detail(play.alice, play.ids.moth)["l3mon"]
    assert (block["decay"], block["start"], block["floor"]) == (True, 500, 200)
    assert attempt(play.alice, play.ids.moth, "moth-answer") == "correct"
    after = detail(play.bob, play.ids.moth)
    assert after["value"] == 500 and after["l3mon"]["first_blood_open"] is False
    assert attempt(play.bob, play.ids.moth, "moth-answer") == "correct"
    assert detail(play.bob, play.ids.moth)["value"] == 499, "the value is what it is worth now; start is where it began"
    assert detail(play.bob, play.ids.moth)["l3mon"]["start"] == 500


def test_a_standard_challenge_that_carries_a_scoring_function_decays_too(play):
    challenge = Challenges(name="Slow Fade", category="misc", description="d", value=300, initial=300, minimum=100, decay=10, function="linear", state="hidden", type="standard")
    db.session.add(challenge)
    db.session.commit()
    cid = challenge.id
    assert play.admin.put("/api/v1/l3mon/admin/programmes", json={"programmes": [{"challenge_id": cid, "channel": "street", "cell": 9, "number": 109, "slug": "slow_fade"}]}).status_code == 200
    on_air(play.admin, "slow_fade")
    block = detail(play.alice, cid)["l3mon"]
    assert (block["decay"], block["start"], block["floor"]) == (True, 300, 100)


def test_a_sponsored_programme_names_its_sponsor(play):
    block = detail(play.alice, play.ids.coffee)["l3mon"]
    assert block["sponsor"] == {"name": "Acme", "logo": "acme.svg"} and block["channel_name"] == "Sponsored Break"


def test_a_live_programme_says_it_is_live(play):
    on_air(play.admin, "steam_tunnel")
    block = detail(play.alice, play.ids.steam)["l3mon"]
    assert block["live"] is True and block["difficulty"] == "insane"


def test_the_studios_own_score_is_in_the_block_and_follows_a_solve_and_a_hint(play):
    assert detail(play.alice, play.ids.lantern)["l3mon"]["score"] == 0
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    assert detail(play.alice, play.ids.lantern)["l3mon"]["score"] == 100
    assert play.alice.post("/api/v1/unlocks", json={"target": 1, "type": "hints"}).status_code == 200
    assert detail(play.abe, play.ids.lantern)["l3mon"]["score"] == 90, "every member sees the studio's number"
    assert detail(play.bob, play.ids.lantern)["l3mon"]["score"] == 0


def test_the_block_never_holds_a_flag_a_hint_text_or_another_studios_numbers(play):
    block = detail(play.alice, play.ids.lantern)["l3mon"]
    assert set(block) == {"slug", "number", "channel", "channel_name", "sponsor", "difficulty", "author", "live", "decay", "start", "floor", "first_blood_open", "tries_left", "score", "phase"}
    assert "-answer" not in str(block) and "look left" not in str(block)


def test_tries_left_is_none_when_unlimited_and_counts_down_under_the_lockout_rule(play):
    limited = gen_challenge(db, name="Careful Door", value=80, state="hidden", max_attempts=3)
    from tests.helpers import gen_flag

    gen_flag(db, limited.id, content="careful-answer")
    cid = limited.id
    play.admin.put("/api/v1/l3mon/admin/programmes", json={"programmes": [{"challenge_id": cid, "channel": "street", "cell": 8, "number": 108, "slug": "careful_door"}]})
    on_air(play.admin, "careful_door")
    assert detail(play.alice, cid)["l3mon"]["tries_left"] == 3
    assert detail(play.alice, play.ids.lantern)["l3mon"]["tries_left"] is None
    assert attempt(play.alice, cid, "wrong") == "incorrect"
    assert detail(play.alice, cid)["l3mon"]["tries_left"] == 2 and detail(play.abe, cid)["l3mon"]["tries_left"] == 2, "the studio's tries, not the player's"
    assert detail(play.bob, cid)["l3mon"]["tries_left"] == 3
    assert attempt(play.abe, cid, "wrong") == "incorrect"  # a member who is not the captain: the studio's tries, whoever sends the flag
    assert detail(play.alice, cid)["l3mon"]["tries_left"] == 1 and detail(play.abe, cid)["l3mon"]["tries_left"] == 1
    assert attempt(play.alice, cid, "wrong") == "incorrect"
    assert detail(play.alice, cid)["l3mon"]["tries_left"] == 0


def test_tries_left_under_the_timeout_rule_counts_only_the_recent_fails(play):
    limited = gen_challenge(db, name="Careful Door", value=80, state="hidden", max_attempts=2)
    cid = limited.id
    play.admin.put("/api/v1/l3mon/admin/programmes", json={"programmes": [{"challenge_id": cid, "channel": "street", "cell": 8, "number": 108, "slug": "careful_door"}]})
    on_air(play.admin, "careful_door")
    set_config("max_attempts_behavior", "timeout")
    set_config("max_attempts_timeout", 300)
    team_id = Teams.query.filter_by(name="studio-a").first().id
    from CTFd.models import Users

    uid = Users.query.filter_by(name="alice").first().id
    now = datetime.datetime.utcnow()
    for age in (3600, 10):  # one fail an hour ago (forgotten), one just now
        db.session.add(Fails(user_id=uid, team_id=team_id, challenge_id=cid, ip="127.0.0.1", provided="x", date=now - datetime.timedelta(seconds=age)))
    db.session.commit()
    assert detail(play.alice, cid)["l3mon"]["tries_left"] == 1
    set_config("max_attempts_behavior", "lockout")
    assert detail(play.alice, cid)["l3mon"]["tries_left"] == 0, "under lock-out every fail ever counts"


def test_solved_by_me_is_the_studios_own_record_even_when_the_public_counts_do_not_show_it(play):
    """CTFd reads it from the public counts and says false when no studio that counts has solved the challenge: wrong for a hidden studio
    and, under a freeze, for a studio whose only solve came after it. Found by the golden scenarios."""
    ghost = team_client(play.app, "ghost", "studio-ghost")
    ghost_id = Teams.query.filter_by(name="studio-ghost").first().id
    assert play.admin.patch(f"/api/v1/teams/{ghost_id}", json={"hidden": True}).status_code == 200
    assert attempt(ghost, play.ids.lantern, "lantern-answer") == "correct"
    data = detail(ghost, play.ids.lantern)
    assert data["solved_by_me"] is True and data["solves"] == 0, "it solved it, and the public count does not include it"
    assert detail(play.alice, play.ids.lantern)["solved_by_me"] is False
    set_config("freeze", T_LIVE - 3600)
    assert attempt(play.alice, play.ids.moth, "moth-answer") == "correct"
    assert detail(play.alice, play.ids.moth)["solved_by_me"] is True, "frozen counts are empty, but the studio's own solve is not"


def test_the_crew_gets_the_block_without_a_studio(play):
    block = detail(play.admin, play.ids.lantern)["l3mon"]
    assert block["score"] is None and block["tries_left"] is None and block["slug"] == "lantern_walk"


def test_a_challenge_on_no_channel_gets_no_block(play):
    loose = gen_challenge(db, name="Loose Thread", value=10, state="visible")
    data = detail(play.admin, loose.id)
    assert "l3mon" not in data


def test_while_the_scoreboard_is_frozen_the_block_says_so_and_the_score_stays_live(play):
    """CTFd dates a solve with the real clock (the column default is bound at import), so the freeze is set just before the real now."""
    import calendar

    from freezegun.api import real_datetime

    set_config("freeze", calendar.timegm(real_datetime.utcnow().timetuple()) - 100)
    assert attempt(play.alice, play.ids.lantern, "lantern-answer") == "correct"
    block = detail(play.alice, play.ids.lantern)["l3mon"]
    assert block["phase"] == {"state": "live", "frozen": True} and block["score"] == 100, "CTFd's own score for the studio would be 0 here"
    assert Teams.query.filter_by(name="studio-a").first().get_score() == 0


# ---- before the start -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path,message",
    [
        ("get", "/api/v1/challenges/{lantern}", "Programmes go on air when the broadcast starts."),
        ("get", "/api/v1/challenges/99999", "Programmes go on air when the broadcast starts."),
        ("get", "/api/v1/challenges", "Programmes go on air when the broadcast starts."),
        ("post", "/api/v1/challenges/attempt", "Submissions open when the broadcast starts."),
    ],
)
def test_before_the_start_a_studio_gets_phase_closed_in_the_contracts_shape_for_a_real_id_and_a_missing_one_alike(play, method, path, message):
    path = path.format(lantern=play.ids.lantern)
    with clock(T_START - 3600):
        kwargs = {"json": {"challenge_id": play.ids.lantern, "submission": "x"}} if method == "post" else {}
        r = getattr(play.alice, method)(path, **kwargs)
    body = r.get_json()
    assert r.status_code == 403 and body["success"] is False and body["error"] == "phase_closed" and body["phase"] == "before" and body["message"] == message
    assert r.headers["X-Request-Id"] == body["request_id"]


def test_before_the_start_a_visitor_and_an_account_without_a_studio_are_left_to_ctfds_own_answers(play):
    register_user(play.app, name="carol", email="carol@example.com")
    carol = login_as_user(play.app, "carol")
    with clock(T_START - 3600):
        r = carol.get(f"{LIST}/{play.ids.lantern}")
        assert r.status_code in (302, 403) and (r.get_json(silent=True) or {}).get("error") != "phase_closed"
        r = play.app.test_client().get(f"{LIST}/{play.ids.lantern}")
        assert r.status_code in (302, 403) and (r.get_json(silent=True) or {}).get("error") != "phase_closed"


def test_before_the_start_an_unverified_account_keeps_ctfds_own_answer_not_the_phase_wording(play):
    from CTFd.models import Users

    set_config("verify_emails", True)
    Users.query.filter_by(name="alice").first().verified = False
    db.session.commit()
    with clock(T_START - 3600):
        r = play.alice.get(f"{LIST}/{play.ids.lantern}")
    assert r.status_code == 403 and (r.get_json(silent=True) or {}).get("error") != "phase_closed"


def test_before_the_start_a_403_the_crew_gets_for_another_reason_is_not_called_phase_closed(play):
    with clock(T_START - 3600):
        r = play.admin.post("/api/v1/challenges/attempt", json={"challenge_id": play.ids.lantern, "submission": "x"})
    assert r.status_code == 403 and (r.get_json(silent=True) or {}).get("error") != "phase_closed", "the crew has no studio to send a flag for: CTFd's own refusal"


def test_the_crew_can_still_read_a_challenge_before_the_start(play):
    with clock(T_START - 3600):
        r = play.admin.get(f"{LIST}/{play.ids.lantern}")
    assert r.status_code == 200 and "l3mon" in r.get_json()["data"]


def shape(response, asked):
    """The status and the body of an answer with the id that was asked for and the request id taken out: CTFd's 404 repeats the address
    that was asked for, and our own refusals carry a request id that differs from one request to the next."""
    body = response.get_json(silent=True)
    if isinstance(body, dict) and "request_id" in body:
        text = json.dumps({key: value for key, value in body.items() if key != "request_id"}, sort_keys=True)
    else:
        text = response.get_data(as_text=True)
    return response.status_code, text.replace(str(asked), "<id>")


@pytest.mark.parametrize("anonymize", [None, True, "preview"])
def test_a_programme_whose_prerequisite_the_studio_has_not_met_answers_exactly_like_one_that_does_not_exist(play, anonymize):
    dumpling = Challenges.query.get(play.ids.dumpling)
    dumpling.requirements = {"prerequisites": [play.ids.lantern], **({} if anonymize is None else {"anonymize": anonymize})}
    dumpling.attribution = "SecretAuthor"
    db.session.commit()
    gone = play.bob.get(f"{LIST}/99999")
    locked = play.bob.get(f"{LIST}/{play.ids.dumpling}")
    assert gone.status_code == 404
    assert shape(locked, play.ids.dumpling) == shape(gone, 99999), "a locked programme is a missing one"
    for word in (b"Dumpling", b"SecretAuthor", b"dumpling_gate", b"Snack Square"):
        assert word not in locked.get_data()
    # the control: once the prerequisite is solved the same request answers, with the block
    assert attempt(play.bob, play.ids.lantern, "lantern-answer") == "correct"
    unlocked = play.bob.get(f"{LIST}/{play.ids.dumpling}")
    assert unlocked.status_code == 200 and unlocked.get_json()["data"]["l3mon"]["slug"] == "dumpling_gate"
    # and the crew is never held back by a prerequisite
    other = play.admin.get(f"{LIST}/{play.ids.dumpling}")
    assert other.status_code == 200 and other.get_json()["data"]["l3mon"]["author"] == "SecretAuthor"


def test_before_the_start_a_locked_programme_is_not_told_apart_from_an_open_one(play):
    dumpling = Challenges.query.get(play.ids.dumpling)
    dumpling.requirements = {"prerequisites": [play.ids.lantern], "anonymize": True}
    db.session.commit()
    with clock(T_START - 3600):
        locked = play.bob.get(f"{LIST}/{play.ids.dumpling}")
        open_ = play.bob.get(f"{LIST}/{play.ids.moth}")
    assert shape(locked, play.ids.dumpling) == shape(open_, play.ids.moth), "before the start nothing tells a locked programme from an open one"

