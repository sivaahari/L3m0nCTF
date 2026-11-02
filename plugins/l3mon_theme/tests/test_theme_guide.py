"""Theme tests for the Guide page (SP6 Stream C).

Verifies `/guide` route gating, rendering, context passing, security stance, channel keys mapping (C4),
empty news crawl absence (C5), and state transitions through a full CTFd application with the l3mon theme
and plugins loaded.
"""
import re

import pytest

from CTFd.models import Awards, Teams, Users, db
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_theme.pages.guide import channel_keys
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PASSWORD = "test-guide-password-123"


@pytest.fixture
def app():
    """A CTFd app in teams mode with our theme and plugins, a team, and player accounts."""
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    with app.app_context():
        # Register captain and member
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


def test_signed_out_visitor_is_redirected_to_login(app):
    with app.test_client() as client:
        r = client.get("/guide")
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/login?next=%2Fguide")


def test_user_without_studio_is_redirected_to_team_setup(app):
    client = login_as_user(app, "solo_player", PASSWORD)
    r = client.get("/guide")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/team")


def test_unverified_user_is_redirected_to_confirm(app):
    with app.app_context():
        set_config("verify_emails", True)
        u = Users.query.filter_by(name="captain").first()
        u.verified = False
        db.session.commit()

    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/guide")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/confirm")


def test_banned_user_gets_403_off_air(app):
    with app.app_context():
        u = Users.query.filter_by(name="captain").first()
        u.banned = True
        db.session.commit()

    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/guide")
    assert r.status_code == 403
    body = r.get_data(as_text=True)
    assert "id=\"sl-h\"" in body or "RESTRICTED CHANNEL" in body or "OFF AIR" in body


def test_signed_in_player_with_studio_renders_guide(app):
    client = login_as_user(app, "captain", PASSWORD)
    r = client.get("/guide")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    assert "data-guide" in body
    assert "Citrus Crew" in body
    assert "data-k=\"score\"" in body
    assert "data-k=\"place\"" in body
    assert "Channel progress" in body

    # C5 check: empty crawl bar is NOT drawn
    assert '<section class="crawl"' not in body

    # Security check: no inline scripts or styles
    assert "<style>" not in body and "style=\"" not in body
    assert "<script>" not in body
    assert "javascript:" not in body
    # Plain English check: no points word in user visible labels
    assert "points" not in body.lower() or "trp" in body.lower()


def test_crew_admin_renders_guide_without_studio(app):
    client = login_as_user(app, "admin_crew", PASSWORD)
    r = client.get("/guide")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    assert "data-guide" in body
    assert "Crew accounts do not belong to a studio" in body
    assert "Channel progress" in body


def test_channel_keys_helper_maps_no_or_falls_back():
    """C4: test channel_keys when database row IDs differ from channel numbers."""
    channels = [
        {"id": 31, "no": 3, "name": "Alpha"},
        {"id": 7, "no": 1, "name": "Bravo"},
    ]
    keys = channel_keys(channels)
    assert keys == {31: 3, 7: 1}, "Unique valid numbers 0..99 map row id to no"

    # Duplicates fall back to row id
    duplicates = [
        {"id": 31, "no": 3, "name": "Alpha"},
        {"id": 7, "no": 3, "name": "Bravo"},
    ]
    assert channel_keys(duplicates) == {31: 31, 7: 7}

    # Outside 0..99 falls back to row id
    outside = [
        {"id": 31, "no": 105, "name": "Alpha"},
        {"id": 7, "no": 1, "name": "Bravo"},
    ]
    assert channel_keys(outside) == {31: 31, 7: 7}
