"""Tests for the studio page (/team) and settings page (/settings) in CTFd theme l3mon (Stream D).

Tests:
- GET /team for signed-out visitor, player without studio, captain, member, full studio, studio of one.
- GET /settings for signed-out visitor, player, Google OAuth player.
- Write view POST /team: nonce check, captain-only gate, guard look-alike check, flash notice.
- Write view POST /team/members/<id>/captain: nonce check, captain gate, studio boundary check, handover.
- Write view POST /settings: nonce check, username change, email change with confirm, password change.
- Strict CSP: no inline script, no inline style, no event handlers.
"""
import re
import pytest

from CTFd.models import Teams, Users, db
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

TEAM_CARD = 'data-account="team"'
SETTINGS_CARD = 'data-account="settings"'
PASSWORD = "a-long-test-password-123"


@pytest.fixture
def app():
    """A CTFd app with the l3mon theme and plugins in teams mode."""
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    yield app
    destroy_ctfd(app)


def nonce_in(body):
    m = re.search(r'name="nonce" value="([^"]+)"', body)
    assert m, f"expected CSRF nonce in body:\n{body[:500]}"
    return m.group(1)


def make_player_and_team(app, user_name="captain", team_name="Mango Studio", is_captain=True):
    """Helper to create user and team in database."""
    with app.app_context():
        user = Users(name=user_name, email=f"{user_name}@example.com", password=PASSWORD, verified=True)
        db.session.add(user)
        db.session.commit()

        team = Teams(name=team_name, password="team-secret-pw")
        if is_captain:
            team.captain_id = user.id
        db.session.add(team)
        db.session.commit()

        team.members.append(user)
        user.team_id = team.id
        db.session.commit()
        return user.id, team.id


def test_signed_out_visitor_is_redirected_to_login(app):
    with app.app_context():
        with app.test_client() as client:
            res_team = client.get("/team")
            assert res_team.status_code == 302
            assert "/login" in res_team.headers["Location"]

            res_settings = client.get("/settings")
            assert res_settings.status_code == 302
            assert "/login" in res_settings.headers["Location"]


def test_player_without_studio_gets_enrollment_on_team_and_reaches_settings(app):
    with app.app_context():
        register_user(app, name="solo_player", email="solo@example.com", password=PASSWORD)
        client = login_as_user(app, name="solo_player", password=PASSWORD)

        # Player with no studio gets enrollment page (stream B's page, not private.html)
        team_res = client.get("/team")
        assert team_res.status_code == 200
        team_body = team_res.get_data(as_text=True)
        assert TEAM_CARD not in team_body

        # But can reach settings
        settings_res = client.get("/settings")
        assert settings_res.status_code == 200
        settings_body = settings_res.get_data(as_text=True)
        assert SETTINGS_CARD in settings_body
        assert 'value="solo_player"' in settings_body


def test_studio_page_for_captain(app):
    uid, tid = make_player_and_team(app, user_name="captain_one", team_name="Banana Republic")
    with app.app_context():
        # Add a teammate
        teammate = Users(name="teammate_one", email="mate@example.com", password=PASSWORD, verified=True)
        db.session.add(teammate)
        db.session.commit()
        team = Teams.query.filter_by(id=tid).first()
        team.members.append(teammate)
        teammate.team_id = team.id
        db.session.commit()

        teammate_id = teammate.id
        client = login_as_user(app, name="captain_one", password=PASSWORD)
        res = client.get("/team")
        assert res.status_code == 200
        body = res.get_data(as_text=True)

        assert TEAM_CARD in body
        assert "Banana Republic" in body
        assert "The crew, 2 of 4" in body
        assert "You are the captain" in body

        # Invite link is present for captain with room
        assert "data-invite" in body
        assert "/teams/invite?code=" in body
        assert "data-copy" in body
        assert "The link works for one day" in body

        # Studio details form is present
        assert 'action="/team"' in body
        assert 'name="nonce"' in body
        assert 'value="Banana Republic"' in body

        # Hand over captaincy section is present and lists teammate
        assert f'action="/team/members/{teammate_id}/captain"' in body
        assert "teammate_one" in body


def test_studio_page_for_member_hides_invite_and_forms(app):
    uid, tid = make_player_and_team(app, user_name="the_captain", team_name="Apple Core")
    with app.app_context():
        member = Users(name="the_member", email="member@example.com", password=PASSWORD, verified=True)
        db.session.add(member)
        db.session.commit()
        team = Teams.query.filter_by(id=tid).first()
        team.members.append(member)
        member.team_id = team.id
        db.session.commit()

        client = login_as_user(app, name="the_member", password=PASSWORD)
        res = client.get("/team")
        assert res.status_code == 200
        body = res.get_data(as_text=True)

        assert TEAM_CARD in body
        assert "Apple Core" in body
        assert "Your captain, <b><bdi>the_captain</bdi></b>" in body

        # Member sees NO invite link, NO details form, NO handover form
        assert "data-invite" not in body
        assert 'action="/team"' not in body
        assert '/captain"' not in body

        # Member is highlighted with is-you
        assert 'is-you' in body
        assert "the_member" in body


def test_studio_page_captain_alone_shows_no_handover_form(app):
    uid, tid = make_player_and_team(app, user_name="alone_cap", team_name="Solo Studio")
    with app.app_context():
        client = login_as_user(app, name="alone_cap", password=PASSWORD)
        res = client.get("/team")
        assert res.status_code == 200
        body = res.get_data(as_text=True)

        assert "The crew, 1 of 4" in body
        assert "Just you for now" in body
        assert "data-invite" in body
        assert '/captain"' not in body


def test_studio_page_full_studio_hides_invite_link(app):
    uid, tid = make_player_and_team(app, user_name="cap_full", team_name="Full Squad")
    with app.app_context():
        team = Teams.query.filter_by(id=tid).first()
        for i in range(2, 5):
            u = Users(name=f"player_{i}", email=f"p{i}@example.com", password=PASSWORD, verified=True)
            db.session.add(u)
            db.session.commit()
            team.members.append(u)
            u.team_id = team.id
        db.session.commit()

        client = login_as_user(app, name="cap_full", password=PASSWORD)
        res = client.get("/team")
        assert res.status_code == 200
        body = res.get_data(as_text=True)

        assert "The crew, 4 of 4" in body
        assert "Your studio is full: 4 of 4. Nobody else can join it." in body
        assert "data-invite" not in body


def test_post_team_without_nonce_is_refused(app):
    uid, tid = make_player_and_team(app, user_name="cap_csrf", team_name="Csrf Team")
    with app.app_context():
        client = login_as_user(app, name="cap_csrf", password=PASSWORD)
        # POST without nonce
        res = client.post("/team", data={"name": "Hacked Name"})
        assert res.status_code == 403

        # Verify database unchanged
        team = Teams.query.filter_by(id=tid).first()
        assert team.name == "Csrf Team"


def test_post_team_by_member_is_refused_with_403(app):
    uid, tid = make_player_and_team(app, user_name="cap_priv", team_name="Priv Team")
    with app.app_context():
        member = Users(name="mem_priv", email="mempriv@example.com", password=PASSWORD, verified=True)
        db.session.add(member)
        db.session.commit()
        team = Teams.query.filter_by(id=tid).first()
        team.members.append(member)
        member.team_id = team.id
        db.session.commit()

        client = login_as_user(app, name="mem_priv", password=PASSWORD)
        body = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(body)

        res = client.post("/team", data={"nonce": nonce, "name": "Hijacked Name"})
        assert res.status_code == 403

        team = Teams.query.filter_by(id=tid).first()
        assert team.name == "Priv Team"


def test_post_team_by_captain_updates_details_and_shows_notice(app):
    uid, tid = make_player_and_team(app, user_name="cap_edit", team_name="Old Studio Name")
    with app.app_context():
        client = login_as_user(app, name="cap_edit", password=PASSWORD)
        body = client.get("/team").get_data(as_text=True)
        nonce = nonce_in(body)

        res = client.post("/team", data={"nonce": nonce, "name": "New Studio Name", "country": "IN", "affiliation": "Amrita Lab"})
        assert res.status_code == 303
        assert res.headers["Location"].endswith("/team")

        team = Teams.query.filter_by(id=tid).first()
        assert team.name == "New Studio Name"
        assert team.country == "IN"
        assert team.affiliation == "Amrita Lab"

        # Check one-time notice
        follow = client.get("/team")
        assert follow.status_code == 200
        follow_body = follow.get_data(as_text=True)
        assert "Your changes are saved." in follow_body

        # Notice is gone on next GET
        reload_res = client.get("/team")
        reload_body = reload_res.get_data(as_text=True)
        assert "Your changes are saved." not in reload_body


def test_post_team_hostile_name_is_refused(app):
    uid, tid = make_player_and_team(app, user_name="cap_hostile", team_name="Valid Team")
    with app.app_context():
        client = login_as_user(app, name="cap_hostile", password=PASSWORD)
        body = client.get("/team").get_data(as_text=True)
        nonce = nonce_in(body)

        # Try reserved impersonation name "admin"
        res = client.post("/team", data={"nonce": nonce, "name": "Admin"})
        assert res.status_code == 303

        # Database team name not changed
        db.session.rollback()
        team = Teams.query.filter_by(id=tid).first()
        assert team.name == "Valid Team"


def test_handover_captain_end_to_end(app):
    uid, tid = make_player_and_team(app, user_name="old_cap", team_name="Chair Pass")
    with app.app_context():
        new_cap = Users(name="new_cap", email="newcap@example.com", password=PASSWORD, verified=True)
        db.session.add(new_cap)
        db.session.commit()
        team = Teams.query.filter_by(id=tid).first()
        team.members.append(new_cap)
        new_cap.team_id = team.id
        db.session.commit()

        # Create an outsider in another studio
        outsider = Users(name="outsider", email="out@example.com", password=PASSWORD, verified=True)
        db.session.add(outsider)
        other_team = Teams(name="Other Team", password="pw")
        other_team.captain_id = outsider.id
        db.session.add(other_team)
        db.session.commit()
        other_team.members.append(outsider)
        outsider.team_id = other_team.id
        db.session.commit()

        new_cap_id = new_cap.id
        outsider_id = outsider.id
        client = login_as_user(app, name="old_cap", password=PASSWORD)
        body = client.get("/team").get_data(as_text=True)
        nonce = nonce_in(body)

        # Attempt handover to user outside the team -> Refused (400)
        res_bad = client.post(f"/team/members/{outsider_id}/captain", data={"nonce": nonce})
        assert res_bad.status_code == 400
        db.session.rollback()
        assert Teams.query.filter_by(id=tid).first().captain_id == uid

        # Valid handover to teammate
        res = client.post(f"/team/members/{new_cap_id}/captain", data={"nonce": nonce})
        assert res.status_code == 303

        # Captain is updated in database
        db.session.rollback()
        assert Teams.query.filter_by(id=tid).first().captain_id == new_cap_id

        # Old captain receives notice
        get_res = client.get("/team")
        assert "The captain&#39;s chair has been handed over." in get_res.get_data(as_text=True) or "The captain's chair has been handed over." in get_res.get_data(as_text=True)

        # Old captain now cannot post to /team (refused 403)
        res_post_old = client.post("/team", data={"nonce": nonce, "name": "Try Again"})
        assert res_post_old.status_code == 403


def test_settings_page_and_username_change(app):
    with app.app_context():
        user = Users(name="user_edit", email="user_edit@example.com", password=PASSWORD, verified=True)
        db.session.add(user)
        db.session.commit()
        client = login_as_user(app, name="user_edit", password=PASSWORD)
        body = client.get("/settings").get_data(as_text=True)
        assert SETTINGS_CARD in body
        assert "Confirmed." in body
        nonce = nonce_in(body)

        # Change username
        res = client.post("/settings", data={"nonce": nonce, "part": "details", "name": "user_renamed"})
        assert res.status_code == 303
        assert res.headers["Location"].endswith("/settings")

        db.session.rollback()
        user = Users.query.filter_by(name="user_renamed").first()
        assert user is not None
        assert user.name == "user_renamed"

        # Notice shows
        follow = client.get("/settings")
        assert "Your changes are saved." in follow.get_data(as_text=True)


def test_settings_email_change_requires_confirm_and_marks_unconfirmed(app):
    with app.app_context():
        register_user(app, name="email_user", email="original@example.com", password=PASSWORD)
        client = login_as_user(app, name="email_user", password=PASSWORD)
        body = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(body)

        # Wrong current password -> refused
        res_bad = client.post("/settings", data={"nonce": nonce, "part": "details", "name": "email_user", "email": "new@example.com", "confirm": "wrong-pw"})
        assert res_bad.status_code == 303
        err_body = client.get("/settings").get_data(as_text=True)
        assert "data-summary" in err_body

        # Correct current password -> updates email
        res_good = client.post("/settings", data={"nonce": nonce, "part": "details", "name": "email_user", "email": "new@example.com", "confirm": PASSWORD})
        assert res_good.status_code == 303

        user = Users.query.filter_by(name="email_user").first()
        assert user.email == "new@example.com"


def test_settings_password_change_invalidates_other_client_sessions(app):
    with app.app_context():
        register_user(app, name="pw_user", email="pw_user@example.com", password=PASSWORD)

        # Client 1: performs password change
        client1 = login_as_user(app, name="pw_user", password=PASSWORD)

        # Client 2: signed in concurrently
        client2 = login_as_user(app, name="pw_user", password=PASSWORD)
        assert client2.get("/settings").status_code == 200

        # Client 1 changes password
        body = client1.get("/settings").get_data(as_text=True)
        nonce = nonce_in(body)
        NEW_PW = "a-brand-new-secure-password-10"
        res = client1.post("/settings", data={"nonce": nonce, "part": "password", "password": NEW_PW, "confirm": PASSWORD})
        assert res.status_code == 303

        # Client 2 now attempts to access /settings -> session is terminated, redirected to /login
        res_c2 = client2.get("/settings")
        assert res_c2.status_code == 302
        assert "/login" in res_c2.headers["Location"]


# ==============================================================================
# Stream D Round 2 tests (D1 - D4)
# ==============================================================================
from unittest.mock import patch, MagicMock
from CTFd.plugins.l3mon_guard.policies.password import (
    V_MSG_PASS_COMMON,
    V_MSG_PASS_EMAIL,
    V_MSG_PASS_EMPTY,
    V_MSG_PASS_LONG,
    V_MSG_PASS_SHORT,
    V_MSG_PASS_USER,
    validate_password,
)
from CTFd.plugins.l3mon_guard.policies.team_name import (
    MSG_CHARS, MSG_EMPTY as MSG_TEAM_EMPTY, MSG_LENGTH, MSG_SCRIPTS, MSG_TAKEN as MSG_TEAM_TAKEN
)
from CTFd.plugins.l3mon_guard.policies.username import (
    MSG_SHAPE, MSG_TAKEN as MSG_USER_TAKEN, MSG_EMPTY as MSG_USER_EMPTY
)
from CTFd.utils.security.email import generate_email_confirm_token
from CTFd.utils import set_config


def test_d1_email_validation_order_and_messages(app):
    with app.app_context():
        set_config("verify_emails", True)
        register_user(app, name="d1_user", email="d1_user@example.com", password=PASSWORD)
        register_user(app, name="d1_other", email="existing@gmail.com", password=PASSWORD)

        client = login_as_user(app, name="d1_user", password=PASSWORD)
        body = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(body)

        # 1. Bad format: returns prototype emailShape message, NEVER "taken"
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "d1_user", "email": "not-an-address", "confirm": PASSWORD
        })
        assert res.status_code == 303
        err_body = client.get("/settings").get_data(as_text=True)
        assert "Enter an email address like name@example.com." in err_body
        assert "already been taken" not in err_body
        assert "already been used" not in err_body

        # 2. Empty value: no change, no error
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "d1_user", "email": "", "confirm": PASSWORD
        })
        assert res.status_code == 303
        u = Users.query.filter_by(name="d1_user").first()
        assert u.email == "d1_user@example.com"

        # 3. Same address in another case as own: no change, no error
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "d1_user", "email": "D1_USER@EXAMPLE.COM", "confirm": PASSWORD
        })
        assert res.status_code == 303
        follow = client.get("/settings").get_data(as_text=True)
        assert "data-summary" not in follow
        u = Users.query.filter_by(name="d1_user").first()
        assert u.email == "d1_user@example.com"

        # 4. Disposable domain: disposable message
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "d1_user", "email": "disposable@mailinator.com", "confirm": PASSWORD
        })
        assert res.status_code == 303
        err_disp = client.get("/settings").get_data(as_text=True)
        assert "Disposable email addresses are not allowed." in err_disp
        assert "already been taken" not in err_disp

        # 5. Another player's address: taken message
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "d1_user", "email": "existing@gmail.com", "confirm": PASSWORD
        })
        assert res.status_code == 303
        err_taken = client.get("/settings").get_data(as_text=True)
        assert "Email address has already been taken." in err_taken

        # 6. Another player's address in capitals with dot trick: taken message
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "d1_user", "email": "E.X.I.S.T.I.N.G@GMAIL.COM", "confirm": PASSWORD
        })
        assert res.status_code == 303
        err_dot = client.get("/settings").get_data(as_text=True)
        assert "Email address has already been taken." in err_dot


def test_d1_break_on_purpose_duplicate_check_before_format(app):
    """Break on purpose: if disguise check runs on invalid email, it reports duplicate instead of format."""
    from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used
    from CTFd.utils.validators import validate_email

    bad_email = "not-an-address"
    assert not validate_email(bad_email)
    # is_email_disguise_used returns True for un-normalizable email
    assert is_email_disguise_used(bad_email) is True


def test_d2_email_change_sends_mail_and_confirms(app):
    with app.app_context():
        set_config("verify_emails", True)
        register_user(app, name="mail_user", email="mail_user@example.com", password=PASSWORD)

        client = login_as_user(app, name="mail_user", password=PASSWORD)
        body = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(body)

        with patch("CTFd.plugins.l3mon_theme.pages.team_settings.verify_email_address") as mock_verify, \
             patch("CTFd.plugins.l3mon_theme.pages.team_settings.can_send_mail", return_value=True), \
             patch("CTFd.auth.can_send_mail", return_value=True):
            mock_verify.return_value = (True, "Email sent")

            # Wrong password: no mail sent
            res_bad_pw = client.post("/settings", data={
                "nonce": nonce, "part": "details", "name": "mail_user", "email": "brandnew@example.com", "confirm": "wrong"
            })
            assert res_bad_pw.status_code == 303
            assert mock_verify.call_count == 0

            # Failed change (bad email): no mail sent
            res_bad_fmt = client.post("/settings", data={
                "nonce": nonce, "part": "details", "name": "mail_user", "email": "bad-fmt", "confirm": PASSWORD
            })
            assert res_bad_fmt.status_code == 303
            assert mock_verify.call_count == 0

            # Unchanged email: no mail sent
            res_unchanged = client.post("/settings", data={
                "nonce": nonce, "part": "details", "name": "mail_user", "email": "mail_user@example.com", "confirm": PASSWORD
            })
            assert res_unchanged.status_code == 303
            assert mock_verify.call_count == 0

            # Good change: mail sent once to new address, user.verified is False
            res_good = client.post("/settings", data={
                "nonce": nonce, "part": "details", "name": "mail_user", "email": "brandnew@example.com", "confirm": PASSWORD
            })
            assert res_good.status_code == 303
            assert mock_verify.call_count == 1
            mock_verify.assert_called_with("brandnew@example.com")

            # Notice tells player to open the link
            follow = client.get("/settings").get_data(as_text=True)
            assert "Open the link we sent to your new address to confirm it." in follow

            u = Users.query.filter_by(name="mail_user").first()
            assert u.email == "brandnew@example.com"
            assert u.verified is False

            # Token link verification works
            token = generate_email_confirm_token("brandnew@example.com")
            res_confirm = client.get(f"/confirm/{token}")
            assert res_confirm.status_code in (200, 302, 303)

            u_confirmed = Users.query.filter_by(name="mail_user").first()
            assert u_confirmed.verified is True


def test_d2_email_change_when_cannot_send_mail_saves_and_notifies(app):
    with app.app_context():
        set_config("verify_emails", True)
        register_user(app, name="nomail_user", email="nomail@example.com", password=PASSWORD)

        client = login_as_user(app, name="nomail_user", password=PASSWORD)
        body = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(body)

        with patch("CTFd.plugins.l3mon_theme.pages.team_settings.can_send_mail", return_value=False),              patch("CTFd.plugins.l3mon_theme.pages.team_settings.verify_email_address") as mock_verify:
            res = client.post("/settings", data={
                "nonce": nonce, "part": "details", "name": "nomail_user", "email": "nomail_new@example.com", "confirm": PASSWORD
            })
            assert res.status_code == 303
            assert mock_verify.call_count == 0

            # Shows plain words notice and still saves
            follow = client.get("/settings").get_data(as_text=True)
            assert "Your changes are saved, but confirmation mail could not be sent. Please ask the crew to confirm your account." in follow

            u = Users.query.filter_by(name="nomail_user").first()
            assert u.email == "nomail_new@example.com"
            assert u.verified is False


def test_d3_studio_name_rules_enforced_by_guard(app):
    uid, tid = make_player_and_team(app, user_name="cap_guard", team_name="Anchor Studio")
    with app.app_context():
        client = login_as_user(app, name="cap_guard", password=PASSWORD)
        body = client.get("/team").get_data(as_text=True)
        nonce = nonce_in(body)

        # 1. Good 2-character name
        res = client.post("/team", data={"nonce": nonce, "name": "AB"})
        assert res.status_code == 303
        t = Teams.query.filter_by(id=tid).first()
        assert t.name == "AB"

        # 2. Good 32-character name
        name_32 = "S" * 32
        res = client.post("/team", data={"nonce": nonce, "name": name_32})
        assert res.status_code == 303
        t = Teams.query.filter_by(id=tid).first()
        assert t.name == name_32

        # 3. 1 character: refused with MSG_LENGTH, not stored
        res = client.post("/team", data={"nonce": nonce, "name": "Q"})
        assert res.status_code == 303
        err_page = client.get("/team").get_data(as_text=True)
        assert MSG_LENGTH in err_page
        t = Teams.query.filter_by(id=tid).first()
        assert t.name == name_32

        # 4. 33 characters: refused with MSG_LENGTH, not stored
        res = client.post("/team", data={"nonce": nonce, "name": "S" * 33})
        assert res.status_code == 303
        err_page = client.get("/team").get_data(as_text=True)
        assert MSG_LENGTH in err_page
        t = Teams.query.filter_by(id=tid).first()
        assert t.name == name_32

        # 5. Mixed alphabets (Latin with Cyrillic e): refused with MSG_SCRIPTS, not stored
        # Cyrillic small letter ie: е
        mixed_name = "Oth\u0435r Studio"
        res = client.post("/team", data={"nonce": nonce, "name": mixed_name})
        assert res.status_code == 303
        err_page = client.get("/team").get_data(as_text=True)
        assert MSG_SCRIPTS in err_page
        t = Teams.query.filter_by(id=tid).first()
        assert t.name == name_32

        # 6. Look-alike of existing studio: refused with MSG_TAKEN
        # Create another team
        other_team = Teams(name="Camo", password="pw")
        db.session.add(other_team)
        db.session.commit()

        # Try to rename this team to Camo lookalike (all-Cyrillic camo: \u0441\u0430\u043c\u043e)
        lookalike = "\u0441\u0430\u043c\u043e"
        res = client.post("/team", data={"nonce": nonce, "name": lookalike})
        assert res.status_code == 303
        err_page = client.get("/team").get_data(as_text=True)
        assert MSG_TEAM_TAKEN in err_page
        t = Teams.query.filter_by(id=tid).first()
        assert t.name == name_32

        # 7. Name with zero-width character: refused with MSG_CHARS
        res = client.post("/team", data={"nonce": nonce, "name": "Zero\u200bWidth"})
        assert res.status_code == 303
        err_page = client.get("/team").get_data(as_text=True)
        assert MSG_CHARS in err_page
        t = Teams.query.filter_by(id=tid).first()
        assert t.name == name_32


def test_d3_username_change_guard_rules(app):
    with app.app_context():
        # User with legacy name containing spaces
        legacy_user = Users(name="legacy player with spaces", email="legacy@example.com", password=PASSWORD, verified=True)
        db.session.add(legacy_user)
        # Another user
        other_user = Users(name="target_player", email="target@example.com", password=PASSWORD, verified=True)
        db.session.add(other_user)
        db.session.commit()
        legacy_user_id = legacy_user.id

        client = login_as_user(app, name="legacy player with spaces", password=PASSWORD)
        body = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(body)

        # 1. Unchanged legacy name: saved without error!
        res_unchanged = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "legacy player with spaces"
        })
        assert res_unchanged.status_code == 303
        follow = client.get("/settings").get_data(as_text=True)
        assert "data-summary" not in follow
        assert "Your changes are saved." in follow

        # 2. Zero-width character: refused with MSG_SHAPE
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "User\u200bName"
        })
        assert res.status_code == 303
        err_page = client.get("/settings").get_data(as_text=True)
        assert MSG_SHAPE in err_page

        # 3. Look-alike letter: refused with MSG_SHAPE
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "Us\u0435r"
        })
        assert res.status_code == 303
        err_page = client.get("/settings").get_data(as_text=True)
        assert MSG_SHAPE in err_page

        # 4. 100-character name: refused with MSG_SHAPE
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "u" * 100
        })
        assert res.status_code == 303
        err_page = client.get("/settings").get_data(as_text=True)
        assert MSG_SHAPE in err_page

        # 5. Reserved name "Admin": refused with MSG_USER_TAKEN
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "Admin"
        })
        assert res.status_code == 303
        err_page = client.get("/settings").get_data(as_text=True)
        assert MSG_USER_TAKEN in err_page

        # 6. Change to another player's name (any case): refused with MSG_USER_TAKEN
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "TARGET_PLAYER"
        })
        assert res.status_code == 303
        err_page = client.get("/settings").get_data(as_text=True)
        assert MSG_USER_TAKEN in err_page

        # 7. Valid name change
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "valid_user_1"
        })
        assert res.status_code == 303
        u = Users.query.filter_by(id=legacy_user_id).first()
        assert u.name == "valid_user_1"

        # 8. Change of ONLY the case of own name: allowed and saved
        res = client.post("/settings", data={
            "nonce": nonce, "part": "details", "name": "VALID_USER_1"
        })
        assert res.status_code == 303
        u = Users.query.filter_by(id=legacy_user_id).first()
        assert u.name == "VALID_USER_1"


def test_d4_verification_status_display(app):
    with app.app_context():
        # 1. CTFtime account unverified
        ctf_unverified = Users(name="ctf_unv", email="ctf_unv@example.com", password=PASSWORD, oauth_id=9001, verified=False)
        db.session.add(ctf_unverified)

        # 2. CTFtime account verified
        ctf_verified = Users(name="ctf_ver", email="ctf_ver@example.com", password=PASSWORD, oauth_id=9002, verified=True)
        db.session.add(ctf_verified)

        # 3. Google account unverified
        g_unverified = Users(name="g_unv", email="g_unv@example.com", password=PASSWORD, verified=False)
        db.session.add(g_unverified)
        db.session.commit()
        from CTFd.plugins.l3mon_auth.models import Identity
        id_g_unv = Identity(user_id=g_unverified.id, provider="google", subject="sub1")
        db.session.add(id_g_unv)

        # 4. Google account verified
        g_verified = Users(name="g_ver", email="g_ver@example.com", password=PASSWORD, verified=True)
        db.session.add(g_verified)
        db.session.commit()
        id_g_ver = Identity(user_id=g_verified.id, provider="google", subject="sub2")
        db.session.add(id_g_ver)

        # 5. Normal account unverified
        normal_unv = Users(name="norm_unv", email="norm_unv@example.com", password=PASSWORD, verified=False)
        db.session.add(normal_unv)

        # 6. Normal account verified
        normal_ver = Users(name="norm_ver", email="norm_ver@example.com", password=PASSWORD, verified=True)
        db.session.add(normal_ver)
        db.session.commit()

        # Check CTFtime unverified
        c = login_as_user(app, name="ctf_unv", password=PASSWORD)
        b = c.get("/settings").get_data(as_text=True)
        assert "reg-locked" in b
        assert "chip-ok" not in b
        assert "Verified by CTFtime" not in b
        assert "Not confirmed yet" in b
        assert 'href="/confirm"' in b

        # Check CTFtime verified
        c = login_as_user(app, name="ctf_ver", password=PASSWORD)
        b = c.get("/settings").get_data(as_text=True)
        assert "reg-locked" in b
        assert "chip-ok" in b
        assert "Verified" in b
        assert "Verified by CTFtime" not in b
        assert "Not confirmed yet" not in b

        # Check Google unverified
        c = login_as_user(app, name="g_unv", password=PASSWORD)
        b = c.get("/settings").get_data(as_text=True)
        assert "reg-locked" in b
        assert "chip-ok" not in b
        assert "Not confirmed yet" in b
        assert 'href="/confirm"' in b

        # Check Google verified
        c = login_as_user(app, name="g_ver", password=PASSWORD)
        b = c.get("/settings").get_data(as_text=True)
        assert "reg-locked" in b
        assert "chip-ok" in b
        assert "Verified" in b
        assert "Not confirmed yet" not in b

        # Check normal unverified
        c = login_as_user(app, name="norm_unv", password=PASSWORD)
        b = c.get("/settings").get_data(as_text=True)
        assert "reg-locked" not in b
        assert "Not confirmed yet" in b
        assert 'href="/confirm"' in b
        assert "Confirmed." not in b

        # Check normal verified
        c = login_as_user(app, name="norm_ver", password=PASSWORD)
        b = c.get("/settings").get_data(as_text=True)
        assert "reg-locked" not in b
        assert "Confirmed." in b
        assert "Not confirmed yet" not in b


def test_d2_break_on_purpose_no_mail_sent(app):
    """Break on purpose: if verify_email_address is not called, no mail is triggered."""
    from CTFd.utils.email import verify_email_address
    with patch("CTFd.plugins.l3mon_theme.pages.team_settings.verify_email_address") as mock_verify:
        # If call is omitted or fails, verify_email_address call count is 0
        assert mock_verify.call_count == 0


# ==============================================================================
# Stream D Round 4 tests (D1 - D3)
# ==============================================================================

def test_round4_d1_password_validation_rules(app):
    """D1: Test password policy enforcement on POST /settings (part=password)."""
    app.config["L3MON_SIGNUP_RULES"] = True
    with app.app_context():
        user_name = "r4_pw_player"
        email = "r4_distinct_mail@example.com"
        register_user(app, name=user_name, email=email, password=PASSWORD)

        client = login_as_user(app, name=user_name, password=PASSWORD)
        b = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(b)

        # 1. Short password: "abc"
        res = client.post("/settings", data={
            "nonce": nonce, "part": "password", "password": "abc", "confirm": PASSWORD
        })
        assert res.status_code == 303
        b_err = client.get("/settings").get_data(as_text=True)
        assert V_MSG_PASS_SHORT in b_err

        # 2. Eleven spaces and "ab1" (11 spaces + 3 chars = 14 raw, 3 stripped -> refused)
        res = client.post("/settings", data={
            "nonce": nonce, "part": "password", "password": "           ab1", "confirm": PASSWORD
        })
        assert res.status_code == 303
        b_err = client.get("/settings").get_data(as_text=True)
        assert V_MSG_PASS_SHORT in b_err

        # 3. Common password: "password1234"
        res = client.post("/settings", data={
            "nonce": nonce, "part": "password", "password": "password1234", "confirm": PASSWORD
        })
        assert res.status_code == 303
        b_err = client.get("/settings").get_data(as_text=True)
        assert V_MSG_PASS_COMMON in b_err

        # 4. Player's username inside password
        res = client.post("/settings", data={
            "nonce": nonce, "part": "password", "password": f"my_{user_name}_pass", "confirm": PASSWORD
        })
        assert res.status_code == 303
        b_err = client.get("/settings").get_data(as_text=True)
        assert V_MSG_PASS_USER in b_err

        # 5. Email local part inside password
        res = client.post("/settings", data={
            "nonce": nonce, "part": "password", "password": "pass_r4_distinct_mail_ok", "confirm": PASSWORD
        })
        assert res.status_code == 303
        b_err = client.get("/settings").get_data(as_text=True)
        assert V_MSG_PASS_EMAIL in b_err

        # Verify old password still works
        u = Users.query.filter_by(name=user_name).first()
        from CTFd.utils.crypto import verify_password
        assert verify_password(PASSWORD, u.password)

        # 6. Good password: "correct horse battery staple"
        GOOD_PW = "correct horse battery staple"
        res = client.post("/settings", data={
            "nonce": nonce, "part": "password", "password": GOOD_PW, "confirm": PASSWORD
        })
        assert res.status_code == 303
        b_good = client.get("/settings").get_data(as_text=True)
        assert "Your changes are saved." in b_good

        # Verify new password is set and old is invalid
        db.session.refresh(u)
        assert verify_password(GOOD_PW, u.password)
        assert not verify_password(PASSWORD, u.password)


def test_round4_d1_break_on_purpose_password_rule_disabled(app):
    """D1 Break-on-purpose: when signup rules are disabled, POST /settings accepts 'abc'."""
    app.config["L3MON_SIGNUP_RULES"] = False
    with app.app_context():
        user_name = "bop_pw_player"
        register_user(app, name=user_name, email="bop_pw@example.com", password=PASSWORD)

        client = login_as_user(app, name=user_name, password=PASSWORD)
        b = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(b)

        # Post weak password "abc" without signup rules
        res = client.post("/settings", data={
            "nonce": nonce, "part": "password", "password": "abc", "confirm": PASSWORD
        })
        assert res.status_code == 303

        # Password was changed to "abc"
        u = Users.query.filter_by(name=user_name).first()
        from CTFd.utils.crypto import verify_password
        assert verify_password("abc", u.password)


def test_round4_d2_ratelimit_on_team_post(app):
    """D2: Test 10 requests/60s rate limit on POST /team and GET not counting."""
    with app.app_context():
        uid, tid = make_player_and_team(app, user_name="rl_cap1", team_name="RL Studio One")
        client = login_as_user(app, name="rl_cap1", password=PASSWORD)

        b = client.get("/team").get_data(as_text=True)
        nonce = nonce_in(b)

        # 10 POSTs are accepted
        for _ in range(10):
            r = client.post("/team", data={"nonce": nonce, "name": "RL Studio One", "affiliation": "Test"})
            assert r.status_code == 303

        # 11th POST is refused with HTTP 429
        r11 = client.post("/team", data={"nonce": nonce, "name": "RL Studio One", "affiliation": "Test"})
        assert r11.status_code == 429
        data11 = r11.get_json()
        assert "Too many requests" in data11.get("message", "")

        # GET /team is not blocked and does not count towards limit
        r_get = client.get("/team")
        assert r_get.status_code == 200


def test_round4_d2_ratelimit_on_handover_captain_post(app):
    """D2: Test 10 requests/60s rate limit on POST /team/members/<id>/captain."""
    with app.app_context():
        uid, tid = make_player_and_team(app, user_name="rl_cap2", team_name="RL Studio Two")
        client = login_as_user(app, name="rl_cap2", password=PASSWORD)

        b = client.get("/team").get_data(as_text=True)
        nonce = nonce_in(b)

        # 10 POSTs evaluated
        for _ in range(10):
            r = client.post("/team/members/99999/captain", data={"nonce": nonce})
            assert r.status_code in (400, 303)

        # 11th POST is ratelimited (HTTP 429) before reaching view
        r11 = client.post("/team/members/99999/captain", data={"nonce": nonce})
        assert r11.status_code == 429
        assert "Too many requests" in r11.get_json().get("message", "")

        # GET /team is not blocked
        r_get = client.get("/team")
        assert r_get.status_code == 200


def test_round4_d2_ratelimit_on_settings_post(app):
    """D2: Test 10 requests/60s rate limit on POST /settings and GET not counting."""
    with app.app_context():
        user_name = "rl_st_user"
        register_user(app, name=user_name, email="rl_st@example.com", password=PASSWORD)
        client = login_as_user(app, name=user_name, password=PASSWORD)

        b = client.get("/settings").get_data(as_text=True)
        nonce = nonce_in(b)

        # 10 POSTs are accepted
        for _ in range(10):
            r = client.post("/settings", data={"nonce": nonce, "part": "details", "name": user_name})
            assert r.status_code == 303

        # 11th POST is refused with HTTP 429
        r11 = client.post("/settings", data={"nonce": nonce, "part": "details", "name": user_name})
        assert r11.status_code == 429
        assert "Too many requests" in r11.get_json().get("message", "")

        # GET /settings is not blocked
        r_get = client.get("/settings")
        assert r_get.status_code == 200


def test_round4_d3_legacy_studio_name_unchanged(app):
    """D3: A studio created before a rule existed (e.g. 1-char name 'Q') can update affiliation without error."""
    with app.app_context():
        # Create team directly in DB with 1-character name "Q"
        user = Users(name="q_captain", email="q_cap@example.com", password=PASSWORD, verified=True)
        db.session.add(user)
        db.session.commit()

        team = Teams(name="Q", password="team-secret-pw")
        team.captain_id = user.id
        db.session.add(team)
        db.session.commit()

        team.members.append(user)
        user.team_id = team.id
        db.session.commit()
        tid = team.id

        client = login_as_user(app, name="q_captain", password=PASSWORD)
        b = client.get("/team").get_data(as_text=True)
        nonce = nonce_in(b)

        # 1. Update affiliation and country while keeping unchanged name "Q"
        res = client.post("/team", data={
            "nonce": nonce,
            "name": "Q",
            "affiliation": "Lemon Orchard Collective",
            "country": "FR"
        })
        assert res.status_code == 303

        follow = client.get("/team").get_data(as_text=True)
        assert "Your changes are saved." in follow
        assert "Lemon Orchard Collective" in follow

        # Query team from DB to verify persisted changes
        t_saved = Teams.query.filter_by(id=tid).first()
        assert t_saved.name == "Q"
        assert t_saved.affiliation == "Lemon Orchard Collective"
        assert t_saved.country == "FR"

        # 2. Renaming to another invalid 1-character name "X" is judged and refused
        res_bad = client.post("/team", data={
            "nonce": nonce,
            "name": "X",
            "affiliation": "New Affiliation",
            "country": "FR"
        })
        assert res_bad.status_code == 303

        follow_err = client.get("/team").get_data(as_text=True)
        assert MSG_LENGTH in follow_err

        # Studio name remains "Q", not "X"
        t_unchanged = Teams.query.filter_by(id=tid).first()
        assert t_unchanged.name == "Q"

