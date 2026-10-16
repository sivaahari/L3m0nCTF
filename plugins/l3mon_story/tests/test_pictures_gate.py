"""Who may see the painted pictures of a channel's cold open: the same people and the same times as the story itself, and nobody else.

`GET /api/v1/l3mon/story/<slug>/pic/<id>` sends one WebP. It has the story's gate in front of it: a visitor is told to sign in, a channel
that is not on air, a channel with no story, a picture the story does not name and a name that is not an id all give the same 404, so
nothing about a story that is not available can be learned, and the crew can preview any picture at any time.

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
    STORY, T_END, T_LIVE, T_START, bundle, clock, load_plan, make_app, release_street, started, team_client, webp, write_pic_story, write_story,
)
from tests.helpers import destroy_ctfd, login_as_user


@pytest.fixture()
def play(tmp_path, monkeypatch):
    folder = tmp_path / "story"
    folder.mkdir()
    monkeypatch.setenv("L3MON_STORY_DIR", str(folder))
    store.reset()
    street, street_files = write_pic_story(str(folder), "street", title="Picture Hour")
    snack, snack_files = write_pic_story(str(folder), "snack", title="Hidden Noodles")
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            load_plan(admin)
            release_street(admin)
            alice = team_client(app, "alice", "studio-a")
            yield SimpleNamespace(app=app, admin=admin, alice=alice, folder=str(folder), visitor=app.test_client(), files=street_files, snack_files=snack_files)
    destroy_ctfd(app)
    store.reset()


def pic(client, slug="street", name="p1", **kw):
    return client.get(f"{STORY}/{slug}/pic/{name}", **kw)


def same_answer(responses):
    seen = set()
    for r in responses:
        body = r.get_json()
        body.pop("request_id", None)
        seen.add((r.status_code, json.dumps(body, sort_keys=True)))
    return seen


# ---- who ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["street/pic/p1", "snack/pic/p1", "never-heard-of-it/pic/p1", "street/pic/p9"])
def test_a_visitor_is_told_to_sign_in_whatever_is_asked_so_nothing_is_learned(play, path):
    r = play.visitor.get(f"{STORY}/{path}")
    assert r.status_code == 401 and r.mimetype == "application/json" and r.get_json()["error"] == "auth_required"


def test_a_registered_player_gets_the_picture_as_a_webp_with_a_strong_tag(play):
    r = pic(play.alice)
    assert r.status_code == 200 and r.mimetype == "image/webp" and r.get_data() == play.files["p1"]
    assert r.headers["ETag"].startswith('"i') and r.headers["ETag"].endswith('"')
    assert r.headers["Cache-Control"] == "private, max-age=300"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "Cookie" in r.headers["Vary"] and r.headers["Content-Length"] == str(len(play.files["p1"]))
    assert r.headers["Content-Disposition"].startswith("inline")


def test_every_picture_of_the_story_can_be_fetched(play):
    for name, data in play.files.items():
        assert pic(play.alice, name=name).get_data() == data


def test_asking_again_with_the_tag_answers_304_with_no_body(play):
    etag = pic(play.alice).headers["ETag"]
    again = pic(play.alice, headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.get_data() == b"" and again.headers["ETag"] == etag
    assert pic(play.alice, headers={"If-None-Match": '"i-other"'}).status_code == 200


def test_an_unverified_player_is_refused_when_verification_is_required(play):
    set_config("verify_emails", True)
    Users.query.filter_by(name="alice").first().verified = False
    db.session.commit()
    r = pic(play.alice)
    assert r.status_code == 403 and r.get_json()["error"] == "unverified"


def test_a_suspended_player_is_refused_in_json(play):
    Users.query.filter_by(name="alice").first().banned = True
    db.session.commit()
    r = pic(play.alice)
    assert r.status_code == 403 and r.get_json()["error"] == "banned"


def test_only_get_is_allowed(play):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(play.alice, method)(f"{STORY}/street/pic/p1", json={}).status_code in (404, 405)


# ---- when -----------------------------------------------------------------------------------------------------------------

def test_a_channel_not_on_air_an_unknown_channel_and_an_unknown_picture_all_give_the_same_404(play):
    answers = same_answer([
        pic(play.alice, "snack"), pic(play.alice, "never-heard-of-it"), pic(play.alice, "street", "p9"), pic(play.alice, "street", "P1"),
        pic(play.alice, "street", "p1.webp"), pic(play.alice, "Street"), pic(play.alice, "street", "sun"), pic(play.alice, "a" * 80),
    ])
    assert len(answers) == 1 and next(iter(answers))[0] == 404, "the same words for every one: nothing about a story that is not on air can be learned"
    assert "Hidden Noodles" not in str(answers)
    for path in ("../etc/passwd", "street/pic/../../x", "street/pic/%2e%2e", "street/pic/p1/extra", "street/pic"):
        assert play.alice.get(f"{STORY}/{path}").status_code == 404


def test_before_the_start_no_picture_is_available_even_to_a_player(play):
    with clock(T_START - 3600):
        assert pic(play.alice).status_code == 404


def test_while_paused_the_pictures_are_still_available(play):
    set_config("paused", True)
    assert pic(play.alice).status_code == 200


def test_pulling_the_channels_last_programme_back_takes_its_pictures_away_again(play):
    from CTFd.plugins.l3mon_core.models import Programme

    programme = Programme.query.filter_by(slug="lantern_walk").one().id
    r = play.admin.put("/api/v1/l3mon/admin/release", json={"changes": [{"kind": "programme", "id": programme, "mode": "withhold"}]})
    assert r.status_code == 200
    assert pic(play.alice).status_code == 404


def test_after_the_end_every_channel_that_has_a_programme_has_its_pictures_again(play):
    with clock(T_END + 60):
        play.alice.get("/api/v1/l3mon/ticks")
        assert pic(play.alice, "snack").status_code == 200 and pic(play.alice).status_code == 200


def test_the_crew_can_preview_any_picture_at_any_time(play):
    with clock(T_START - 3600):
        assert pic(play.admin, "snack").status_code == 200 and pic(play.admin).status_code == 200
    assert pic(play.admin, "never-heard-of-it").status_code == 404 and pic(play.admin, "street", "p9").status_code == 404


# ---- the folder -----------------------------------------------------------------------------------------------------------

def test_a_picture_swapped_on_the_server_is_not_sent(play):
    assert pic(play.alice).status_code == 200  # the folder was looked at (the clock of a test starts far from the real one, so the first request looks again)
    with open(os.path.join(play.folder, "pics", "street", "p1.webp"), "wb") as handle:
        handle.write(webp(9))
    assert pic(play.alice).status_code == 404, "the file no longer matches the checksum in the bundle"
    assert pic(play.alice, name="p2").status_code == 200


def test_a_story_whose_picture_is_missing_is_not_available_at_all(play):
    os.remove(os.path.join(play.folder, "pics", "street", "p2.webp"))
    store.reset()
    assert pic(play.alice).status_code == 404 and play.alice.get(f"{STORY}/street").status_code == 404
    assert play.alice.get(STORY).get_json()["data"]["stories"] == []


def test_a_drawn_story_has_no_pictures_to_fetch(play):
    write_story(play.folder, "street", bundle("street", title="Drawn Hour"))
    store.reset()
    assert play.alice.get(f"{STORY}/street").status_code == 200
    assert pic(play.alice, name="sun").status_code == 404 and pic(play.alice, name="p1").status_code == 404


# ---- what the story and the list say ------------------------------------------------------------------------------------

def test_the_story_names_its_pictures_and_never_says_where_they_are_kept(play):
    r = play.alice.get(f"{STORY}/street")
    data = r.get_json()["data"]
    assert data["v"] == 2 and data["flip"] is True and set(data["pics"]) == {"p1", "p2"}
    text = r.get_data(as_text=True)
    assert play.folder not in text and "pics/" not in text and ".webp" not in text


def test_the_list_holds_only_what_is_available_and_only_its_title_its_kicker_and_its_length(play):
    stories = play.alice.get(STORY).get_json()["data"]["stories"]
    assert stories == [{"slug": "street", "title": "Picture Hour", "kicker": "CH 01 · Street", "panels": 2}]


# ---- the page --------------------------------------------------------------------------------------------------------------

def test_the_page_shows_the_pictures_to_a_browser_without_scripts_and_the_painted_words_as_text(play):
    r = play.alice.get("/story/street")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "<noscript>" in html
    assert html.count('src="/api/v1/l3mon/story/street/pic/p') == 2 and 'loading="lazy"' in html
    assert "Line 1 sets the scene." in html and "Host:" in html and "Picture 2: a round sun over a dark field." in html


def test_the_page_of_a_drawn_story_has_no_pictures_in_it(play):
    write_story(play.folder, "street", bundle("street", title="Drawn Hour"))
    store.reset()
    html = play.alice.get("/story/street").get_data(as_text=True)
    assert "<noscript>" not in html and "/pic/" not in html


def test_the_page_of_a_picture_story_that_is_not_available_is_a_404(play):
    assert play.alice.get("/story/snack").status_code == 404
