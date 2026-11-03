"""Tests for programme story isolation, query budget, and escapement.

Ensures:
- l3mon.story is present on challenge detail API (GET /api/v1/challenges/<id>) for visible programmes.
- Empty story defaults to empty string ("").
- Withheld and locked programmes return 404 and never leak story text.
- Board JSON (GET /api/v1/l3mon/board) never contains programme story fields.
- Guide JSON (GET /api/v1/l3mon/guide) never contains programme story fields.
- Query budget: reading the challenge detail / l3mon block costs zero extra database statements.
- Hostile markup and characters in story are safely escaped in the no-script page (/board/<slug>).
"""
import json
from types import SimpleNamespace
import pytest
from sqlalchemy import event

from CTFd.models import db
from CTFd.plugins.l3mon_core.models import Programme
from board_world import BOARD, T_LIVE, clock, make_app, on_air, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user


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
            bob = team_client(app, "bob", "studio-b")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, bob=bob)
    destroy_ctfd(app)


def test_visible_programme_carries_story_in_l3mon_block(play):
    prog = Programme.query.filter_by(slug="lantern_walk").one()
    prog.story = "A lantern flickers in the misty square."
    db.session.commit()

    r = play.alice.get(f"/api/v1/challenges/{play.ids.lantern}")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert "l3mon" in data
    assert data["l3mon"]["story"] == "A lantern flickers in the misty square."


def test_programme_with_no_story_carries_empty_string(play):
    prog = Programme.query.filter_by(slug="lantern_walk").one()
    prog.story = ""
    db.session.commit()

    r = play.alice.get(f"/api/v1/challenges/{play.ids.lantern}")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["l3mon"]["story"] == ""


def test_withheld_and_locked_programmes_answer_404_and_do_not_leak_story(play):
    # hush_alley is withheld
    hush_prog = Programme.query.filter_by(slug="hush_alley").one()
    hush_prog.story = "Secret withheld story line."
    db.session.commit()

    r = play.alice.get(f"/api/v1/challenges/{play.ids.hush}")
    assert r.status_code == 404
    assert "Secret withheld story line" not in r.get_data(as_text=True)

    # locked programme: dumpling_gate has prerequisite noodle_ledger, not solved by bob
    dumpling_prog = Programme.query.filter_by(slug="dumpling_gate").one()
    dumpling_prog.story = "Secret locked story line."
    db.session.commit()

    r_bob = play.bob.get(f"/api/v1/challenges/{play.ids.dumpling}")
    assert r_bob.status_code == 404
    assert "Secret locked story line" not in r_bob.get_data(as_text=True)


def test_board_json_never_contains_programme_story(play):
    prog = Programme.query.filter_by(slug="lantern_walk").one()
    prog.story = "A lantern flickers in the misty square."
    db.session.commit()

    r = play.alice.get(BOARD)
    assert r.status_code == 200
    board_data = r.get_json()["data"]
    # Check that no programme in board JSON has a 'story' key
    for p in board_data.get("programmes", []):
        assert "story" not in p, f"Programme {p.get('slug')} leaked story in board JSON"
    # Ensure raw text does not contain our story line
    assert "A lantern flickers in the misty square." not in r.get_data(as_text=True)


def test_guide_json_never_contains_programme_story(play):
    prog = Programme.query.filter_by(slug="lantern_walk").one()
    prog.story = "A lantern flickers in the misty square."
    db.session.commit()

    r = play.alice.get("/api/v1/l3mon/guide")
    assert r.status_code == 200
    assert "A lantern flickers in the misty square." not in r.get_data(as_text=True)


def test_query_budget_reading_challenge_detail_has_no_extra_queries(play):
    prog = Programme.query.filter_by(slug="lantern_walk").one()
    prog.story = "A lantern flickers in the misty square."
    db.session.commit()

    # Warm cache
    play.alice.get(f"/api/v1/challenges/{play.ids.lantern}")

    queries = []

    def on(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    listen = event.listen(db.engine, "before_cursor_execute", on)
    try:
        r = play.alice.get(f"/api/v1/challenges/{play.ids.lantern}")
    finally:
        event.remove(db.engine, "before_cursor_execute", on)

    assert r.status_code == 200
    # Exactly what CTFd and l3mon panel.block need (block() uses a single query joining Programme and Channel)
    # The story column is part of Programme table and adds 0 queries.
    prog_queries = [q for q in queries if "l3mon_programme" in q]
    assert len(prog_queries) == 1, f"Expected 1 programme query, got {len(prog_queries)}"


def test_noscript_page_shows_story_and_escapes_markup(play):
    from CTFd.utils import set_config

    with play.app.app_context():
        set_config("ctf_theme", "l3mon")

    prog = Programme.query.filter_by(slug="lantern_walk").one()
    prog.story = "A curious story with & special <script>alert(1)</script> characters."
    db.session.commit()

    r = play.alice.get("/board/lantern_walk")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "In the story" in html
    # Check escaping: <script> must be escaped, not rendered as a raw tag
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html or "alert(1)" in html
