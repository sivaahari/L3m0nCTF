"""Tests for wrapped CTFd view functions (SP5 B3.3, B4.1-B4.4).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_views.py
"""
from tests.helpers import create_ctfd, destroy_ctfd, gen_team, login_as_user, register_user

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_guard.models import Evidence
from CTFd.plugins.l3mon_guard.policies.team_name import MSG_CHARS, MSG_TAKEN


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
            assert MSG_TAKEN in r.get_data(as_text=True)
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
            assert MSG_TAKEN in r.get_data(as_text=True)
    destroy_ctfd(app)


def test_what_is_stored_for_a_new_studio_is_the_name_that_was_checked():
    """CTFd stores what the form says; the guard writes the checked (cleaned) name back, so the two cannot differ."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        register_user(app, name="spaced_player", email="spaced@example.com")
        with login_as_user(app, name="spaced_player") as client:
            r = client.post("/teams/new", data=_team_data(client, "Team \t  Alpha  "))
            assert r.status_code == 302
            assert [t.name for t in Teams.query.all()] == ["Team Alpha"]
    destroy_ctfd(app)


def test_a_full_width_name_is_stored_in_its_composed_form():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        register_user(app, name="wide_player", email="wide@example.com")
        with login_as_user(app, name="wide_player") as client:
            r = client.post("/teams/new", data=_team_data(client, "\N{FULLWIDTH LATIN CAPITAL LETTER S}tudio One"))
            assert r.status_code == 302
            assert [t.name for t in Teams.query.all()] == ["Studio One"]
    destroy_ctfd(app)


def test_an_invisible_character_in_a_new_studio_name_is_refused_and_recorded_readably():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        register_user(app, name="sneaky_player", email="sneaky@example.com")
        with login_as_user(app, name="sneaky_player") as client:
            r = client.post("/teams/new", data=_team_data(client, "Zero\N{ZERO WIDTH SPACE}Width"))
            assert r.status_code == 200
            assert MSG_CHARS in r.get_data(as_text=True)
            assert Teams.query.count() == 0
        evidence = Evidence.query.filter_by(kind="name_policy_blocked").first()
        assert evidence is not None
        assert evidence.detail.isascii() and "u200b" in evidence.detail, "the crew reads the name's real code points, not the invisible character itself"
    destroy_ctfd(app)


def test_the_api_rename_stores_the_checked_name_and_refuses_an_invisible_character():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        team_id = gen_team(db, name="Beta Studio").id
        with login_as_user(app, name="admin") as client:
            ok = client.patch(f"/api/v1/teams/{team_id}", json={"name": "Gamma   Studio "})
            assert ok.status_code == 200
            assert Teams.query.get(team_id).name == "Gamma Studio"
            bad = client.patch(f"/api/v1/teams/{team_id}", json={"name": "Delta\N{RIGHT-TO-LEFT OVERRIDE}Studio"})
            assert bad.status_code == 400
            assert bad.get_json()["errors"]["name"] == [MSG_CHARS]
            assert Teams.query.get(team_id).name == "Gamma Studio"
    destroy_ctfd(app)


def _captain(app, name, studio):
    register_user(app, name=name, email=f"{name}@example.com")
    client = login_as_user(app, name=name)
    assert client.post("/teams/new", data=_team_data(client, studio)).status_code == 302
    return client


def test_a_captain_cannot_rename_the_studio_through_the_api_past_the_name_policy():
    """PATCH /api/v1/teams/me is CTFd's own way for a captain to rename the studio. The guard once wrapped two endpoint names that do not exist, so this route
    met no rule at all: "Admin Crew", a twin of another studio, a one-character name and an invisible character all went through."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        gen_team(db, name="Omega Studio")
        client = _captain(app, "capt_one", "Captain Studio")
        for bad, why in (
            ("Official Admin Crew", MSG_TAKEN),
            ("omega   STUDIO", MSG_TAKEN),
            ("Q", None),
            ("Zero\N{ZERO WIDTH SPACE}Width", MSG_CHARS),
            ("Oth\N{CYRILLIC SMALL LETTER IE}r Studio", None),
            ("X" * 33, None),
        ):
            r = client.patch("/api/v1/teams/me", json={"name": bad})
            assert r.status_code == 400, bad
            assert r.get_json()["errors"]["name"] and (why is None or r.get_json()["errors"]["name"] == [why]), bad
            assert Teams.query.filter_by(name=bad).first() is None
        assert Evidence.query.filter_by(kind="name_policy_blocked").count() == 6
        ok = client.patch("/api/v1/teams/me", json={"name": "Captain   Studio Two "})
        assert ok.status_code == 200
        assert ok.get_json()["data"]["name"] == "Captain Studio Two", "the stored name is the checked name"
        again = client.patch("/api/v1/teams/me", json={"name": "Captain Studio Two", "affiliation": "Somewhere"})
        assert again.status_code == 200, "a studio may keep its own name when it changes something else"
    destroy_ctfd(app)


def test_a_studio_made_before_a_rule_can_still_change_its_other_details():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        client = _captain(app, "capt_old", "Sensible Name")
        team = Teams.query.filter_by(name="Sensible Name").first()
        team.name = "Q"  # made when a one-character name was allowed
        db.session.commit()
        r = client.patch("/api/v1/teams/me", json={"name": "Q", "affiliation": "Amrita"})
        assert r.status_code == 200
        assert client.patch("/api/v1/teams/me", json={"name": "R"}).status_code == 400, "but a change of the name is judged"
    destroy_ctfd(app)
