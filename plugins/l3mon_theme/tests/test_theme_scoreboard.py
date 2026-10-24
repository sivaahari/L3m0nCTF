"""Theme tests for the Scoreboard page (SP6 Stream C).

Verifies `/scoreboard` gating (C2: player page, visitor -> /login?next=..., unconfirmed -> /confirm,
no studio -> /team, banned -> 403, crew passes), rendering, context passing, security stance,
zero hook cost on other paths, and absence of empty news crawl (C5).
"""
import pytest
from flask import Flask, request

from CTFd.models import Teams, Users, db
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PASSWORD = "test-scoreboard-password-123"


@pytest.fixture
def app():
    """A CTFd app in teams mode with our theme and plugins, studios, and players."""
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    with app.app_context():
        register_user(app, name="captain", email="captain@example.com", password=PASSWORD)
        register_user(app, name="teammate", email="teammate@example.com", password=PASSWORD)
        register_user(app, name="solo_player", email="solo@example.com", password=PASSWORD)

        u_cap = Users.query.filter_by(name="captain").first()
        u_team = Users.query.filter_by(name="teammate").first()

        studio = Teams(name="Citrus Crew", email="citrus@example.com")
        studio.captain_id = u_cap.id
        db.session.add(studio)
        db.session.commit()

        u_cap.team_id = studio.id
        u_team.team_id = studio.id
        db.session.commit()

        # Admin account
        register_user(app, name="admin_crew", email="admin@example.com", password=PASSWORD)
        u_admin = Users.query.filter_by(name="admin_crew").first()
        u_admin.type = "admin"
        db.session.commit()

    yield app
    destroy_ctfd(app)


def test_visitor_is_redirected_to_login(app):
    """C2: visitor without session is redirected to /login with next parameter."""
    with app.test_client() as client:
        r = client.get("/scoreboard")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/login?next=%2Fscoreboard")


def test_user_without_studio_is_redirected_to_team_setup(app):
    """C2: signed-in player without a studio is redirected to /team."""
    client = login_as_user(app, "solo_player", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/team")


def test_unverified_player_is_redirected_to_confirm(app):
    """C2: unverified player is redirected to /confirm when email verification is required."""
    with app.app_context():
        set_config("verify_emails", True)
        u = Users.query.filter_by(name="captain").first()
        u.verified = False
        db.session.commit()

    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/confirm")


def test_banned_user_gets_403_off_air(app):
    """C2: suspended/banned player gets 403 OFF AIR slate."""
    with app.app_context():
        u = Users.query.filter_by(name="captain").first()
        u.banned = True
        db.session.commit()

    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 403
    body = r.get_data(as_text=True)
    assert "id=\"sl-h\"" in body or "RESTRICTED CHANNEL" in body or "OFF AIR" in body


def test_signed_in_player_with_studio_sees_own_line(app):
    """Player with studio passes gate, sees scoreboard and their studio line."""
    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    assert "data-sb" in body
    assert "Citrus Crew" in body
    assert "data-k=\"score\"" in body
    assert "data-k=\"place\"" in body

    # C5 check: empty crawl bar is NOT drawn
    assert '<section class="crawl"' not in body

    # Security check: no inline scripts or styles
    assert "<style>" not in body and "style=\"" not in body
    assert "<script>" not in body
    assert "javascript:" not in body

    # Plain English check: no points word in user visible text
    assert "points" not in body.lower() or "trp" in body.lower()


def test_crew_admin_sees_scoreboard_without_own_line(app):
    """C2: Crew (admin) passes gate without studio, sees scoreboard without studio me block."""
    client = login_as_user(app, "admin_crew", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    assert "data-sb" in body
    assert "Your studio" not in body


def test_scoreboard_gate_hook_costs_nothing_on_other_paths(app):
    """C2: scoreboard hook exits immediately on any path other than /scoreboard."""
    from CTFd.plugins.l3mon_theme.pages.scoreboard import _guard_scoreboard

    with app.test_request_context("/login", method="GET"):
        res = _guard_scoreboard()
        assert res is None, "Hook must return None on other paths"

    with app.test_request_context("/settings", method="GET"):
        res = _guard_scoreboard()
        assert res is None, "Hook must return None on other paths"

    with app.test_request_context("/scoreboard", method="POST"):
        res = _guard_scoreboard()
        assert res is None, "Hook must return None on non-GET/HEAD methods"
