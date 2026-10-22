"""The registration page (Account) and studio availability API (SP6 stream B).

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted:
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_theme_register.py
"""
import os
import re
from unittest import mock

import pytest
from sqlalchemy import event

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_guard import turnstile
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

REG_H = 'id="reg-h"'
PASSWORD = "a-long-test-password"
KEYS = {"TURNSTILE_SITEKEY": turnstile.CF_TEST_PASS_SITEKEY, "TURNSTILE_SECRET": turnstile.CF_TEST_PASS_SECRET}


@pytest.fixture
def app():
    """A CTFd app with the theme and our plugins."""
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    register_user(app, name="existing_player", email="player@example.com", password=PASSWORD)
    yield app
    destroy_ctfd(app)


def nonce_in(body):
    m = re.search(r'name="nonce" value="([^"]+)"', body)
    assert m, "form has a hidden nonce"
    return m.group(1)


def test_the_register_page_is_the_theme_and_has_csrf_and_stepper(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/register").get_data(as_text=True)
            assert REG_H in body
            assert "Create your account" in body
            assert "ACCOUNT · CH 000" in body
            assert "Step 1 of 3: Account" in body
            assert 'id="f-username"' in body
            assert 'name="username"' in body
            assert 'id="f-email"' in body
            assert 'id="f-password"' in body
            with client.session_transaction() as sess:
                token = sess["nonce"]
            assert nonce_in(body) == token
            assert f'<meta name="csrf-token" content="{token}">' in body

            # strict CSP & hygiene checks
            assert "<style" not in body
            assert " style=" not in body
            assert "javascript:" not in body
            assert not re.search(r"\son[a-z]+\s*=", body)
            assert "prototype" not in body.lower()
            assert "mock" not in body.lower()


def test_register_account_route_alias_renders_theme_page(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/register/account").get_data(as_text=True)
            assert REG_H in body
            assert "Create your account" in body


def test_register_with_duplicate_user_shows_summary_and_error(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/register").get_data(as_text=True)
            nonce = nonce_in(body)
            r = client.post(
                "/register",
                data={
                    "nonce": nonce,
                    "username": "existing_player",
                    "email": "unique@example.com",
                    "password": PASSWORD,
                },
            )
            text = r.get_data(as_text=True)
            assert r.status_code == 200
            assert REG_H in text
            assert 'data-summary' in text or 'summary' in text
            assert 'value="existing_player"' in text
            assert PASSWORD not in text


def test_register_successful_with_username_form_field(app):
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/register").get_data(as_text=True)
            nonce = nonce_in(body)
            r = client.post(
                "/register",
                data={
                    "nonce": nonce,
                    "username": "fresh_player",
                    "email": "fresh@example.com",
                    "password": PASSWORD,
                },
            )
            # CTFd redirects to challenges (or confirm depending on verify_emails)
            assert r.status_code == 302
            user = Users.query.filter_by(name="fresh_player").first()
            assert user is not None
            assert user.email == "fresh@example.com"


def test_studio_availability_unauthenticated_returns_401(app):
    with app.app_context():
        with app.test_client() as client:
            r = client.get("/api/v1/l3mon/teams/available?name=MyStudio")
            assert r.status_code == 401
            assert r.get_json()["error"] == "auth_required"


def test_studio_availability_authenticated_lifecycle(app):
    with app.app_context():
        client = login_as_user(app, "existing_player", PASSWORD)

        # 1. Invalid empty/too short
        r = client.get("/api/v1/l3mon/teams/available?name=")
        assert r.status_code == 200
        assert r.get_json()["data"] == {"available": False, "reason": "invalid"}

        r = client.get("/api/v1/l3mon/teams/available?name=A")
        assert r.status_code == 200
        assert r.get_json()["data"] == {"available": False, "reason": "invalid"}

        # 2. Reserved name
        r = client.get("/api/v1/l3mon/teams/available?name=admin")
        assert r.status_code == 200
        assert r.get_json()["data"] == {"available": False, "reason": "taken"}

        r = client.get("/api/v1/l3mon/teams/available?name=Crew")
        assert r.status_code == 200
        assert r.get_json()["data"] == {"available": False, "reason": "taken"}

        # 3. Valid and free
        r = client.get("/api/v1/l3mon/teams/available?name=Solar%20Flare")
        assert r.status_code == 200
        assert r.get_json()["data"] == {"available": True, "reason": None}

        # 4. Create team and check taken (case-insensitive & whitespace collapsing)
        team = Teams(name="Solar Flare")
        db.session.add(team)
        db.session.commit()

        r = client.get("/api/v1/l3mon/teams/available?name=solar%20flare")
        assert r.status_code == 200
        assert r.get_json()["data"] == {"available": False, "reason": "taken"}

        r = client.get("/api/v1/l3mon/teams/available?name=%20%20Solar%20%20%20Flare%20%20")
        assert r.status_code == 200
        assert r.get_json()["data"] == {"available": False, "reason": "taken"}


def test_studio_availability_query_budget(app):
    with app.app_context():
        client = login_as_user(app, "existing_player", PASSWORD)
        queries = []

        def on(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        event.listen(db.engine, "before_cursor_execute", on)
        try:
            r = client.get("/api/v1/l3mon/teams/available?name=SomeUnusedTeam")
            assert r.status_code == 200
            assert r.get_json()["data"]["available"] is True
            # Budget: at most 1 statement for the team lookup
            team_queries = [q for q in queries if "teams" in q.lower()]
            assert len(team_queries) <= 1, f"executed {len(team_queries)} team queries: {team_queries}"
        finally:
            event.remove(db.engine, "before_cursor_execute", on)


def test_register_captcha_when_configured(app):
    with mock.patch.dict(os.environ, KEYS):
        with app.app_context():
            with app.test_client() as client:
                body = client.get("/register").get_data(as_text=True)
                assert 'class="cf-turnstile"' in body
                assert turnstile.CF_TEST_PASS_SITEKEY in body
                assert "challenges.cloudflare.com/turnstile/v0/api.js" in body

    with mock.patch.dict(os.environ, {"TURNSTILE_SITEKEY": "", "TURNSTILE_SECRET": ""}):
        with app.app_context():
            with app.test_client() as client:
                body = client.get("/register").get_data(as_text=True)
                assert 'class="cf-turnstile"' not in body
                assert "challenges.cloudflare.com" not in body


def test_confirm_verify_page_rendering_and_stepper(app):
    with app.app_context():
        user = Users.query.filter_by(name="existing_player").first()
        user.verified = False
        db.session.commit()

        client = login_as_user(app, "existing_player", PASSWORD)
        with mock.patch("CTFd.auth.can_send_mail", return_value=True):
            r = client.get("/confirm")
            assert r.status_code == 200
            body = r.get_data(as_text=True)
            assert REG_H in body
            assert "Check your email" in body
            assert "VERIFY · CH 000" in body
            assert "Step 2 of 3: Verify" in body
            assert "I have clicked the link" in body
            assert "Resend email" in body
            assert "player@example.com" in body
            assert nonce_in(body)

            # Strict CSP
            assert "<style" not in body
            assert " style=" not in body
            assert "javascript:" not in body


def test_confirm_link_invalid_renders_link_status_page(app):
    with app.app_context():
        with app.test_client() as client:
            with mock.patch("CTFd.auth.can_send_mail", return_value=True):
                r = client.get("/confirm/completely-bogus-token-data")
                assert r.status_code == 200
                body = r.get_data(as_text=True)
                assert REG_H in body
                assert "Link not found" in body or "invalid" in body
                assert "VERIFY · CH 000" in body
                assert "data-link=\"invalid\"" in body
                # Stepper is hidden for link verification state
                assert "Step 2 of 3" not in body


def test_teams_enrollment_choice_page(app):
    with app.app_context():
        client = login_as_user(app, "existing_player", PASSWORD)
        r = client.get("/team")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert REG_H in body
        assert "Set up your studio" in body
        assert "STUDIO · CH 000" in body
        assert "Step 3 of 3: Studio" in body
        assert "Create a studio" in body
        assert "Join with an invite code" in body
        assert 'id="f-name"' in body
        assert 'id="f-country"' in body
        assert 'id="f-affiliation"' in body
        assert 'id="f-invite"' in body
        assert '<option value="in"' in body  # populated from l3mon.countries


def test_teams_new_and_join_routes_render_studio(app):
    with app.app_context():
        client = login_as_user(app, "existing_player", PASSWORD)
        r_new = client.get("/teams/new")
        assert r_new.status_code == 200
        body_new = r_new.get_data(as_text=True)
        assert REG_H in body_new
        assert "Set up your studio" in body_new
        assert 'value="create" id="m-create" checked' in body_new

        r_join = client.get("/teams/join")
        assert r_join.status_code == 200
        body_join = r_join.get_data(as_text=True)
        assert REG_H in body_join
        assert "Set up your studio" in body_join
        assert 'value="join" id="m-join" checked' in body_join


def test_studio_creation_and_invite_joining_lifecycle(app):
    with app.app_context():
        # 1. Existing player creates "Solar Studio" via /register/studio
        client1 = login_as_user(app, "existing_player", PASSWORD)
        r_get = client1.get("/register/studio")
        assert r_get.status_code == 200
        nonce = nonce_in(r_get.get_data(as_text=True))

        r_create = client1.post(
            "/register/studio",
            data={
                "nonce": nonce,
                "mode": "create",
                "name": "Solar Studio",
                "country": "in",
                "affiliation": "Lemon Academy",
            },
        )
        assert r_create.status_code == 302
        team = Teams.query.filter_by(name="Solar Studio").first()
        assert team is not None
        assert team.country == "IN"
        assert team.affiliation == "Lemon Academy"
        assert team.password is not None and len(team.password) > 0  # random secret password

        # 2. Register teammate and join via invite code
        register_user(app, name="teammate", email="teammate@example.com", password=PASSWORD)
        team = Teams.query.filter_by(name="Solar Studio").first()
        code = team.get_invite_code()
        assert code

        client2 = login_as_user(app, "teammate", PASSWORD)

        # GET /teams/invite?code=...
        r_inv = client2.get(f"/teams/invite?code={code}")
        assert r_inv.status_code == 200
        inv_body = r_inv.get_data(as_text=True)
        assert REG_H in inv_body
        assert "Solar Studio" in inv_body
        assert "Join Solar Studio" in inv_body
        inv_nonce = nonce_in(inv_body)

        # POST /teams/invite?code=...
        r_join = client2.post(f"/teams/invite?code={code}", data={"nonce": inv_nonce})
        assert r_join.status_code == 302

        u2 = Users.query.filter_by(name="teammate").first()
        assert u2.team_id == team.id


def test_studio_creation_guard_rejections(app):
    with app.app_context():
        register_user(app, name="guard_test_player", email="guard@example.com", password=PASSWORD)
        client = login_as_user(app, "guard_test_player", PASSWORD)
        nonce = nonce_in(client.get("/register/studio").get_data(as_text=True))

        # 1. Reserved name "admin"
        r = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "admin"})
        assert r.status_code == 403
        body = r.get_data(as_text=True)
        assert "reserved" in body.lower() or "taken" in body.lower()

        # 2. Too short name "A"
        r = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "A"})
        assert r.status_code == 403
        body = r.get_data(as_text=True)
        assert "2 to 32" in body or "invalid" in body.lower()
