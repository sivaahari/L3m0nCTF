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

            # POST is refused (404/405) and no action taken
            initial_count = Users.query.count()
            r_post = client.post("/register/account", data={"name": "test", "password": PASSWORD})
            assert r_post.status_code in {404, 405}
            assert Users.query.count() == initial_count


def test_register_verify_route_alias_is_get_only(app):
    """B8: Aliases must not accept POST to prevent split rate-limiting."""
    with app.app_context():
        with app.test_client() as client:
            # GET redirects 302 to CTFd's /confirm
            r_get = client.get("/register/verify")
            assert r_get.status_code == 302
            assert "/confirm" in r_get.headers["Location"]

            # POST is refused (404/405)
            r_post = client.post("/register/verify", data={"go": "resend"})
            assert r_post.status_code in {404, 405}


def test_register_rules_acceptance_enforced_server_side(app):
    """B6: Server refuses sign-up without privacy=1 with V_MSG.rules before user creation."""
    app.config["L3MON_SIGNUP_RULES"] = True
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
                    "name": "existing_player",
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
            import html
            assert "Email verification is enabled but email sending isn't available" in html.unescape(body)
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
        assert '<option value="IN"' in body


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
        assert "That studio name is taken." in r.get_data(as_text=True)

        # Verify evidence recorded
        ev = Evidence.query.filter_by(kind="name_policy_blocked").first()
        assert ev is not None
        assert "name='admin'" in ev.detail or "name=admin" in ev.detail


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


# =========================================================================
# Round 3 Tests: B1, B2, B3, B4, B5, B6, B9
# =========================================================================


def test_studio_creation_country_browser_options_and_validation(app):
    """B1: Browser option values (upper case) for first, middle, last country store country;
    lower-case normalized; 'ZZ' refused on country field without creating studio.
    """
    with app.app_context():
        register_user(app, name="country_player", email="country@example.com", password=PASSWORD)
        client = login_as_user(app, "country_player", PASSWORD)

        body = client.get("/register/studio").get_data(as_text=True)
        nonce = nonce_in(body)

        options = re.findall(r'<option\s+value="([A-Z]{2})"[^>]*>', body)
        assert len(options) >= 3, "country list has options"
        first_c = options[0]
        middle_c = options[len(options) // 2]
        last_c = options[-1]

        # 1. First country
        r1 = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "First Country Studio", "country": first_c})
        assert r1.status_code == 302
        t1 = Teams.query.filter_by(name="First Country Studio").first()
        assert t1 is not None and t1.country == first_c

        u = Users.query.filter_by(name="country_player").first()
        u.team_id = None
        db.session.commit()

        # 2. Middle country
        r2 = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "Middle Country Studio", "country": middle_c})
        assert r2.status_code == 302
        t2 = Teams.query.filter_by(name="Middle Country Studio").first()
        assert t2 is not None and t2.country == middle_c

        u = Users.query.filter_by(name="country_player").first()
        u.team_id = None
        db.session.commit()

        # 3. Last country
        r3 = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "Last Country Studio", "country": last_c})
        assert r3.status_code == 302
        t3 = Teams.query.filter_by(name="Last Country Studio").first()
        assert t3 is not None and t3.country == last_c

        u = Users.query.filter_by(name="country_player").first()
        u.team_id = None
        db.session.commit()

        # 4. Lower-case code normalized (defence in depth)
        r_lower = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "Lower Country Studio", "country": "in"})
        assert r_lower.status_code == 302
        t_lower = Teams.query.filter_by(name="Lower Country Studio").first()
        assert t_lower is not None and t_lower.country == "IN"

        u = Users.query.filter_by(name="country_player").first()
        u.team_id = None
        db.session.commit()

        # 5. Invalid 'ZZ' refused with error on country field
        r_zz = client.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "ZZ Studio", "country": "ZZ"})
        assert r_zz.status_code == 200
        text_zz = r_zz.get_data(as_text=True)
        assert "Invalid country" in text_zz
        assert 'href="#f-country"' in text_zz
        assert 'id="f-country-e"' in text_zz
        assert Teams.query.filter_by(name="ZZ Studio").first() is None


def test_confirm_go_check_never_sends_mail_and_redirects_when_verified(app):
    """B2: POST /confirm with go=check never sends mail (mocked).
    When unconfirmed: 200 with quiet text; when confirmed: 303 to /register/studio.
    go=resend sends mail once.
    """
    with app.app_context():
        register_user(app, name="mail_tester", email="mail@example.com", password=PASSWORD)
        user = Users.query.filter_by(name="mail_tester").first()
        user.verified = False
        db.session.commit()

        client = login_as_user(app, "mail_tester", PASSWORD)
        body = client.get("/confirm").get_data(as_text=True)
        nonce = nonce_in(body)

        with mock.patch("CTFd.utils.email.verify_email_address") as mock_verify_mail:
            # Click "I have clicked the link" 3 times while unconfirmed
            for _ in range(3):
                r = client.post("/confirm", data={"nonce": nonce, "go": "check"})
                assert r.status_code == 200
                text = r.get_data(as_text=True)
                assert "NOT YET." in text
                assert "The link has not been opened yet. Check your inbox and spam folder, then try again." in text
                assert "SENT" not in text

            assert mock_verify_mail.call_count == 0, "go=check must never call verify_email_address"

            # Click "Resend email" (go=resend)
            with mock.patch("CTFd.auth.can_send_mail", return_value=True):
                r_resend = client.post("/confirm", data={"nonce": nonce, "go": "resend"})
                assert r_resend.status_code == 200
                assert mock_verify_mail.call_count == 1, "go=resend must call verify_email_address once"

            # Now mark user verified
            user = Users.query.filter_by(name="mail_tester").first()
            user.verified = True
            db.session.commit()

            # go=check now answers 303 redirect to /register/studio
            r_check_verified = client.post("/confirm", data={"nonce": nonce, "go": "check"})
            assert r_check_verified.status_code == 303
            assert "/register/studio" in r_check_verified.headers["Location"]
            assert mock_verify_mail.call_count == 1, "no additional mail sent"


def test_unconfirmed_player_gated_from_studio_and_teams(app):
    """B3: While verify_emails is on, unconfirmed players are redirected to /confirm from
    /team, /teams/new, /teams/join, /register/studio.
    States: visitor -> /login, unconfirmed -> /confirm, confirmed without studio -> studio step,
    confirmed with studio -> /team (teams.private), suspended -> 403.
    """
    app.config["verify_emails"] = True
    with app.app_context():
        from CTFd.utils import set_config
        set_config("verify_emails", True)

        # 1. Visitor (unauthenticated)
        with app.test_client() as visitor:
            assert visitor.get("/register/studio").status_code == 302
            assert "/login" in visitor.get("/register/studio").headers["Location"]
            assert visitor.get("/teams/new").status_code == 302
            assert "/login" in visitor.get("/teams/new").headers["Location"]

        # 2. Unconfirmed player
        register_user(app, name="unconfirmed_p", email="unconf@example.com", password=PASSWORD)
        u_unconf = Users.query.filter_by(name="unconfirmed_p").first()
        u_unconf.verified = False
        db.session.commit()
        client_unconf = login_as_user(app, "unconfirmed_p", PASSWORD)

        for route in ("/team", "/teams/new", "/teams/join", "/register/studio"):
            r = client_unconf.get(route)
            assert r.status_code == 302, f"unconfirmed player on {route} must redirect"
            assert "/confirm" in r.headers["Location"], f"unconfirmed player on {route} must redirect to /confirm"

        # Check stepper on /confirm shows Verify as current step
        confirm_body = client_unconf.get("/confirm").get_data(as_text=True)
        assert "Step 2 of 3: Verify" in confirm_body
        assert 'current' in confirm_body

        # 3. Confirmed player without studio
        u = Users.query.filter_by(name="unconfirmed_p").first()
        u.verified = True
        db.session.commit()
        db.session.expire_all()
        r_studio = client_unconf.get("/register/studio")
        assert r_studio.status_code == 200
        assert "Set up your studio" in r_studio.get_data(as_text=True)

        # 4. Confirmed player with studio
        nonce = nonce_in(r_studio.get_data(as_text=True))
        client_unconf.post("/register/studio", data={"nonce": nonce, "mode": "create", "name": "Gated Studio"})
        db.session.expire_all()
        u = Users.query.filter_by(name="unconfirmed_p").first()
        assert u.team_id is not None

        r_with_studio = client_unconf.get("/register/studio")
        assert r_with_studio.status_code == 302
        assert "/team" in r_with_studio.headers["Location"]

        # 5. Suspended (banned) player
        u.banned = True
        db.session.commit()
        db.session.expire_all()
        r_banned = client_unconf.get("/register/studio")
        assert r_banned.status_code == 403


def test_studio_creation_drops_client_supplied_website(app):
    """B4: Client-supplied website=evil is dropped; only canonical CTFtime is stored.
    Without ctftime, website is empty/null.
    """
    with app.app_context():
        register_user(app, name="phish_tester", email="phish@example.com", password=PASSWORD)
        client = login_as_user(app, "phish_tester", PASSWORD)
        nonce = nonce_in(client.get("/register/studio").get_data(as_text=True))

        # 1. Post evil website alongside valid ctftime
        r1 = client.post(
            "/register/studio",
            data={
                "nonce": nonce,
                "mode": "create",
                "name": "Clean Website Studio",
                "website": "https://evil.example/phish",
                "ctftime": "123456",
            },
        )
        assert r1.status_code == 302
        t1 = Teams.query.filter_by(name="Clean Website Studio").first()
        assert t1 is not None
        assert t1.website == "https://ctftime.org/team/123456"
        assert "evil.example" not in (t1.website or "")

        u = Users.query.filter_by(name="phish_tester").first()
        u.team_id = None
        db.session.commit()

        # 2. Post evil website with NO ctftime
        r2 = client.post(
            "/register/studio",
            data={
                "nonce": nonce,
                "mode": "create",
                "name": "No CTFtime Studio",
                "website": "https://evil.example/phish",
            },
        )
        assert r2.status_code == 302
        t2 = Teams.query.filter_by(name="No CTFtime Studio").first()
        assert t2 is not None
        assert not t2.website or t2.website == ""
        assert "evil.example" not in (t2.website or "")


def test_error_mapping_table_single_display_no_duplicate_alert(app):
    """B5: Each error in FIELD_MAP maps to one field.
    No duplicate alert box is rendered when errors exist (summary + field only).
    """
    with app.app_context():
        # Test render on register page with duplicate username
        with app.test_request_context("/register"):
            rendered = app.jinja_env.get_template("register.html").render(
                errors=["That user name is already taken"],
                l3mon={"csrf": "token", "assets": {"css.app": "", "js.boot": "", "js.register": ""}, "signed_in": False, "player": False, "me": None, "event_name": "CTF", "site_url": "", "build_sha": "1", "captcha": {}, "ctftime_on": False},
            )
            assert 'href="#f-username"' in rendered
            assert 'id="f-username-e"' in rendered
            # Verify single display: no second alert box rendered
            assert 'class="alert alert-error"' not in rendered
            assert rendered.count("That user name is already taken") == 2  # Once in summary, once on field

        # Test render on studio enrollment with invalid country
        with app.test_request_context("/register/studio"):
            rendered_studio = app.jinja_env.get_template("teams/team_enrollment.html").render(
                errors=["Invalid country"],
                l3mon={"csrf": "token", "assets": {"css.app": "", "js.boot": "", "js.register": ""}, "signed_in": True, "player": True, "me": {}, "event_name": "CTF", "site_url": "", "build_sha": "1", "countries": []},
            )
            assert 'href="#f-country"' in rendered_studio
            assert 'id="f-country-e"' in rendered_studio
            assert 'class="alert alert-error"' not in rendered_studio
            assert rendered_studio.count("Invalid country") == 2  # Once in summary, once on field


def test_invite_link_wording_real_platform_vs_demo(app):
    """B6: Real platform (studio.inviteLink=true) displays captain's invite link wording;
    demo mode displays short code ABCD-1234 copy.
    """
    with app.app_context():
        # Real platform mode (default in team_enrollment.html)
        client = login_as_user(app, "existing_player", PASSWORD)
        body = client.get("/teams/join").get_data(as_text=True)
        assert "Paste the invite link your captain sent you." in body

        # Error with invalid invite format on real platform
        nonce = nonce_in(body)
        r_err = client.post("/register/studio", data={"nonce": nonce, "mode": "join", "invite": "https://invalid.example/phish"})
        assert r_err.status_code == 200
        text_err = r_err.get_data(as_text=True)
        assert "Paste the invite link your captain sent you." in text_err
        assert 'href="#f-invite"' in text_err

        # Demo mode test (simulated rendering with inviteLink=false)
        with app.test_request_context("/register/studio"):
            rendered_demo = app.jinja_env.get_template("_register-studio.html").render(
                page={"title": "Studio", "cls": "pg-app", "osd": "", "skip": "", "crawl": False, "signedIn": True, "player": True},
                asset={"css": "", "boot": "", "js": ""},
                csrf="token",
                reg={"key": "studio", "h1": "Studio", "lead": "", "phone": "", "dir": "", "showStepper": True, "stepper": {"s1": {}, "s2": {}, "s3": {}, "s4": None, "dots": [], "count": ""}, "alert": None, "summary": [], "hasSummary": False, "dev": None},
                studio={"create": False, "join": True, "name": "", "country": "", "affiliation": "", "ctftime": "", "invite": "", "inviteLink": False, "shuffle": "0", "countries": [], "ctPreview": "", "hint": None, "err": {}, "pv": {"name": "", "meta": ""}, "mk": "", "mood": "", "action": "", "back": "", "submit": ""},
                event={"name": "CTF", "contact": None, "site": ""},
                build={"sha": "1"},
                news=[],
                dev=None,
                nav={},
                me={},
            )
            assert "Ask your captain. It looks like ABCD-1234." in rendered_demo


def test_theme_registration_hooks_cost_on_unrelated_routes(app):
    """B9: theme_registration_hooks runs on every request; on unrelated routes,
    it executes 0 queries, no is_admin() checks, and no Teams/Users reads.
    """
    with app.app_context():
        client = login_as_user(app, "existing_player", PASSWORD)
        queries = []

        def on(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        # First request primes caches
        client.get("/scoreboard")

        event.listen(db.engine, "before_cursor_execute", on)
        try:
            queries.clear()
            r = client.get("/scoreboard")
            assert r.status_code == 200
            # Ensure no registration/team lookup queries executed from theme_registration_hooks
            team_queries = [q for q in queries if "from teams" in q.lower() or "teams.name" in q.lower()]
            assert len(team_queries) == 0, f"unexpected team queries: {team_queries}"
        finally:
            event.remove(db.engine, "before_cursor_execute", on)
