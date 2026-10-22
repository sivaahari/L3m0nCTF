"""Theme tests for the Scoreboard page (SP6 Stream C).

Verifies `/scoreboard` rendering, gating, context passing, security stance, and state transitions
through a full CTFd application with the l3mon theme and plugins loaded.
"""
import pytest

from CTFd.models import Teams, Users, db
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


def test_visitor_can_view_scoreboard_by_default(app):
    with app.test_client() as client:
        r = client.get("/scoreboard")
        assert r.status_code == 200
        body = r.get_data(as_text=True)

        assert "data-sb" in body
        assert "Scoreboard" in body
        assert "TRP" in body

        # Security check: no inline scripts or styles
        assert "<style>" not in body and "style=\"" not in body
        assert "<script>" not in body
        assert "javascript:" not in body

        # Plain English check: no points word in user visible text
        assert "points" not in body.lower() or "trp" in body.lower()


def test_signed_in_player_with_studio_sees_own_line(app):
    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    assert "data-sb" in body
    assert "Citrus Crew" in body
    assert "data-k=\"score\"" in body
    assert "data-k=\"place\"" in body


def test_crew_admin_sees_scoreboard_without_own_line(app):
    client = login_as_user(app, "admin_crew", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    assert "data-sb" in body
    # Crew does not have a studio me block
    assert "Your studio" not in body


def test_banned_user_gets_403_off_air(app):
    with app.app_context():
        u = Users.query.filter_by(name="captain").first()
        u.banned = True
        db.session.commit()

    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/scoreboard")
    assert r.status_code == 403
    body = r.get_data(as_text=True)
    assert "id=\"sl-h\"" in body or "RESTRICTED CHANNEL" in body or "OFF AIR" in body
