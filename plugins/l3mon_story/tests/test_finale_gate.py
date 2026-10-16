"""The finale: the last story, which belongs to no channel and plays for every registered player once the broadcast is complete.

Two things open it: the on-air meter reaching 100 percent (the Guide's own number, as that player sees it), or the crew's trigger (the setting
`l3mon_finale_open`). Until then it is as absent as any story that is not on air: the same 404, a visitor told to sign in, the crew able to
preview it. Before the start nothing opens it, not even the trigger.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_story
"""
import json
from types import SimpleNamespace

import pytest

from CTFd.cache import clear_standings
from CTFd.models import Users, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_story import store
from CTFd.utils import set_config
from story_world import STORY, T_LIVE, T_START, clock, load_plan, make_app, release_street, started, team_client, write_pic_story
from tests.helpers import destroy_ctfd, gen_solve, login_as_user

TRIGGER = "l3mon_finale_open"
TARGET = "l3mon_story_air_target"
METER = "l3mon_story_meter"


@pytest.fixture()
def play(tmp_path, monkeypatch):
    folder = tmp_path / "story"
    folder.mkdir()
    monkeypatch.setenv("L3MON_STORY_DIR", str(folder))
    store.reset()
    write_pic_story(str(folder), "street", title="Picture Hour")
    finale, files = write_pic_story(str(folder), "finale", title="Last Light", kicker="LAST CALL", panels=3)
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            load_plan(admin)
            release_street(admin)
            alice = team_client(app, "alice", "studio-a")
            yield SimpleNamespace(app=app, admin=admin, alice=alice, visitor=app.test_client(), folder=str(folder), files=files)
    destroy_ctfd(app)
    store.reset()


def get(client, slug="finale"):
    return client.get(f"{STORY}/{slug}")


def open_with_the_meter(play, target=1):
    """One solve of the one programme on air, against a target of `target` solves: the meter reads 1 / target."""
    set_config(TARGET, target)
    challenge_id = Programme.query.filter_by(slug="lantern_walk").one().challenge_id
    user = Users.query.filter_by(name="alice").one()
    gen_solve(db, user_id=user.id, team_id=user.team_id, challenge_id=challenge_id)
    clear_standings()


def guide_meter(client):
    return client.get("/api/v1/l3mon/guide").get_json()["data"]["story"]["on_air"]


# ---- shut -----------------------------------------------------------------------------------------------------------------

def test_the_finale_is_as_absent_as_any_story_that_is_not_on_air_until_something_opens_it(play):
    unknown = get(play.alice, "never-heard-of-it")
    shut = get(play.alice)
    assert shut.status_code == 404
    first, second = unknown.get_json(), shut.get_json()
    first.pop("request_id"), second.pop("request_id")
    assert first == second, "the same words: nobody can tell that a finale exists"
    assert "Last Light" not in shut.get_data(as_text=True)


def test_the_list_does_not_hold_the_finale_while_it_is_shut(play):
    slugs = [s["slug"] for s in play.alice.get(STORY).get_json()["data"]["stories"]]
    assert slugs == ["street"]


def test_a_visitor_is_told_to_sign_in_for_the_finale_open_or_shut(play):
    assert play.visitor.get(f"{STORY}/finale").status_code == 401
    set_config(TRIGGER, True)
    assert play.visitor.get(f"{STORY}/finale").status_code == 401
    assert play.visitor.get(f"{STORY}/finale/pic/p1").status_code == 401


def test_the_page_and_the_pictures_are_shut_with_the_story(play):
    assert play.alice.get("/story/finale").status_code == 404
    assert play.alice.get(f"{STORY}/finale/pic/p1").status_code == 404


def test_a_meter_below_one_hundred_percent_does_not_open_it(play):
    open_with_the_meter(play, target=2)
    assert guide_meter(play.alice) == 0.5
    assert get(play.alice).status_code == 404


def test_no_target_means_the_meter_cannot_open_it(play):
    challenge_id = Programme.query.filter_by(slug="lantern_walk").one().challenge_id
    user = Users.query.filter_by(name="alice").one()
    gen_solve(db, user_id=user.id, team_id=user.team_id, challenge_id=challenge_id)
    clear_standings()
    assert guide_meter(play.alice) == 0 and get(play.alice).status_code == 404


def test_a_trigger_that_is_not_an_on_spelling_does_not_open_it(play):
    for value in ("0", "false", "no", "off", "", "maybe", "2"):
        set_config(TRIGGER, value)
        assert get(play.alice).status_code == 404, value


def test_before_the_start_nothing_opens_it_not_even_the_trigger(play):
    set_config(TRIGGER, True)
    with clock(T_START - 3600):
        assert get(play.alice).status_code == 404 and play.alice.get(STORY).get_json()["data"]["stories"] == []


# ---- open -----------------------------------------------------------------------------------------------------------------

def test_the_crews_trigger_opens_the_story_the_pictures_the_page_and_the_list(play):
    set_config(TRIGGER, True)
    r = get(play.alice)
    assert r.status_code == 200 and r.get_json()["data"]["title"] == "Last Light" and len(r.get_json()["data"]["panels"]) == 3
    assert play.alice.get(f"{STORY}/finale/pic/p3").get_data() == play.files["p3"]
    page = play.alice.get("/story/finale")
    assert page.status_code == 200 and "Last Light" in page.get_data(as_text=True)
    stories = play.alice.get(STORY).get_json()["data"]["stories"]
    assert [s["slug"] for s in stories] == ["finale", "street"] and stories[0] == {"slug": "finale", "title": "Last Light", "kicker": "LAST CALL", "panels": 3}


@pytest.mark.parametrize("value", [True, "1", "true", "yes", "on", "TRUE", " On "])
def test_every_on_spelling_of_the_trigger_opens_it(play, value):
    set_config(TRIGGER, value)
    assert get(play.alice).status_code == 200


def test_taking_the_trigger_back_shuts_it_again(play):
    set_config(TRIGGER, True)
    assert get(play.alice).status_code == 200
    set_config(TRIGGER, False)
    assert get(play.alice).status_code == 404


def test_the_meter_at_one_hundred_percent_opens_it_without_the_trigger(play):
    open_with_the_meter(play, target=1)
    assert guide_meter(play.alice) == 1
    assert get(play.alice).status_code == 200 and play.alice.get(f"{STORY}/finale/pic/p1").status_code == 200
    assert "finale" in [s["slug"] for s in play.alice.get(STORY).get_json()["data"]["stories"]]


def test_the_meter_does_not_open_it_when_the_crew_switched_the_meter_off(play):
    open_with_the_meter(play, target=1)
    set_config(METER, False)
    assert get(play.alice).status_code == 404
    set_config(TRIGGER, True)
    assert get(play.alice).status_code == 200, "the trigger still works with the meter off"


def test_with_the_meter_switched_off_or_no_target_nothing_is_measured_at_all(play, monkeypatch):
    """The answer is no either way (the Guide has no story block then); the point is that nobody pays for working that out."""
    from CTFd.plugins.l3mon_board import guide
    from CTFd.plugins.l3mon_story import finale

    open_with_the_meter(play, target=1)
    calls = []
    real = guide.meter
    monkeypatch.setattr(guide, "meter", lambda viewer: calls.append(1) or real(viewer))
    set_config(METER, False)
    assert get(play.alice).status_code == 404
    set_config(METER, True)
    set_config(TARGET, 0)
    finale.forget()
    assert get(play.alice).status_code == 404
    assert calls == [], "the meter is off or has no target: the gate does not measure it"
    set_config(TARGET, 1)
    assert get(play.alice).status_code == 200 and calls == [1], "and with both set it does"


def test_a_player_without_a_studio_may_watch_it_too(play):
    from story_world import player_without_studio

    set_config(TRIGGER, True)
    carol = player_without_studio(play.app, "carol")
    assert get(carol).status_code == 200


def test_a_suspended_player_is_refused_even_when_it_is_open(play):
    set_config(TRIGGER, True)
    Users.query.filter_by(name="alice").first().banned = True
    db.session.commit()
    r = get(play.alice)
    assert r.status_code == 403 and r.get_json()["error"] == "banned"


# ---- the crew ---------------------------------------------------------------------------------------------------------------

def test_the_crew_can_preview_the_finale_at_any_time_and_before_the_start(play):
    assert get(play.admin).status_code == 200
    with clock(T_START - 3600):
        assert get(play.admin).status_code == 200 and play.admin.get(f"{STORY}/finale/pic/p1").status_code == 200


def test_the_finale_does_not_need_a_channel_and_the_plan_refuses_a_channel_of_that_name(play):
    assert Channel.query.filter_by(slug="finale").count() == 0
    assert get(play.alice).status_code == 404
    r = play.admin.put("/api/v1/l3mon/admin/programmes", json={"channels": [{"slug": "finale", "name": "Sneaky", "position": 99}], "programmes": []})
    assert r.status_code == 400 and "channels[0].slug" in r.get_json()["errors"], r.get_data(as_text=True)


def test_even_a_channel_that_got_that_name_by_some_other_road_is_neither_listed_nor_given_a_button_while_the_finale_is_shut(play, monkeypatch):
    """The second line of defence: the plan refuses the slug, but if a row had it the channel rule must not list or open the finale."""
    from CTFd.plugins.l3mon_story import api

    monkeypatch.setattr(api, "on_air_channels", lambda solved: {"street", "finale"})
    who = SimpleNamespace(admin=False, solved=set(), team=None, user=None)
    assert sorted(api.available(who)) == ["street"], "the finale is not on air through the channel rule"
    assert api.story_for(who, "finale") is None
    assert store.panels_of("finale") is None, "so the board shows no button for it"
    assert store.panels_of("street") == 2


# ---- no story, no finale ----------------------------------------------------------------------------------------------------

def test_with_no_finale_file_the_trigger_opens_nothing_and_nothing_breaks(play):
    import os

    os.remove(os.path.join(play.folder, "finale.json"))
    store.reset()
    set_config(TRIGGER, True)
    assert get(play.alice).status_code == 404
    assert [s["slug"] for s in play.alice.get(STORY).get_json()["data"]["stories"]] == ["street"]


def test_the_gate_does_not_work_out_the_meter_when_there_is_no_finale_to_show(play, monkeypatch):
    import os

    from CTFd.plugins.l3mon_board import guide

    os.remove(os.path.join(play.folder, "finale.json"))
    store.reset()
    calls = []
    real = guide.build
    monkeypatch.setattr(guide, "build", lambda viewer: calls.append(1) or real(viewer))
    assert get(play.alice).status_code == 404 and play.alice.get(STORY).status_code == 200
    assert play.alice.get(f"{STORY}/street").status_code == 200
    assert calls == [], "no finale file: nothing to decide, so no work"


def test_a_picture_of_another_channel_is_not_reachable_through_the_finale(play):
    set_config(TRIGGER, True)
    assert play.alice.get(f"{STORY}/finale/pic/sun").status_code == 404
    assert json.loads(get(play.alice).get_data())["data"]["slug"] == "finale"


# ---- what a finale request costs --------------------------------------------------------------------------------------------------------------

def test_the_meter_is_worked_out_without_building_the_whole_guide(play, monkeypatch):
    """Everybody asks at the same moment, when the meter reaches 100 percent: the gate needs one number, not the studio's whole Guide."""
    from CTFd.plugins.l3mon_board import guide

    set_config(TARGET, 1)
    calls = {"build": 0, "meter": 0}
    real_build, real_meter = guide.build, guide.meter
    monkeypatch.setattr(guide, "build", lambda viewer: calls.update(build=calls["build"] + 1) or real_build(viewer))
    monkeypatch.setattr(guide, "meter", lambda viewer: calls.update(meter=calls["meter"] + 1) or real_meter(viewer))
    assert get(play.alice).status_code == 404
    assert calls == {"build": 0, "meter": 1}, calls


def test_a_players_answer_is_kept_for_a_few_seconds_but_the_crews_trigger_is_never_waited_for(play, monkeypatch):
    from CTFd.plugins.l3mon_board import guide
    from CTFd.plugins.l3mon_story import finale

    set_config(TARGET, 2)
    now = {"t": 1000.0}
    monkeypatch.setattr(finale, "time", SimpleNamespace(monotonic=lambda: now["t"]))
    finale.forget()
    calls = []
    real = guide.meter
    monkeypatch.setattr(guide, "meter", lambda viewer: calls.append(1) or real(viewer))
    for _ in range(4):
        assert get(play.alice).status_code == 404
    assert len(calls) == 1, "nine requests of one viewing cost one measurement, not nine"
    now["t"] += finale.KEEP_SECONDS + 1
    assert get(play.alice).status_code == 404 and len(calls) == 2, "and it is asked again after a few seconds"
    set_config(TRIGGER, True)
    assert get(play.alice).status_code == 200, "the crew's trigger opens it at once, whatever was kept"
    set_config(TRIGGER, False)
    assert get(play.alice).status_code == 404 and len(calls) == 2, "and shutting it again does not need a new measurement either"


def test_the_kept_answers_are_few_and_a_forgotten_one_is_measured_again(play, monkeypatch):
    from CTFd.plugins.l3mon_story import finale

    finale.forget()
    set_config(TARGET, 1)
    for n in range(finale.MOST_KEPT + 50):
        finale._kept[n + 1] = (10**9, False)
    assert len(finale._kept) > finale.MOST_KEPT
    get(play.alice)
    assert len(finale._kept) <= finale.MOST_KEPT, "a thousand teams cannot grow it without end"


def test_the_finale_page_says_finale_and_a_channels_page_still_says_cold_open(play):
    set_config(TRIGGER, True)
    assert "<title>Last Light · Finale</title>" in play.alice.get("/story/finale").get_data(as_text=True)
    assert "· Cold open</title>" in play.alice.get("/story/street").get_data(as_text=True)


def test_each_studio_is_measured_for_itself_and_not_given_another_studios_answer(play, monkeypatch):
    from CTFd.plugins.l3mon_board import guide
    from CTFd.plugins.l3mon_story import finale

    set_config(TARGET, 2)
    finale.forget()
    asked = []
    real = guide.meter
    monkeypatch.setattr(guide, "meter", lambda viewer: asked.append(viewer.team.id if viewer.team is not None else 0) or real(viewer))
    bob = team_client(play.app, "bob", "studio-b")
    assert get(play.alice).status_code == 404 and get(bob).status_code == 404
    assert len(asked) == 2 and len(set(asked)) == 2, asked
