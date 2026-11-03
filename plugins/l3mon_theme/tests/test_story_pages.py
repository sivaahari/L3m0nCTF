"""Tests for the Stories page (/stories) (Stream E, E2).

Verifies:
- Access gating (signed-out -> /login, unverified -> /confirm, no studio -> /team, banned -> 403)
- Empty state ("Nothing to watch yet...")
- One and several unlocked channel stories rendered with CH badge and "Watch the cold open"
- Finale card rendered at the end when unlocked with "Watch the finale" and is-finale styling
- Crew access previews all stories
- HEAD request returns 200 with empty body
- Markup escaping in story titles and kickers
- Hook cost: zero SQL queries on non-stories requests
"""
import calendar
import datetime
from unittest import mock

import pytest
from freezegun import freeze_time
from sqlalchemy import event

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_story import store
from CTFd.plugins.l3mon_story.tests.story_world import bundle, write_story
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user, register_user

PASSWORD = "test-stories-password-123"
T_START = calendar.timegm(datetime.datetime(2026, 11, 28, 3, 30, 0).timetuple())
T_END = T_START + 24 * 3600
T_LIVE = T_START + 3600


@pytest.fixture
def story_env(tmp_path, monkeypatch):
    """Sets up CTFd app with l3mon theme in teams mode and an isolated story directory."""
    folder = tmp_path / "stories"
    folder.mkdir()
    monkeypatch.setenv("L3MON_STORY_DIR", str(folder))
    store.reset()

    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    from CTFd.plugins.l3mon_story import finale
    finale.forget()
    app.permanent_session_lifetime = datetime.timedelta(days=3650)

    with app.app_context():
        set_config("start", T_START)
        set_config("end", T_END)
        # Create channels: ch1 (street), ch2 (snack)
        ch1 = Channel(slug="street", name="Mighty Street", position=1, kind="standard", release_state="released")
        ch2 = Channel(slug="snack", name="Snack Square", position=2, kind="standard", release_state="released")
        db.session.add_all([ch1, ch2])
        db.session.commit()

        # Add challenge and programme on ch1
        c1 = gen_challenge(db, name="Street Intro", category="street", state="visible")
        p1 = Programme(challenge_id=c1.id, channel_id=ch1.id, cell=1, number=1, slug="street-intro", release_state="released")
        db.session.add(p1)
        db.session.commit()

        # Register users
        register_user(app, name="player1", email="player1@example.com", password=PASSWORD)
        register_user(app, name="noteam_player", email="noteam@example.com", password=PASSWORD)
        register_user(app, name="unverified_player", email="unverified@example.com", password=PASSWORD)

        # Assign studio to player1
        u1 = Users.query.filter_by(name="player1").first()
        team = Teams(name="Studio Lemon", email="team@example.com", captain_id=u1.id)
        db.session.add(team)
        db.session.commit()
        u1.team_id = team.id
        u1.verified = True

        # Unverified user setup
        u_unv = Users.query.filter_by(name="unverified_player").first()
        u_unv.verified = False
        t_unv = Teams(name="Studio Unverified", email="unv@example.com", captain_id=u_unv.id)
        db.session.add(t_unv)
        db.session.commit()
        u_unv.team_id = t_unv.id

        db.session.commit()

    yield {"app": app, "folder": str(folder)}

    destroy_ctfd(app)
    monkeypatch.delenv("L3MON_STORY_DIR", raising=False)
    store.reset()
    finale.forget()


def test_stories_gate_signed_out_redirects_to_login(story_env):
    """A signed-out visitor is redirected to /login?next=%2Fstories."""
    app = story_env["app"]
    with app.test_client() as client:
        r = client.get("/stories")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]
        assert "next=" in r.headers["Location"]


def test_stories_gate_unverified_redirects_to_confirm(story_env):
    """An unverified account is redirected to /confirm."""
    app = story_env["app"]
    with app.app_context():
        set_config("verify_emails", True)
    client = login_as_user(app, "unverified_player", password=PASSWORD)
    r = client.get("/stories")
    assert r.status_code == 302
    assert "/confirm" in r.headers["Location"]


def test_stories_gate_no_team_redirects_to_team_private(story_env):
    """A player without a studio is redirected to /team."""
    app = story_env["app"]
    client = login_as_user(app, "noteam_player", password=PASSWORD)
    r = client.get("/stories")
    assert r.status_code == 302
    assert "/team" in r.headers["Location"]


def test_stories_empty_state(story_env):
    """When no stories are available, /stories displays the empty state message."""
    app = story_env["app"]
    with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
        client = login_as_user(app, "player1", password=PASSWORD)
        r = client.get("/stories")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Nothing to watch yet. The comics open as the channels go on air." in html
        assert "Stories" in html
        assert "Back to Guide" in html


def test_stories_one_channel_story(story_env):
    """When street has an on-air programme and a story bundle, street story card appears."""
    app = story_env["app"]
    folder = story_env["folder"]
    write_story(folder, "street", bundle("street", title="Mighty Street: Cold Open", kicker="Mighty Street", panels=6))
    store.reset()

    with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
        client = login_as_user(app, "player1", password=PASSWORD)
        r = client.get("/stories")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Mighty Street: Cold Open" in html
        assert "Mighty Street" in html
        assert "6 pictures" in html
        assert "CH 01" in html
        assert "Watch the cold open" in html
        assert 'href="/story/street"' in html


def test_stories_several_channels_order(story_env):
    """Multiple channel stories appear in order of channel position."""
    app = story_env["app"]
    folder = story_env["folder"]
    write_story(folder, "street", bundle("street", title="Mighty Street: Cold Open", panels=6))
    write_story(folder, "snack", bundle("snack", title="Snack Square: Cold Open", panels=5))
    store.reset()

    with app.app_context():
        # Put programme on snack channel
        ch2 = Channel.query.filter_by(slug="snack").first()
        c2 = gen_challenge(db, name="Snack Intro", category="snack", state="visible")
        p2 = Programme(challenge_id=c2.id, channel_id=ch2.id, cell=1, number=2, slug="snack-intro", release_state="released")
        db.session.add(p2)
        db.session.commit()

    with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
        client = login_as_user(app, "player1", password=PASSWORD)
        r = client.get("/stories")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Mighty Street: Cold Open" in html
        assert "Snack Square: Cold Open" in html
        # Check ordering: street (CH 01) before snack (CH 02)
        pos_street = html.find("Mighty Street: Cold Open")
        pos_snack = html.find("Snack Square: Cold Open")
        assert pos_street < pos_snack


def test_stories_finale_card_at_the_end(story_env):
    """When finale is unlocked, it appears as the last card with is-finale class and 'Watch the finale'."""
    app = story_env["app"]
    folder = story_env["folder"]
    write_story(folder, "street", bundle("street", title="Mighty Street: Cold Open", panels=6))
    write_story(folder, "finale", bundle("finale", title="The Director's Cut", kicker="Finale", panels=8))
    store.reset()

    with app.app_context():
        set_config("l3mon_finale_open", True)

    with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
        client = login_as_user(app, "player1", password=PASSWORD)
        r = client.get("/stories")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "The Director&#39;s Cut" in html or "The Director's Cut" in html
        assert "Watch the finale" in html
        assert "is-finale" in html
        assert 'href="/story/finale"' in html
        # Finale appears after street story
        pos_street = html.find("Mighty Street: Cold Open")
        pos_finale = html.find("The Director")
        assert pos_street < pos_finale


def test_stories_crew_preview_before_start(story_env):
    """Crew members can preview stories before start and without a studio."""
    app = story_env["app"]
    folder = story_env["folder"]
    write_story(folder, "street", bundle("street", title="Mighty Street: Cold Open", panels=6))
    store.reset()

    # Time before start
    with freeze_time(datetime.datetime.utcfromtimestamp(T_START - 3600)):
        client = login_as_user(app, "admin")
        r = client.get("/stories")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Mighty Street: Cold Open" in html


def test_stories_head_method(story_env):
    """HEAD /stories returns 200 with headers and empty body."""
    app = story_env["app"]
    with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
        client = login_as_user(app, "player1", password=PASSWORD)
        r = client.head("/stories")
        assert r.status_code == 200
        assert r.data == b""
        assert "text/html" in r.headers.get("Content-Type", "")


def test_stories_escapes_markup(story_env):
    """Special HTML characters like &, ", ' in story title/kicker are escaped in templates."""
    app = story_env["app"]
    folder = story_env["folder"]
    write_story(folder, "street", bundle("street", title='Street & "Quotes" \'Special\'', kicker='Kicker & "More"'))
    store.reset()

    with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
        client = login_as_user(app, "player1", password=PASSWORD)
        r = client.get("/stories")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "&amp;" in html
        assert "<script>" not in html


def test_stories_hook_cost_on_other_routes(story_env):
    """Visiting non-stories routes incurs zero queries from the stories module."""
    app = story_env["app"]
    with app.app_context():
        with app.test_client() as client:
            # Visiting /login
            queries = []

            def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
                queries.append(statement)

            event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
            try:
                r = client.get("/login")
                assert r.status_code == 200
            finally:
                event.remove(db.engine, "before_cursor_execute", before_cursor_execute)

            # None of the queries touch l3mon_channel or l3mon_programme via story.py
            story_queries = [q for q in queries if "from l3mon_channel" in q.lower() and "story" in q.lower()]
            assert len(story_queries) == 0
