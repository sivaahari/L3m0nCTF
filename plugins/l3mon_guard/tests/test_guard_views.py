"""Tests for wrapped CTFd view functions (SP5 B3.3, B4.1-B4.4).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_views.py
"""
from tests.helpers import create_ctfd, destroy_ctfd, gen_team, login_as_user, register_user

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_guard.models import Evidence


def _register_data(client, name, email, password="password123"):
    client.get("/register")
    with client.session_transaction() as sess:
        nonce = sess.get("nonce")
    return {
        "name": name,
        "email": email,
        "password": password,
        "nonce": nonce,
        "_submit": "Submit",
    }


def _team_data(client, name, password="teampassword"):
    client.get("/teams/new")
    with client.session_transaction() as sess:
        nonce = sess.get("nonce")
    return {
        "name": name,
        "password": password,
        "nonce": nonce,
    }


def test_register_blocks_disposable_domain():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as client:
            r = client.post(
                "/register",
                data=_register_data(client, "disposable_user", "user@mailinator.com"),
            )
            assert r.status_code == 200
            assert "This email address cannot be used here" in r.get_data(as_text=True)

            # Assert evidence was recorded
            evidence = Evidence.query.filter_by(kind="disposable_email_blocked").first()
            assert evidence is not None
            assert "mailinator.com" in evidence.detail
            assert "user@" not in evidence.detail and "user" not in evidence.detail.replace("disposable_email", ""), "only the domain is kept, never the address (integration audit)"

            # Assert user was not created
            assert Users.query.filter_by(name="disposable_user").first() is None
    destroy_ctfd(app)


def test_register_blocks_disguised_email():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        register_user(app, name="legit", email="legit@gmail.com")

        with app.test_client() as client:
            r = client.post(
                "/register",
                data=_register_data(client, "disguised_user", "l.e.g.i.t+testing@googlemail.com"),
            )
            assert r.status_code == 200
            assert "That email has already been used" in r.get_data(as_text=True)
            assert Users.query.filter_by(name="disguised_user").first() is None
    destroy_ctfd(app)


def test_register_blocks_bidi_zero_width_email():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as client:
            r = client.post(
                "/register",
                data=_register_data(client, "bidi_user", "evil\u200b@example.com"),
            )
            assert r.status_code == 200
            assert "invalid control characters" in r.get_data(as_text=True)
            assert Users.query.filter_by(name="bidi_user").first() is None
    destroy_ctfd(app)


def test_team_creation_blocks_impersonation_for_normal_users():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        register_user(app, name="regular_player", email="regular@example.com")
        with login_as_user(app, name="regular_player") as client:
            r = client.post(
                "/teams/new",
                data=_team_data(client, "Official Admin Crew"),
            )
            assert r.status_code == 200
            assert "That team name is reserved." in r.get_data(as_text=True)
            assert Teams.query.filter_by(name="Official Admin Crew").first() is None
    destroy_ctfd(app)


def test_team_creation_blocks_collision():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        gen_team(db, name="Omega Studio")
        register_user(app, name="another_player", email="another@example.com")

        with login_as_user(app, name="another_player") as client:
            r = client.post(
                "/teams/new",
                data=_team_data(client, "omega studio"),
            )
            assert r.status_code == 200
            assert "That team name is already taken." in r.get_data(as_text=True)
    destroy_ctfd(app)
