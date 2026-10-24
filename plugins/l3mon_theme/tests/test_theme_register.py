"""The registration page (Account), email verification, and studio setup (SP6 stream B).

Tests for Stream B, round 2 (audit fixes B1-B10).
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
from CTFd.plugins.l3mon_guard.models import Evidence
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


# =========================================================================
# Step 1: Account (/register) & Route Aliases (B6, B8, B9)
# =========================================================================


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


def test_register_account_route_alias_is_get_only(app):
    """B8: Aliases must not accept POST to prevent split rate-limiting."""
    with app.app_context():
        with app.test_client() as client:
            # GET redirects 302 to CTFd's /register
            r_get = client.get("/register/account")
            assert r_get.status_code == 302
            assert "/register" in r_get.headers["Location"]

            # POST is refused (405 Method Not Allowed)
            r_post = client.post("/register/account", data={"name": "test", "password": PASSWORD})
            assert r_post.status_code == 405


def test_register_verify_route_alias_is_get_only(app):
    """B8: Aliases must not accept POST to prevent split rate-limiting."""
    with app.app_context():
        with app.test_client() as client:
            # GET redirects 302 to CTFd's /confirm
            r_get = client.get("/register/verify")
            assert r_get.status_code == 302
            assert "/confirm" in r_get.headers["Location"]

            # POST is refused (405 Method Not Allowed)
            r_post = client.post("/register/verify", data={"go": "resend"})
            assert r_post.status_code == 405


def test_register_rules_acceptance_enforced_server_side(app):
    """B6: Server refuses sign-up without privacy=1 with V_MSG.rules before user creation."""
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/register").get_data(as_text=True)
            nonce = nonce_in(body)
            initial_count = Users.query.count()

            # Attempt registration WITHOUT privacy checkbox
            r = client.post(
                "/register",
                data={
                    "nonce": nonce,
                    "username": "rules_tester",
                    "email": "rules@example.com",
                    "password": PASSWORD,
                },
            )
            assert r.status_code == 200
            text = r.get_data(as_text=True)
            assert "Tick the box to accept the rules." in text
            assert 'href="#f-privacy"' in text
            assert 'id="f-privacy-e"' in text
            # User was not created
            assert Users.query.count() == initial_count
            assert Users.query.filter_by(name="rules_tester").first() is None


def test_register_captcha_enforced_when_configured(app):
    """B9: Turnstile CAPTCHA check is enforced (not only drawn) when keys are set."""
    with mock.patch.dict(os.environ, KEYS):
        with app.app_context():
            with app.test_client() as client:
                body = client.get("/register").get_data(as_text=True)
                assert 'class="cf-turnstile"' in body
                nonce = nonce_in(body)
                initial_count = Users.query.count()

                # POST without turnstile response token
                r = client.post(
                    "/register",
                    data={
                        "nonce": nonce,
                        "username": "captcha_tester",
                        "email": "captcha@example.com",
                        "password": PASSWORD,
                        "privacy": "1",
                    },
                )
                assert r.status_code == 200
                text = r.get_data(as_text=True)
                assert Users.query.count() == initial_count
                assert Users.query.filter_by(name="captcha_tester").first() is None


def test_register_with_duplicate_user_shows_summary_and_error(app):
    """B7 & B9: Duplicate user attaches to username field; no vacuous or assertions."""
    with app.app_context():
        with app.test_client() as client:
            body = client.get("/register").get_data(as_text=True)
            nonce = nonce_in(body)
            initial_count = Users.query.count()

            r = client.post(
                "/register",
                data={
                    "nonce": nonce,
                    "username": "existing_player",
                    "email": "unique@example.com",
                    "password": PASSWORD,
                    "privacy": "1",
                },
            )
            text = r.get_data(as_text=True)
            assert r.status_code == 200
            assert REG_H in text
            assert "data-summary" in text
            assert 'href="#f-username"' in text
            assert 'id="f-username-e"' in text
            assert 'value="existing_player"' in text
            assert PASSWORD not in text
            # Assert user count did not change
            assert Users.query.count() == initial_count


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
                    "privacy": "1",
                },
            )
            assert r.status_code == 302
            user = Users.query.filter_by(name="fresh_player").first()
            assert user is not None
            assert user.email == "fresh@example.com"


def test_register_error_table_field_mapping_vs_summary_only(app):
    """B7: Exact messages in table map to fields; unknown messages go to summary only."""
    with app.app_context():
        with app.test_client() as client:
            # 1. Exact message in table: "That email has already been used" -> maps to #f-email
            body = client.get("/register").get_data(as_text=True)
            nonce = nonce_in(body)
            r = client.post(
                "/register",
                data={
                    "nonce": nonce,
                    "username": "fresh_tester",
                    "email": "player@example.com",
                    "password": PASSWORD,
                    "privacy": "1",
                },
            )
            assert r.status_code == 200
            text = r.get_data(as_text=True)
            assert 'href="#f-email"' in text
            assert 'id="f-email-e"' in text

            # 2. Unknown error: simulated rendering with unknown message -> goes to summary only (#reg-h)
            with app.test_request_context("/register"):
                rendered = app.jinja_env.get_template("register.html").render(
                    errors=["Some unmapped general server error"],
                    l3mon={"csrf": "token", "assets": {"css.app": "", "js.boot": "", "js.register": ""}, "signed_in": False, "player": False, "me": None, "event_name": "CTF", "site_url": "", "build_sha": "1", "captcha": {}, "ctftime_on": False},
                )
                assert 'href="#reg-h"' in rendered
                assert 'href="#f-username"' not in rendered
                assert 'href="#f-email"' not in rendered
                assert 'href="#f-password"' not in rendered


# =========================================================================
# Step 2: Email Verification (/confirm) (B4, B5, B9)
# =========================================================================


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


def test_confirm_mail_unavailable_shows_plain_message(app):
    """B4: When mail cannot be sent, CTFd answers confirm with plain error, not 'Link not found'."""
    with app.app_context():
        user = Users.query.filter_by(name="existing_player").first()
        user.verified = False
        db.session.commit()

        client = login_as_user(app, "existing_player", PASSWORD)
        with mock.patch("CTFd.auth.can_send_mail", return_value=False):
            r = client.get("/confirm")
            assert r.status_code == 200
            body = r.get_data(as_text=True)
            assert "Email verification is enabled but email sending isn't available" in body
            assert "Link not found" not in body


def test_confirm_link_invalid_renders_link_status_page(app):
    """B4: Invalid confirmation token renders link status page (Link not found)."""
    with app.app_context():
        with app.test_client() as client:
            with mock.patch("CTFd.auth.can_send_mail", return_value=True):
                r = client.get("/confirm/completely-bogus-token-data")
                assert r.status_code == 200
                body = r.get_data(as_text=True)
                assert REG_H in body
                assert "Link not found" in body
                assert "data-link=\"invalid\"" in body
                assert "Step 2 of 3" not in body


def test_confirm_no_fake_link_state(app):
    """B4: ?link_state=ok or used cannot fake email confirmation."""
    with app.app_context():
        user = Users.query.filter_by(name="existing_player").first()
        user.verified = False
        db.session.commit()

        client = login_as_user(app, "existing_player", PASSWORD)
        with mock.patch("CTFd.auth.can_send_mail", return_value=True):
            r = client.get("/confirm?link_state=ok")
            assert r.status_code == 200
            body = r.get_data(as_text=True)
            assert "Email confirmed" not in body
            assert "Check your email" in body


# =========================================================================
# Step 3: Studio (/register/studio & /teams/*) (B1, B2, B7, B9)
# =========================================================================


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
        assert '<option value="in"' in body


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


def test_studio_creation_dispatches_through_ctfd_teams_new(app):
    """B1: Studio creation creates team via CTFd's own view function with random secret password."""
    with app.app_context():
        client = login_as_user(app, "existing_player", PASSWORD)
        r_get = client.get("/register/studio")
        nonce = nonce_in(r_get.get_data(as_text=True))

        r_create = client.post(
            "/register/studio",
            data={
                "nonce": nonce,
                "mode": "create",
                "name": "Solar Studio",
                "country": "in",
                "affiliation": "Lemon Academy",
                "ctftime": "12345",
            },
        )
        assert r_create.status_code == 302
        team = Teams.query.filter_by(name="Solar Studio").first()
        assert team is not None
        assert team.country == "IN"
        assert team.affiliation == "Lemon Academy"
        # CTFtime canonical URL stored
        assert team.website == "https://ctftime.org/team/12345"
        # Secret random password injected
        assert team.password is not None and len(team.password) > 0


def test_studio_creation_refuses_malicious_website(app):
    """B1: javascript: website is refused by CTFd validators.validate_url."""
    with app.app_context():
        register_user(app, name="xss_player", email="xss@example.com", password=PASSWORD)
        client = login_as_user(app, "xss_player", PASSWORD)
        nonce = nonce_in(client.get("/register/studio").get_data(as_text=True))

        r = client.post(
            "/register/studio",
            data={
                "nonce": nonce,
                "mode": "create",
                "name": "XSS Studio",
                "ctftime": "javascript:alert(document.domain)",
            },
        )
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert "That does not look like a CTFtime team page or number." in text
        assert Teams.query.filter_by(name="XSS Studio").first() is None


def test_studio_creation_refuses_long_website_without_500(app):
    """B1: A 200-character website is refused with a message (not a 500 error)."""
    with app.app_context():
        register_user(app, name="long_url_player", email="long@example.com", password=PASSWORD)
        client = login_as_user(app, "long_url_player", PASSWORD)
        nonce = nonce_in(client.get("/register/studio").get_data(as_text=True))

        long_ctf = "https://ctftime.org/team/" + "9" * 200
        r = client.post(
            "/register/studio",
            data={
                "nonce": nonce,
                "mode": "create",
                "name": "Long URL Studio",
                "ctftime": long_ctf,
            },
        )
        assert r.status_code == 200
        assert "That does not look like a CTFtime team page or number." in r.get_data(as_text=True)
        assert Teams.query.filter_by(name="Long URL Studio").first() is None


def test_studio_creation_prototype_limits(app):
    """B1: Enforce studio name 2-32 chars and affiliation <= 64 chars."""
    with app.app_context():
        register_user(app, name="limits_player", email="limits@example.com", password=PASSWORD)
        client = login_as_user(app, "limits_player", PASSWORD)
        nonce = nonce_in(client.get("/register/studio").get_data(as_text=True))

        # Too short name "A"
        r1 = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "A"})
        assert r1.status_code == 200
        assert "Studio names are 2 to 32 characters." in r1.get_data(as_text=True)

        # Too long name (33 characters)
        r2 = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "A" * 33})
        assert r2.status_code == 200
        assert "Studio names are 2 to 32 characters." in r2.get_data(as_text=True)

        # Too long affiliation (65 characters)
        r3 = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "Valid Name", "affiliation": "X" * 65})
        assert r3.status_code == 200
        assert "Affiliations are at most 64 characters." in r3.get_data(as_text=True)


def test_studio_creation_guard_blocks_admin_and_records_evidence(app):
    """B1: Guard blocks 'admin' and records 'name_policy_blocked' evidence."""
    with app.app_context():
        register_user(app, name="admin_impersonator", email="imp@example.com", password=PASSWORD)
        client = login_as_user(app, "admin_impersonator", PASSWORD)
        nonce = nonce_in(client.get("/register/studio").get_data(as_text=True))

        r = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "admin"})
        assert r.status_code == 200
        assert "That team name is reserved." in r.get_data(as_text=True)

        # Verify evidence recorded
        ev = Evidence.query.filter_by(kind="name_policy_blocked").first()
        assert ev is not None
        assert "name=admin" in ev.detail


def test_studio_join_with_real_invite_code_and_full_url(app):
    """B2: Join box accepts raw token or full invite URL with case preserved."""
    with app.app_context():
        # Captain creates studio
        client1 = login_as_user(app, "existing_player", PASSWORD)
        nonce1 = nonce_in(client1.get("/register/studio").get_data(as_text=True))
        client1.post("/register/studio", data={"nonce": nonce1, "mode": "create", "name": "Invite Studio"})

        team = Teams.query.filter_by(name="Invite Studio").first()
        assert team is not None
        code = team.get_invite_code()
        assert code and any(c.islower() for c in code) and any(c.isupper() for c in code), "code is case-sensitive"

        # Teammate 1 joins via raw code
        register_user(app, name="teammate_raw", email="raw@example.com", password=PASSWORD)
        client2 = login_as_user(app, "teammate_raw", PASSWORD)
        nonce2 = nonce_in(client2.get("/register/studio").get_data(as_text=True))
        r_join = client2.post(
            "/register/studio",
            data={"nonce": nonce2, "mode": "join", "invite": f"  {code}  "},
        )
        assert r_join.status_code == 302
        assert f"/teams/invite?code={code}" in r_join.headers["Location"]

        # Teammate 2 joins via full invite URL
        register_user(app, name="teammate_url", email="url@example.com", password=PASSWORD)
        client3 = login_as_user(app, "teammate_url", PASSWORD)
        nonce3 = nonce_in(client3.get("/register/studio").get_data(as_text=True))
        r_url = client3.post(
            "/register/studio",
            data={
                "nonce": nonce3,
                "mode": "join",
                "invite": f"https://play.l3m0nctf.xyz/teams/invite?code={code}",
            },
        )
        assert r_url.status_code == 302
        assert f"/teams/invite?code={code}" in r_url.headers["Location"]


def test_studio_error_table_field_mapping_vs_summary_only(app):
    """B7: Exact messages in team_enrollment FIELD_MAP attach to fields; others to summary only."""
    with app.app_context():
        client = login_as_user(app, "existing_player", PASSWORD)
        with app.test_request_context("/register/studio"):
            # 1. Exact message in table: "That team name is already taken." -> attached to #f-name
            rendered1 = app.jinja_env.get_template("teams/team_enrollment.html").render(
                errors=["That team name is already taken."],
                l3mon={"csrf": "token", "assets": {"css.app": "", "js.boot": "", "js.register": ""}, "signed_in": True, "player": True, "me": {}, "event_name": "CTF", "site_url": "", "build_sha": "1", "countries": []},
            )
            assert 'href="#f-name"' in rendered1
            assert 'id="f-name-e"' in rendered1

            # 2. General message not in table: "Team creation is currently disabled" -> summary only
            rendered2 = app.jinja_env.get_template("teams/team_enrollment.html").render(
                errors=["Team creation is currently disabled"],
                l3mon={"csrf": "token", "assets": {"css.app": "", "js.boot": "", "js.register": ""}, "signed_in": True, "player": True, "me": {}, "event_name": "CTF", "site_url": "", "build_sha": "1", "countries": []},
            )
            assert 'href="#reg-h"' in rendered2
            assert 'href="#f-name"' not in rendered2
            assert 'href="#f-invite"' not in rendered2


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
            team_queries = [q for q in queries if "teams" in q.lower()]
            assert len(team_queries) <= 1, f"executed {len(team_queries)} team queries: {team_queries}"
        finally:
            event.remove(db.engine, "before_cursor_execute", on)
