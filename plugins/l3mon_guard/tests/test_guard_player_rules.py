"""The username and e-mail rules where a player chooses or changes them: the sign-up form and CTFd's own API (`PATCH /api/v1/users/me`, the crew's
`PATCH /api/v1/users/<id>`). Before this, the rules lived in the browser only: a script could sign up as `root`, or rename itself to `admin` or to a name with an
invisible character, or move to a throw-away mail address, through the very calls the pages make.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_player_rules.py
"""
import pytest

from CTFd.models import Users, db
from CTFd.plugins.l3mon_guard.policies.username import MSG_SHAPE, MSG_TAKEN
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user

PASSWORD = "Lemon-Studio-Passphrase-2026"
DISPOSABLE = "This email address cannot be used here. Please use a permanent email address."
USED = "That email has already been used"


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True)
    app.config["L3MON_SIGNUP_RULES"] = True
    yield app
    destroy_ctfd(app)


def _signup(app, name, email, password=PASSWORD):
    """What a browser posts to /register (the rules box included, which stream B's page requires and CTFd ignores)."""
    with app.test_client() as client:
        client.get("/register")
        with client.session_transaction() as sess:
            nonce = sess.get("nonce")
        response = client.post("/register", data={"name": name, "email": email, "password": password, "privacy": "1", "nonce": nonce})
        body = response.get_data(as_text=True)
    return response.status_code, body


def _make_user(name, email, **extra):
    user = Users(name=name, email=email, password=PASSWORD, verified=True, **extra)
    db.session.add(user)
    db.session.commit()
    return user.id


def _wrapped_by_the_rules(view):
    while view is not None:
        if getattr(view, "l3mon_player_rules", False):
            return True
        view = getattr(view, "__wrapped__", None)
    return False


def test_the_rules_are_installed_on_the_endpoints_ctfd_really_has(app):
    """A wrapper on a name that does not exist does nothing, silently (it happened to the studio-name rule): name each real endpoint."""
    for endpoint in ("auth.register", "api.users_user_private", "api.users_user_public"):
        assert endpoint in app.view_functions, endpoint
        assert _wrapped_by_the_rules(app.view_functions[endpoint]), f"{endpoint} is not under the player rules"


@pytest.mark.parametrize(
    "name, message",
    [
        ("xy", MSG_SHAPE),
        ("a" * 25, MSG_SHAPE),
        ("a b c", MSG_SHAPE),
        ("lemon\U0001F34B", MSG_SHAPE),
        ("sivaa​", MSG_SHAPE),  # an invisible character
        ("sіvaa", MSG_SHAPE),  # a Cyrillic i that looks like the Latin one
        ("root", MSG_TAKEN),
        ("Administrator", MSG_TAKEN),
        ("L3m0n", MSG_TAKEN),
    ],
)
def test_sign_up_refuses_a_name_the_rule_refuses_and_makes_no_account(app, name, message):
    with app.app_context():
        status, body = _signup(app, name, "newplayer@example.com")
        assert status == 200, "the form is shown again, not a redirect"
        assert message in body
        assert Users.query.filter_by(email="newplayer@example.com").first() is None


def test_sign_up_stores_exactly_the_name_that_was_checked(app):
    with app.app_context():
        status, _ = _signup(app, "  sivaa.lemon  ", "sivaa@example.com")
        assert status == 302
        assert Users.query.filter_by(email="sivaa@example.com").one().name == "sivaa.lemon"


def test_sign_up_refuses_a_name_taken_ignoring_case(app):
    with app.app_context():
        _make_user("Sivaa", "first@example.com")
        status, body = _signup(app, "sivaa", "second@example.com")
        assert status == 200 and MSG_TAKEN in body


def test_the_rules_follow_the_switch_so_ctfds_own_helper_still_works(app):
    app.config["L3MON_SIGNUP_RULES"] = False
    with app.app_context():
        status, _ = _signup(app, "xy", "short@example.com", password="password")
        assert status == 302, "with the switch off the stock behaviour stands (CTFd's own tests register 'user' with the password 'password')"


@pytest.mark.parametrize("name", ["ab", "admin", "Root", "sivaa​", "a" * 100, "two words"])
def test_a_player_cannot_rename_themselves_to_a_name_the_rule_refuses(app, name):
    with app.app_context():
        _make_user("realname", "player@example.com")
        with login_as_user(app, name="realname", password=PASSWORD) as client:
            response = client.patch("/api/v1/users/me", json={"name": name})
            assert response.status_code == 400, response.get_json()
            data = response.get_json()
            assert data["success"] is False and data["errors"]["name"][0] in (MSG_SHAPE, MSG_TAKEN)
        assert Users.query.filter_by(email="player@example.com").one().name == "realname"


def test_a_good_rename_is_stored_exactly_as_checked(app):
    with app.app_context():
        _make_user("realname", "player@example.com")
        with login_as_user(app, name="realname", password=PASSWORD) as client:
            response = client.patch("/api/v1/users/me", json={"name": "  new.name_7  "})
            assert response.status_code == 200, response.get_json()
        assert Users.query.filter_by(email="player@example.com").one().name == "new.name_7"


def test_a_name_made_before_the_rule_may_stay_and_the_other_details_still_save(app):
    """An account made before the rule may hold a name it refuses; saving it back unchanged, or saving something else, must not fail."""
    with app.app_context():
        _make_user("ab", "old@example.com")
        with login_as_user(app, name="ab", password=PASSWORD) as client:
            assert client.patch("/api/v1/users/me", json={"affiliation": "Amrita"}).status_code == 200
            assert client.patch("/api/v1/users/me", json={"name": "ab"}).status_code == 200
        assert Users.query.filter_by(email="old@example.com").one().name == "ab"


def test_the_crew_may_give_any_account_any_name(app):
    with app.app_context():
        _make_user("crewmember", "crew@example.com", type="admin")
        target = _make_user("player", "player@example.com")
        with login_as_user(app, name="crewmember", password=PASSWORD) as client:
            assert client.patch(f"/api/v1/users/{target}", json={"name": "support"}).status_code == 200
        assert db.session.get(Users, target).name == "support"


def test_the_crew_may_give_any_account_any_address(app):
    with app.app_context():
        _make_user("crewmember", "crew@example.com", type="admin")
        target = _make_user("player", "player@example.com")
        with login_as_user(app, name="crewmember", password=PASSWORD) as client:
            assert client.patch(f"/api/v1/users/{target}", json={"email": "someone@mailinator.com"}).status_code == 200
        assert db.session.get(Users, target).email == "someone@mailinator.com"


def test_a_player_cannot_move_to_a_throw_away_address(app):
    with app.app_context():
        _make_user("realname", "player@example.com")
        with login_as_user(app, name="realname", password=PASSWORD) as client:
            response = client.patch("/api/v1/users/me", json={"email": "someone@mailinator.com", "confirm": PASSWORD})
            assert response.status_code == 400, response.get_json()
            assert DISPOSABLE in response.get_json()["errors"]["email"]
        assert Users.query.filter_by(name="realname").one().email == "player@example.com"


def test_a_player_cannot_take_an_address_in_another_form_of_one_in_use(app):
    """A dotted or plus-tagged form of an address that is already used is the same mailbox."""
    with app.app_context():
        _make_user("first", "some.one@gmail.com")
        _make_user("second", "second@example.com")
        with login_as_user(app, name="second", password=PASSWORD) as client:
            for disguised in ("someone@gmail.com", "someone+tag@gmail.com", "s.o.m.e.o.n.e@gmail.com"):
                response = client.patch("/api/v1/users/me", json={"email": disguised, "confirm": PASSWORD})
                assert response.status_code == 400, (disguised, response.get_json())
                assert USED in response.get_json()["errors"]["email"]
        assert Users.query.filter_by(name="second").one().email == "second@example.com"


def test_a_good_address_change_still_works_and_the_same_address_is_not_judged_again(app):
    with app.app_context():
        _make_user("realname", "player@example.com")
        with login_as_user(app, name="realname", password=PASSWORD) as client:
            assert client.patch("/api/v1/users/me", json={"email": "player@example.com", "confirm": PASSWORD}).status_code == 200
            assert client.patch("/api/v1/users/me", json={"email": "moved@example.org", "confirm": PASSWORD}).status_code == 200
        assert Users.query.filter_by(name="realname").one().email == "moved@example.org"


def test_with_the_switch_off_the_api_keeps_ctfds_own_behaviour(app):
    app.config["L3MON_SIGNUP_RULES"] = False
    with app.app_context():
        _make_user("realname", "player@example.com")
        with login_as_user(app, name="realname", password=PASSWORD) as client:
            assert client.patch("/api/v1/users/me", json={"name": "ab"}).status_code == 200
