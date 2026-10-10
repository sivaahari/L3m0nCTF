"""The page that plays a story, the player's files, and the board's "Cold open" button.

A visitor who opens the page is sent to the registration page and comes back afterwards. A player gets the shell with the story's words as
text (so it reads without script, and for anyone who prefers text). The player's files are public engine code and hold no story. The board
tells a studio how many panels a channel's cold open has, only for a channel that is on air and has a story, and never carries a field
called `story`.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_story
"""
import re
from types import SimpleNamespace

import pytest

from CTFd.plugins.l3mon_story import store
from story_world import STORY, T_LIVE, T_START, bundle, clock, load_plan, make_app, release_street, started, team_client, write_story
from tests.helpers import destroy_ctfd, login_as_user

BOARD = "/api/v1/l3mon/board"
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


# ---- the page ---------------------------------------------------------------------------------------------------------------

def test_a_visitor_is_sent_to_the_registration_page_and_brought_back_after(play):
    r = play.visitor.get("/story/street")
    assert r.status_code == 303 and r.headers["Location"] == "/register?next=/story/street"
    r = play.visitor.get("/story/snack")
    assert r.status_code == 303 and r.headers["Location"] == "/register?next=/story/snack", "the same for every channel: nothing is learned"


@pytest.mark.parametrize("slug", ["Street", "a b", "../x", "x" * 50, "street.json", "STREET"])
def test_a_name_that_is_not_a_channel_name_is_a_plain_404_even_for_a_visitor(play, slug):
    assert play.visitor.get(f"/story/{slug}").status_code in (404, 308, 301)


def test_a_player_gets_the_page_with_the_words_as_text(play):
    r = play.alice.get("/story/street")
    assert r.status_code == 200 and r.mimetype == "text/html"
    html = r.get_data(as_text=True)
    assert MARKER in html and "Panel 1: a round sun over a dark field." in html and "Host:" in html
    assert 'data-story-url="/api/v1/l3mon/story/street"' in html
    assert r.headers["Cache-Control"] == "private, no-cache"


def test_the_pages_text_is_escaped_so_a_story_can_never_put_markup_on_it(play):
    obj = bundle("street", title=MARKER)
    obj["panels"][0]["bubbles"][0]["text"] = "Fish &amp; chips & 'quotes' \"too\""
    write_story(play.folder, "street", obj)
    store.reset()
    html = play.alice.get("/story/street").get_data(as_text=True)
    assert "Fish &amp;amp; chips &amp; &#39;quotes&#39; &#34;too&#34;" in html
    assert "<img" not in html.split("<section")[1]


def test_the_page_has_no_inline_script_and_loads_only_its_own_files(play):
    html = play.alice.get("/story/street").get_data(as_text=True)
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts == ['<script type="module" src="/plugins/l3mon_story/assets/comic.js">'], scripts
    assert "<style" not in html and " style=" not in html and "onclick" not in html.lower()
    links = re.findall(r"""(?:src|href)="([^"]+)\"""", html)
    assert all(link.startswith("/plugins/l3mon_story/assets/") for link in links), links


def test_the_page_for_a_channel_not_on_air_is_a_404_and_says_nothing(play):
    r = play.alice.get("/story/snack")
    assert r.status_code == 404 and "Hidden Noodles" not in r.get_data(as_text=True)
    assert play.alice.get("/story/never-heard-of-it").status_code == 404


def test_the_crew_can_preview_the_page_of_a_channel_that_is_not_on_air(play):
    r = play.admin.get("/story/snack")
    assert r.status_code == 200 and "Hidden Noodles" in r.get_data(as_text=True)


def test_before_the_start_even_a_channel_on_air_has_no_page_for_a_player(play):
    with clock(T_START - 3600):
        assert play.alice.get("/story/street").status_code == 404


# ---- the player's files ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,mime",
    [("comic.js", "javascript"), ("timeline.js", "javascript"), ("sounds.js", "javascript"), ("comic.css", "text/css")],
)
def test_the_players_files_are_public_and_hold_no_story(play, name, mime):
    r = play.visitor.get(f"/plugins/l3mon_story/assets/{name}")
    assert r.status_code == 200 and mime in r.mimetype
    body = r.get_data(as_text=True)
    assert MARKER not in body and "Hidden Noodles" not in body and "sun over a dark field" not in body
    assert "eval(" not in body and "new Function" not in body and "innerHTML" not in body and "document.write" not in body, "the player builds the page from text nodes only"


def test_the_assets_route_refuses_to_leave_its_folder(play):
    for path in ("../__init__.py", "..%2f__init__.py", "../store.py", "%2e%2e/format.py", "../../l3mon_core/models.py"):
        assert play.visitor.get(f"/plugins/l3mon_story/assets/{path}").status_code in (400, 404), path


# ---- the board's button ------------------------------------------------------------------------------------------------------

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


def channels(client):
    return {c["slug"]: c for c in client.get(BOARD).get_json()["data"]["channels"]}


def test_the_board_says_how_many_panels_a_channel_on_air_has_and_nothing_else(play):
    street = channels(play.alice)["street"]
    assert street["cold_open"] == {"panels": 2}
    assert "story" not in keys_of(play.alice.get(BOARD).get_json()), "the board JSON never carries a field called story"
    assert MARKER not in play.alice.get(BOARD).get_data(as_text=True)


def test_a_channel_with_nothing_on_air_has_no_button_even_though_it_has_a_story(play):
    assert channels(play.alice)["snack"]["cold_open"] is None
    assert "Hidden Noodles" not in play.alice.get(BOARD).get_data(as_text=True)


def test_a_channel_that_is_on_air_but_has_no_story_has_no_button(play):
    import os

    os.remove(f"{play.folder}/street.json")
    store.reset()
    assert channels(play.alice)["street"]["cold_open"] is None


def test_before_the_start_there_is_no_button(play):
    with clock(T_START - 3600):
        assert all(c["cold_open"] is None for c in channels(play.alice).values())


def test_the_list_agrees_with_the_board(play):
    on_board = {slug for slug, c in channels(play.alice).items() if c["cold_open"]}
    listed = {s["slug"] for s in play.alice.get(STORY).get_json()["data"]["stories"]}
    assert on_board == listed == {"street"}
