"""Who may open a channel's cold open, and when.

Only a registered, signed-in player, and only once the channel has something on air and the broadcast has started. The crew can preview any
story at any time. An unknown channel, a channel with nothing on air and a time before the start all give the same 404, so nothing about a
story that is not available can be learned. A visitor is told to sign in (the API) or sent to the registration page (the page). A broken
file never takes a request down; the story is simply not available.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_story
"""
import json
import os
from types import SimpleNamespace

import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_story import store
from CTFd.utils import set_config
from story_world import (
    STORY, T_END, T_LIVE, T_START, bundle, clock, load_plan, make_app, player_without_studio, release_street, started, team_client, write_story,
)
from tests.helpers import destroy_ctfd, login_as_user

MARKER = "Moth Hour Marker"


@pytest.fixture()
def play(tmp_path, monkeypatch):
    folder = tmp_path / "story"
    folder.mkdir()
    monkeypatch.setenv("L3MON_STORY_DIR", str(folder))
    store.reset()
    write_story(str(folder), "street", title=MARKER)
    write_story(str(folder), "snack", title="Hidden Noodles")
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            load_plan(admin)
            release_street(admin)
            alice = team_client(app, "alice", "studio-a")
            yield SimpleNamespace(app=app, admin=admin, alice=alice, folder=str(folder), visitor=app.test_client())
    destroy_ctfd(app)
    store.reset()


def get(client, slug="street", **kw):
    return client.get(f"{STORY}/{slug}", **kw)


# ---- who ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [STORY, f"{STORY}/street", f"{STORY}/snack", f"{STORY}/never-heard-of-it"])
def test_a_visitor_is_told_to_sign_in_whatever_is_asked_so_nothing_is_learned(play, path):
    r = play.visitor.get(path)
    assert r.status_code == 401 and r.mimetype == "application/json" and r.get_json()["error"] == "auth_required"


def test_a_registered_player_gets_the_story_as_json_with_an_etag(play):
    r = get(play.alice)
    assert r.status_code == 200 and r.mimetype == "application/json"
    body = r.get_json()["data"]
    assert body["slug"] == "street" and body["title"] == MARKER and len(body["panels"]) == 2 and "sun" in body["art"]
    assert r.headers["Cache-Control"] == "private, no-cache" and r.headers["ETag"].startswith('"s') and r.headers["X-Request-Id"]
    assert r.headers["Content-Type"].startswith("application/json")


def test_the_answer_is_exactly_the_checked_bundle_and_nothing_else(play):
    body = get(play.alice).get_json()["data"]
    assert body == json.loads(json.dumps(bundle("street", title=MARKER)))


def test_a_player_who_has_not_made_a_studio_yet_may_watch_it_too(play):
    carol = player_without_studio(play.app, "carol")
    assert get(carol).status_code == 200


def test_an_unverified_player_is_refused_when_verification_is_required(play):
    set_config("verify_emails", True)
    Users.query.filter_by(name="alice").first().verified = False
    db.session.commit()
    r = get(play.alice)
    assert r.status_code == 403 and r.get_json()["error"] == "unverified"


def test_a_suspended_player_is_refused_in_json(play):
    Users.query.filter_by(name="alice").first().banned = True
    db.session.commit()
    r = get(play.alice)
    assert r.status_code == 403 and r.get_json()["error"] == "banned"


def test_asking_again_with_the_etag_answers_304_with_no_body(play):
    etag = get(play.alice).headers["ETag"]
    again = get(play.alice, headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.get_data() == b"" and again.headers["ETag"] == etag
    assert get(play.alice, headers={"If-None-Match": '"s-other"'}).status_code == 200


def test_only_get_is_allowed(play):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(play.alice, method)(f"{STORY}/street", json={}).status_code in (404, 405)


# ---- when -----------------------------------------------------------------------------------------------------------------

def test_a_channel_with_nothing_on_air_an_unknown_channel_and_a_bad_name_all_give_the_same_404(play):
    answers = []
    for slug in ("snack", "never-heard-of-it", "Street", "street.json", "a" * 80):
        r = get(play.alice, slug)
        assert r.status_code == 404, slug
        body = r.get_json()
        body.pop("request_id")
        answers.append(json.dumps(body, sort_keys=True))
    assert len(set(answers)) == 1, "the same words for every one: nothing about a story that is not on air can be learned"
    assert "Hidden Noodles" not in "".join(answers)
    for slug in ("../etc/passwd", "a/b", "%2e%2e"):
        assert get(play.alice, slug).status_code == 404, "a name that cannot even be routed is CTFd's own 404"


def test_before_the_start_nothing_is_available_even_to_a_player(play):
    with clock(T_START - 3600):
        assert get(play.alice).status_code == 404 and play.alice.get(STORY).get_json()["data"]["stories"] == []


def test_while_paused_it_is_still_available(play):
    set_config("paused", True)
    assert get(play.alice).status_code == 200


def test_pulling_the_channels_last_programme_back_makes_the_story_unavailable_again(play):
    from CTFd.plugins.l3mon_core.models import Programme

    programme = Programme.query.filter_by(slug="lantern_walk").one().id
    r = play.admin.put("/api/v1/l3mon/admin/release", json={"changes": [{"kind": "programme", "id": programme, "mode": "withhold"}]})
    assert r.status_code == 200
    assert get(play.alice).status_code == 404


def test_after_the_end_every_channel_that_has_a_programme_has_its_story_again(play):
    with clock(T_END + 60):
        play.alice.get("/api/v1/l3mon/ticks")  # the first request after the end lets the scheduler apply the plan's rule
        assert get(play.alice, "snack").status_code == 200 and get(play.alice).status_code == 200


def test_the_crew_can_preview_any_story_at_any_time(play):
    with clock(T_START - 3600):
        assert get(play.admin, "snack").status_code == 200 and get(play.admin).status_code == 200
    assert get(play.admin, "never-heard-of-it").status_code == 404


# ---- the list -------------------------------------------------------------------------------------------------------------

def test_the_list_holds_only_what_is_available_and_only_its_title_its_kicker_and_its_length(play):
    r = play.alice.get(STORY)
    assert r.status_code == 200
    stories = r.get_json()["data"]["stories"]
    assert stories == [{"slug": "street", "title": MARKER, "kicker": "CH 01 · Street", "panels": 2}]
    assert "Hidden Noodles" not in r.get_data(as_text=True)


# ---- the folder -----------------------------------------------------------------------------------------------------------

def test_a_broken_file_is_not_available_and_does_not_hurt_the_others(play, caplog):
    with open(os.path.join(play.folder, "snack.json"), "w", encoding="utf-8") as handle:
        handle.write("{ not json")
    store.reset()
    assert get(play.alice).status_code == 200 and get(play.alice, "snack").status_code == 404


def test_a_file_that_fails_the_checks_is_not_available(play):
    obj = bundle("street", title=MARKER)
    obj["art"]["sun"] = obj["art"]["sun"].replace("<rect", "<script>alert(1)</script><rect")
    write_story(play.folder, "street", obj)
    store.reset()
    assert get(play.alice).status_code == 404, "the file was edited on the server into something unsafe: nobody sees it"


def test_a_file_whose_slug_is_not_its_name_is_ignored(play):
    write_story(play.folder, "street", bundle("snack", title="Wrong Room"))
    store.reset()
    assert get(play.alice).status_code == 404


def test_a_link_to_a_file_outside_the_folder_is_ignored(play, tmp_path):
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps(bundle("street", title="Smuggled")), encoding="utf-8")
    os.remove(os.path.join(play.folder, "street.json"))
    os.symlink(str(outside), os.path.join(play.folder, "street.json"))
    store.reset()
    assert get(play.alice).status_code == 404


def test_a_file_that_is_too_large_is_ignored_without_being_read(play):
    with open(os.path.join(play.folder, "street.json"), "wb") as handle:
        handle.write(b" " * 700_000)
    store.reset()
    assert get(play.alice).status_code == 404


def test_an_edited_file_is_served_after_the_next_look_at_the_folder(play):
    write_story(play.folder, "street", title="New Title")
    store.reset()
    assert get(play.alice).get_json()["data"]["title"] == "New Title"


def test_with_no_folder_at_all_nothing_is_available_and_nothing_breaks(play, monkeypatch):
    monkeypatch.delenv("L3MON_STORY_DIR")
    store.reset()
    assert get(play.alice).status_code == 404 and play.alice.get(STORY).get_json()["data"]["stories"] == []


def test_only_files_named_like_a_channel_are_looked_at(play):
    for name in ("Street.json", "street.json.bak", ".street.json", "street .json", "str eet.json", "x" * 60 + ".json"):
        with open(os.path.join(play.folder, name), "w", encoding="utf-8") as handle:
            json.dump(bundle("street", title="Stray"), handle)
    store.reset()
    assert [s["slug"] for s in play.alice.get(STORY).get_json()["data"]["stories"]] == ["street"]
