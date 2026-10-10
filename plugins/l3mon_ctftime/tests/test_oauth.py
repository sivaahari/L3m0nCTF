"""Login with CTFtime, inside a real CTFd app, against a stand-in for oauth.ctftime.org that the plugin reaches with its real HTTP code.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_ctftime
"""
import logging
import time
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from sqlalchemy.exc import IntegrityError

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_ctftime import oauth
from CTFd.utils import set_config
from ctftime_mock import MockCTFtime, profile
from tests.helpers import create_ctfd, destroy_ctfd, gen_team, gen_user, login_as_user, register_user

START, CALLBACK = "/auth/ctftime", "/auth/ctftime/callback"
MOTH = (4321, "Moth Cipher")


@pytest.fixture()
def provider(monkeypatch):
    stand_in = MockCTFtime().start()
    monkeypatch.setenv("CTFTIME_CLIENT_ID", stand_in.client_id)
    monkeypatch.setenv("CTFTIME_CLIENT_SECRET", stand_in.client_secret)
    monkeypatch.setenv("CTFTIME_OAUTH_BASE", stand_in.url)
    monkeypatch.setenv("L3MON_PLATFORM_HOST", "play.example.test")
    for name in ("CTFTIME_REDIRECT_URI", "CTFTIME_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    yield stand_in
    stand_in.stop()


@pytest.fixture()
def world(provider):
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        yield SimpleNamespace(app=app, provider=provider)
    destroy_ctfd(app)


@pytest.fixture()
def logins():
    """Everything CTFd's `logins` logger writes (it does not propagate to the root logger, so caplog cannot see it)."""
    lines = []

    class Collect(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler, logger = Collect(), logging.getLogger("logins")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield lines
    logger.removeHandler(handler)
    logger.setLevel(old_level)


def sign_in(world, code, prof, next_path=None, client=None):
    """The whole round trip: the start (for the state) and the callback. -> (client, the callback's response)."""
    world.provider.profiles[code] = prof
    client = client or world.app.test_client()
    started = client.get(START + (f"?next={quote(next_path, safe='')}" if next_path else ""))
    assert started.status_code == 302, started.get_data(as_text=True)
    state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
    return client, client.get(f"{CALLBACK}?code={code}&state={state}")


def me(client):
    r = client.get("/api/v1/users/me")
    return r.get_json()["data"] if r.status_code == 200 else None


def login_page(client):
    return client.get("/login").get_data(as_text=True)


def assert_refused(world, client, response, message):
    assert response.status_code == 302 and urlsplit(response.headers["Location"]).path == "/login", response.headers.get("Location")
    assert me(client) is None, "nobody is signed in"
    assert message in login_page(client), message


# ---- off until it is configured -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["CTFTIME_CLIENT_ID", "CTFTIME_CLIENT_SECRET"])
def test_without_a_client_id_or_a_secret_both_routes_do_not_exist(world, monkeypatch, missing):
    monkeypatch.delenv(missing)
    client = world.app.test_client()
    assert client.get(START).status_code == 404 and client.get(f"{CALLBACK}?code=x&state=y").status_code == 404
    assert world.provider.seen == []


def test_a_secret_in_a_file_is_read_and_an_empty_file_is_no_secret(world, monkeypatch, tmp_path):
    monkeypatch.delenv("CTFTIME_CLIENT_SECRET")
    path = tmp_path / "CTFTIME_CLIENT_SECRET"
    monkeypatch.setenv("CTFTIME_CLIENT_SECRET_FILE", str(path))
    client = world.app.test_client()
    path.write_text("", encoding="utf-8")
    assert client.get(START).status_code == 404, "an empty file is the placeholder: the feature is off"
    path.write_text(world.provider.client_secret + "\n", encoding="utf-8")
    assert client.get(START).status_code == 302


def test_the_routes_do_not_exist_when_plugins_are_off(provider):
    app = create_ctfd(user_mode="teams", enable_plugins=False)
    with app.app_context(), app.test_client() as client:
        assert client.get(START).status_code == 404
    destroy_ctfd(app)


# ---- the start -------------------------------------------------------------------------------------------------------------------------

def test_the_start_sends_the_visitor_to_ctftime_with_everything_it_needs_and_remembers_the_state(world):
    client = world.app.test_client()
    r = client.get(START)
    assert r.status_code == 302 and r.headers["Cache-Control"] == "no-store"
    where = urlsplit(r.headers["Location"])
    assert f"{where.scheme}://{where.netloc}{where.path}" == world.provider.url + "/authorize"
    query = {k: v[0] for k, v in parse_qs(where.query).items()}
    assert query["response_type"] == "code" and query["client_id"] == world.provider.client_id
    assert query["redirect_uri"] == "https://play.example.test/auth/ctftime/callback"
    assert query["scope"] == "profile:read team:read"
    assert len(query["state"]) == 32 and int(query["state"], 16) >= 0, "16 random bytes as 32 hex characters"
    with client.session_transaction() as sess:
        assert sess["l3mon_ctftime"]["state"] == query["state"]
    other = world.app.test_client().get(START)
    assert parse_qs(urlsplit(other.headers["Location"]).query)["state"][0] != query["state"], "a new state each time"


def test_an_explicit_redirect_address_wins_over_the_platform_host(world, monkeypatch):
    monkeypatch.setenv("CTFTIME_REDIRECT_URI", "https://elsewhere.example/auth/ctftime/callback")
    query = parse_qs(urlsplit(world.app.test_client().get(START).headers["Location"]).query)
    assert query["redirect_uri"] == ["https://elsewhere.example/auth/ctftime/callback"]


@pytest.mark.parametrize("wanted,kept", [("/story/street", "/story/street"), ("//evil.example/x", None), ("https://evil.example", None), ("/\\evil.example", None), ("evil", None), ("/a\r\nSet-Cookie: x=1", None), ("/" + "a" * 300, None)])
def test_only_a_place_on_this_site_is_remembered_as_where_to_go_back_to(world, wanted, kept):
    client = world.app.test_client()
    client.get(START + "?next=" + quote(wanted, safe=""))
    with client.session_transaction() as sess:
        assert sess["l3mon_ctftime"]["next"] == kept


def test_someone_already_signed_in_is_sent_on_without_a_trip_to_ctftime(world):
    gen_user(db, name="asha", email="asha@example.com", password="password", verified=True)
    client = login_as_user(world.app, "asha")
    r = client.get(START + "?next=/story/street")
    assert r.status_code == 302 and r.headers["Location"].endswith("/story/street") and world.provider.seen == []


def test_the_start_is_limited_to_120_a_minute_for_an_address(world):
    client = world.app.test_client()
    codes = [client.get(START).status_code for _ in range(122)]
    assert codes[:120] == [302] * 120 and 429 in codes[120:]


# ---- a new player ---------------------------------------------------------------------------------------------------------------------

def test_a_new_player_gets_an_account_a_studio_and_a_session_and_ctftime_is_asked_the_documented_way(world):
    client, r = sign_in(world, "c-asha", profile(777, "Asha", "asha@example.com", MOTH), next_path="/story/street")
    assert r.status_code == 302 and r.headers["Location"].endswith("/story/street")
    user = Users.query.filter_by(oauth_id=777).one()
    assert (user.name, user.email, user.verified, user.password, user.type) == ("Asha", "asha@example.com", False, None, "user"), "unverified, with no password"
    team = Teams.query.filter_by(oauth_id=4321).one()
    assert team.name == "Moth Cipher" and team.captain_id == user.id and user.team_id == team.id
    assert me(client)["id"] == user.id
    token, profile_request = world.provider.seen
    assert token["method"] == "POST" and token["path"] == "/token" and token["authorization"] is None
    assert token["form"] == {"grant_type": "authorization_code", "code": "c-asha", "redirect_uri": "https://play.example.test/auth/ctftime/callback", "client_id": "4242", "client_secret": "secret-for-the-tests-only"}
    assert profile_request["path"] == "/user" and profile_request["authorization"] == "Bearer tok-c-asha"


def test_without_a_next_the_player_lands_on_the_front_page(world):
    _, r = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert r.headers["Location"].endswith("/") and urlsplit(r.headers["Location"]).path == "/"


def test_the_account_is_unverified_so_the_platforms_own_email_check_still_applies(world):
    set_config("verify_emails", True)
    client, _ = sign_in(world, "c1", profile(1, "Asha", "asha@example.com", MOTH))
    assert me(client) is not None
    answer = client.get("/api/v1/l3mon/board")
    assert answer.status_code == 403 and answer.get_json()["error"] == "unverified"


def test_a_player_whose_ctftime_profile_has_no_team_gets_an_account_and_no_studio(world):
    client, _ = sign_in(world, "c1", profile(2, "Ben", "ben@example.com"))
    assert me(client)["team_id"] is None and Teams.query.count() == 0


def test_a_later_player_of_the_same_ctftime_team_gets_an_account_and_no_studio_and_the_studio_is_never_changed(world, logins):
    """A CTFtime team's member list is not the event's roster (it holds former members), so nobody is put into a studio that exists."""
    sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    client, r = sign_in(world, "c2", profile(2, "B", "b@example.com", MOTH))
    assert r.status_code == 302 and me(client) is not None, "signed in"
    assert me(client)["team_id"] is None, "but with no studio: the captain's invite is the way in"
    assert [m.name for m in moth_studio().members] == ["A"] and moth_studio().captain_id == Users.query.filter_by(oauth_id=1).one().id and Teams.query.count() == 1
    assert any("already has a studio" in line for line in logins)


def moth_studio():
    return Teams.query.filter_by(oauth_id=4321).one()


def test_a_player_who_signs_in_again_is_never_put_back_in_a_studio_the_crew_took_them_out_of(world):
    client, _ = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    user = Users.query.filter_by(oauth_id=1).one()
    user.team_id = None  # what the crew's removal of a member leaves
    db.session.commit()
    again, r = sign_in(world, "c2", profile(1, "A", "a@example.com", MOTH))
    assert r.status_code == 302 and me(again)["team_id"] is None
    assert Users.query.filter_by(oauth_id=1).one().team_id is None and Teams.query.count() == 1 and len(moth_studio().members) == 0


def test_a_suspended_studio_is_not_joined_either(world):
    gen_team(db, name="Banned Studio", email="b@example.com", member_count=1, oauth_id=4321, banned=True)
    client, r = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert r.status_code == 302 and me(client)["team_id"] is None
    assert len(Teams.query.filter_by(oauth_id=4321).one().members) == 1


def test_a_studio_made_by_the_sign_in_cannot_be_joined_by_name_and_password_and_the_join_form_does_not_crash(world):
    sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    register_user(world.app, name="other", email="other@example.com")
    client = login_as_user(world.app, "other")
    client.get("/teams/join")
    with client.session_transaction() as sess:
        nonce = sess["nonce"]
    r = client.post("/teams/join", data={"name": "Moth Cipher", "password": "guess", "nonce": nonce})
    assert r.status_code == 200 and "incorrect" in r.get_data(as_text=True).lower(), "CTFd's own refusal, not a crash"
    assert Users.query.filter_by(name="other").one().team_id is None


def test_the_crew_can_close_the_making_of_studios_and_the_sign_in_still_makes_the_account(world):
    set_config("team_creation", False)
    client, _ = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert me(client)["team_id"] is None and Teams.query.count() == 0


def test_in_users_mode_there_are_no_studios_at_all(provider):
    app = create_ctfd(user_mode="users", enable_plugins=True)
    with app.app_context():
        client, r = sign_in(SimpleNamespace(app=app, provider=provider), "c1", profile(1, "A", "a@example.com", MOTH))
        assert r.status_code == 302 and me(client) is not None and Teams.query.count() == 0
    destroy_ctfd(app)


def test_a_player_already_in_a_studio_of_their_own_is_not_moved(world):
    gen_team(db, name="Own Studio", email="own@example.com", member_count=1)
    member = Teams.query.filter_by(name="Own Studio").one().members[0]
    member.oauth_id = 15
    db.session.commit()
    client, _ = sign_in(world, "c1", profile(15, "A", "a@example.com", MOTH))
    assert me(client)["team_id"] == member.team_id and Teams.query.filter_by(oauth_id=4321).first() is None


def test_two_studios_made_at_the_same_moment_with_one_name_leave_the_earlier_one(world, monkeypatch):
    earlier = gen_team(db, name="Moth Cipher", email="e@example.com", member_count=1)
    monkeypatch.setattr(oauth, "_studio_name_taken", lambda name: False)  # the race: nobody had the name when this request looked
    client, r = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert r.status_code == 302 and me(client) is not None and me(client)["team_id"] is None
    assert Teams.query.filter_by(name="Moth Cipher").count() == 1 and Teams.query.filter_by(name="Moth Cipher").one().id == earlier.id


def test_a_name_taken_by_another_studio_is_logged_for_the_crew_with_the_ctftime_team(world, caplog):
    caplog.set_level(logging.WARNING, logger="l3mon")
    gen_team(db, name="Moth Cipher", email="t@example.com", member_count=1)
    sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert any("4321" in r.getMessage() and "Moth Cipher" in r.getMessage() and "already has that name" in r.getMessage() for r in caplog.records)


# ---- registration rules that normal sign-up enforces


def test_a_registration_code_the_sign_in_cannot_give_means_no_new_account(world):
    set_config("registration_code", "sesame")
    client, r = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert_refused(world, client, r, oauth.NEEDS_CODE)
    assert Users.query.filter_by(oauth_id=1).first() is None


def test_the_email_allow_list_and_block_list_apply_to_a_ctftime_email_too(world):
    set_config("domain_whitelist", "college.example")
    client, r = sign_in(world, "c1", profile(1, "A", "a@elsewhere.example", MOTH))
    assert_refused(world, client, r, oauth.EMAIL_USED)
    client, r = sign_in(world, "c2", profile(2, "B", "b@college.example", MOTH))
    assert r.status_code == 302 and me(client) is not None, "an address on the list is fine"
    set_config("domain_whitelist", "")
    set_config("domain_blacklist", "spam.example")
    client, r = sign_in(world, "c3", profile(3, "C", "c@spam.example", MOTH))
    assert_refused(world, client, r, oauth.EMAIL_USED)
    assert Users.query.filter(Users.oauth_id.in_([1, 3])).count() == 0


# ---- names that draw nothing, and names that are all taken


@pytest.mark.parametrize("blank", ["\u3164", "\u2800", "\u115f\u1160", "\u206a", "\u034f", "\u200d", "\ufff9", "\uffa0", "★★★", "  .  "])
def test_a_name_that_shows_nothing_becomes_the_numbered_fallback_for_players_and_studios(world, blank):
    sign_in(world, "c1", profile(1, blank, "a@example.com", (60, blank)))
    assert Users.query.filter_by(oauth_id=1).one().name == "ctftime-1" and Teams.query.filter_by(oauth_id=60).one().name == "ctftime-team-60"


@pytest.mark.parametrize("sneaky", ["Mo⁪th", "Cipher⁢Moth", "A￹B", "Team­7"])
def test_a_name_with_a_hidden_format_character_among_its_letters_is_replaced(world, sneaky):
    sign_in(world, "c1", profile(1, sneaky, "a@example.com", (62, sneaky)))
    assert Users.query.filter_by(oauth_id=1).one().name == "ctftime-1" and Teams.query.filter_by(oauth_id=62).one().name == "ctftime-team-62"


@pytest.mark.parametrize("fine", ["നമസ്കാരം", "Asha_K", "Team 7", "Zoë", "क‍ष", "ک‌ب"])
def test_a_name_in_any_script_is_kept_when_it_shows_something(world, fine):
    sign_in(world, "c1", profile(1, fine, "a@example.com", (61, fine)))
    assert Users.query.filter_by(oauth_id=1).one().name == fine and Teams.query.filter_by(oauth_id=61).one().name == fine


def test_when_every_name_is_taken_no_account_is_made_and_no_taken_name_is_reused(world):
    for name in ["Asha", "ctftime-1"] + [f"Asha-{n}" for n in range(2, 50)] + [f"ctftime-1-{n}" for n in range(2, 50)]:
        gen_user(db, name=name, email=f"{name}@example.com", password="password", verified=True)
    client, r = sign_in(world, "c1", profile(1, "Asha", "a@example.com", MOTH))
    assert_refused(world, client, r, oauth.NO_NAME)
    assert Users.query.filter_by(oauth_id=1).first() is None and Users.query.filter_by(name="ctftime-1").count() == 1


# ---- HEAD, the session, the database, the secret


def test_a_head_request_counts_against_the_same_limit(world):
    client = world.app.test_client()
    first = [client.head(START).status_code for _ in range(60)] + [client.get(START).status_code for _ in range(60)]
    assert first == [302] * 120
    assert client.head(START).status_code == 429 and client.get(START).status_code == 429, "one counter for both"
    other = world.app.test_client()
    assert [other.head(f"{CALLBACK}?code=x&state=y").status_code for _ in range(122)][-1] == 429


def test_a_signed_in_visitor_is_not_swapped_to_another_account_by_a_state_kept_from_before(world):
    gen_user(db, name="password-user", email="pw@example.com", password="password", verified=True)
    world.provider.profiles["c1"] = profile(1, "Ctf", "ctf@example.com", MOTH)
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    client.get("/login")
    with client.session_transaction() as sess:
        nonce = sess["nonce"]
    client.post("/login", data={"name": "password-user", "password": "password", "nonce": nonce})
    assert me(client)["name"] == "password-user"
    r = client.get(f"{CALLBACK}?code=c1&state={state}")
    assert r.status_code == 302 and urlsplit(r.headers["Location"]).path == "/"
    assert me(client)["name"] == "password-user" and world.provider.seen == [] and Users.query.filter_by(oauth_id=1).first() is None


def test_no_database_transaction_is_held_while_ctftime_is_being_asked(world):
    """A transaction holds a pooled connection: a slow CTFtime would use up a worker's connections and stop flag submissions."""
    held = []
    real_post, real_get = oauth.requests.post, oauth.requests.get

    def spy(real):
        def call(*args, **kwargs):
            held.append(db.session().in_transaction())
            return real(*args, **kwargs)
        return call

    with mock.patch.object(oauth.requests, "post", side_effect=spy(real_post)), mock.patch.object(oauth.requests, "get", side_effect=spy(real_get)):
        sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert held == [False, False], held


def test_the_limits_in_the_code_are_the_documented_ones():
    assert oauth.TIMEOUT == (3.05, 8) and oauth.STATE_SECONDS == 600


def test_a_state_nine_minutes_old_is_still_good(world):
    world.provider.profiles["c1"] = profile(1, "A", "a@example.com", MOTH)
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    with client.session_transaction() as sess:
        sess["l3mon_ctftime"] = {**sess["l3mon_ctftime"], "at": sess["l3mon_ctftime"]["at"] - 540}
    assert me(client) is None
    r = client.get(f"{CALLBACK}?code=c1&state={state}")
    assert r.status_code == 302 and urlsplit(r.headers["Location"]).path == "/" and me(client) is not None


def test_a_half_configured_platform_says_so_once_at_load(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="l3mon")
    monkeypatch.setenv("CTFTIME_CLIENT_ID", "4242")
    monkeypatch.delenv("CTFTIME_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("CTFTIME_CLIENT_SECRET_FILE", raising=False)
    oauth.warn_if_half_configured()
    assert any("no CTFtime secret can be read" in r.getMessage() for r in caplog.records)
    caplog.clear()
    monkeypatch.setenv("CTFTIME_CLIENT_SECRET", "x")
    oauth.warn_if_half_configured()
    monkeypatch.delenv("CTFTIME_CLIENT_ID")
    monkeypatch.delenv("CTFTIME_CLIENT_SECRET")
    oauth.warn_if_half_configured()
    assert caplog.records == [], "set up completely, or not at all: nothing to say"


def test_the_same_player_signing_in_again_is_the_same_account_and_nothing_is_changed(world):
    client, _ = sign_in(world, "c1", profile(9, "Asha", "asha@example.com", MOTH))
    again, r = sign_in(world, "c2", profile(9, "Asha Renamed", "new@example.com", (4321, "Moth Cipher Renamed")))
    assert r.status_code == 302 and me(again)["name"] == "Asha"
    user = Users.query.filter_by(oauth_id=9).one()
    assert (user.name, user.email) == ("Asha", "asha@example.com") and Users.query.count() == 2 and Teams.query.one().name == "Moth Cipher"


def test_the_team_name_is_kept_exactly_because_ctftimes_feed_finds_a_team_by_its_name(world):
    exact = "Moth_Cipher's [A] team? ★ (IN)"
    sign_in(world, "c1", profile(1, "A", "a@example.com", (50, exact)))
    assert Teams.query.filter_by(oauth_id=50).one().name == exact


def test_a_team_name_with_markup_or_invisible_characters_is_replaced_not_stored(world):
    sign_in(world, "c1", profile(1, "A", "a@example.com", (51, "Evil <img src=x onerror=alert(1)>")))
    sign_in(world, "c2", profile(2, "B", "b@example.com", (52, "Zero​width‮flip")))
    assert Teams.query.filter_by(oauth_id=51).one().name == "ctftime-team-51"
    assert Teams.query.filter_by(oauth_id=52).one().name == "ctftime-team-52"


def test_the_session_id_changes_at_sign_in_so_nothing_a_visitor_held_before_is_the_signed_in_one(world):
    world.provider.profiles["c1"] = profile(1, "A", "a@example.com", MOTH)
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    session_cookie = lambda: next(c.value for c in client.cookie_jar if c.name == "session")  # noqa: E731
    before = session_cookie()
    assert client.get(f"{CALLBACK}?code=c1&state={state}").status_code == 302
    assert me(client) is not None and session_cookie() != before


def test_a_name_taken_by_another_studio_leaves_the_player_without_a_studio_and_takes_nothing_over(world):
    gen_team(db, name="Moth Cipher", email="t@example.com", member_count=1)
    before = Teams.query.count()
    client, r = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert r.status_code == 302 and me(client)["team_id"] is None and Teams.query.count() == before
    assert Teams.query.filter_by(oauth_id=4321).first() is None


def test_the_limit_of_studios_and_of_accounts_is_respected(world):
    set_config("num_teams", 1)
    sign_in(world, "c1", profile(1, "A", "a@example.com", (10, "One")))
    client, _ = sign_in(world, "c2", profile(2, "B", "b@example.com", (11, "Two")))
    assert me(client)["team_id"] is None and Teams.query.count() == 1
    set_config("num_users", Users.query.filter_by(banned=False, hidden=False).count())
    client, r = sign_in(world, "c3", profile(3, "C", "c@example.com"))
    assert_refused(world, client, r, oauth.FULL)
    assert Users.query.filter_by(oauth_id=3).first() is None


# ---- names ----------------------------------------------------------------------------------------------------------------------------

def test_a_user_name_that_is_taken_gets_a_number_and_one_that_is_an_email_address_keeps_the_part_before_the_at(world):
    gen_user(db, name="Asha", email="other@example.com", password="password", verified=True)
    sign_in(world, "c1", profile(1, "Asha", "a@example.com"))
    sign_in(world, "c2", profile(2, "ben@college.example", "b@example.com"))
    assert Users.query.filter_by(oauth_id=1).one().name == "Asha-2" and Users.query.filter_by(oauth_id=2).one().name == "ben"


def test_a_user_name_with_markup_or_nothing_in_it_becomes_ctftime_and_the_id(world):
    sign_in(world, "c1", profile(1, "<script>alert(1)</script>", "a@example.com"))
    sign_in(world, "c2", profile(2, "   ", "b@example.com"))
    sign_in(world, "c3", profile(3, "@@", "c@example.com"))
    assert [Users.query.filter_by(oauth_id=i).one().name for i in (1, 2, 3)] == ["ctftime-1", "ctftime-2", "ctftime-3"]


# ---- who may not sign in this way -----------------------------------------------------------------------------------------------------

def test_an_email_that_belongs_to_another_account_is_never_given_to_the_ctftime_user(world):
    gen_user(db, name="victim", email="victim@example.com", password="password", verified=True)
    client, r = sign_in(world, "c1", profile(5, "Mallory", "victim@example.com", MOTH))
    assert_refused(world, client, r, oauth.EMAIL_USED)
    assert Users.query.filter_by(oauth_id=5).first() is None and Users.query.filter_by(email="victim@example.com").one().oauth_id is None


def test_an_administrators_email_gets_nothing_either(world):
    client, r = sign_in(world, "c1", profile(6, "Mallory", "admin@examplectf.com"))
    assert_refused(world, client, r, oauth.EMAIL_USED)


def test_an_administrator_account_with_a_ctftime_id_cannot_be_opened_this_way(world):
    admin = Users.query.filter_by(name="admin").one()
    admin.oauth_id = 99
    db.session.commit()
    client, r = sign_in(world, "c1", profile(99, "Anyone", "anyone@example.com", MOTH))
    assert_refused(world, client, r, oauth.REFUSED)
    assert Teams.query.count() == 0, "and nothing is made for the visitor on the way"


def test_a_suspended_account_is_told_so_and_not_signed_in(world):
    gen_user(db, name="bad", email="bad@example.com", password="password", verified=True, banned=True, oauth_id=13)
    client, r = sign_in(world, "c1", profile(13, "bad", "bad@example.com", MOTH))
    assert_refused(world, client, r, oauth.BANNED)


def test_when_registration_is_closed_no_new_account_is_made_but_an_existing_one_signs_in(world):
    sign_in(world, "c1", profile(1, "Asha", "asha@example.com", MOTH))
    set_config("registration_visibility", "private")
    client, r = sign_in(world, "c2", profile(2, "Ben", "ben@example.com"))
    assert_refused(world, client, r, oauth.CLOSED)
    assert Users.query.filter_by(oauth_id=2).first() is None
    again, r = sign_in(world, "c3", profile(1, "Asha", "asha@example.com", MOTH))
    assert r.status_code == 302 and me(again) is not None


def test_a_profile_without_a_usable_email_makes_no_account(world):
    for n, bad in enumerate([None, "", "not an email", "a@b", "x" * 200 + "@example.com", 12345]):
        client, r = sign_in(world, f"c{n}", profile(100 + n, "P", bad, MOTH) if bad is not None else {"id": 100 + n, "name": "P", "team": {"id": 1, "name": "T"}})
        assert_refused(world, client, r, oauth.NO_EMAIL)
    assert Users.query.filter(Users.oauth_id >= 100).count() == 0


@pytest.mark.parametrize("bad_id", [None, 0, -4, "abc", 1.5, True, 2**31, [1], {"a": 1}, "\u00b2", "\u0663\u0662\u0661", "1" * 10, " 12", "12 "])
def test_a_profile_without_a_real_id_is_not_trusted(world, bad_id):
    client, r = sign_in(world, "c1", {"id": bad_id, "name": "P", "email": "p@example.com"})
    assert_refused(world, client, r, oauth.BAD_PROFILE)
    assert Users.query.filter_by(email="p@example.com").first() is None


def test_a_team_that_is_not_an_object_or_has_no_usable_id_is_no_team(world):
    for n, team in enumerate(["Moth", ["x"], {"id": "x", "name": "T"}, {"id": 5}, {"id": 5, "name": "   "}, {"id": 5, "name": 7}]):
        client, _ = sign_in(world, f"c{n}", {"id": 200 + n, "name": f"P{n}", "email": f"p{n}@example.com", "team": team})
        assert me(client) is not None and me(client)["team_id"] is None
    assert Teams.query.count() == 0


def test_an_id_given_as_digits_is_accepted_like_a_number(world):
    client, _ = sign_in(world, "c1", {"id": "321", "name": "P", "email": "p@example.com"})
    assert Users.query.filter_by(oauth_id=321).one() and me(client) is not None


# ---- the state, the denial, the code ----------------------------------------------------------------------------------------------------

def test_a_callback_nobody_started_is_refused_before_ctftime_is_asked_anything(world):
    client = world.app.test_client()
    r = client.get(f"{CALLBACK}?code=c1&state={'a' * 32}")
    assert_refused(world, client, r, oauth.STATE)
    assert world.provider.seen == []


def test_a_state_that_does_not_match_is_refused_and_spends_the_one_in_the_session(world):
    world.provider.profiles["c1"] = profile(1, "A", "a@example.com", MOTH)
    client = world.app.test_client()
    client.get(START)
    r = client.get(f"{CALLBACK}?code=c1&state={'b' * 32}")
    assert_refused(world, client, r, oauth.STATE)
    with client.session_transaction() as sess:
        assert "l3mon_ctftime" not in sess
    assert world.provider.seen == [] and Users.query.count() == 1


@pytest.mark.parametrize("given", ["", "x", "é" * 8])
def test_a_missing_or_odd_state_is_refused_without_an_error(world, given):
    client = world.app.test_client()
    client.get(START)
    assert_refused(world, client, client.get(f"{CALLBACK}?code=c1&state={given}"), oauth.STATE)


def test_a_callback_replayed_by_someone_now_signed_in_goes_to_the_front_page_and_asks_ctftime_nothing(world):
    world.provider.profiles["c1"] = profile(1, "A", "a@example.com", MOTH)
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    first = client.get(f"{CALLBACK}?code=c1&state={state}")
    assert first.status_code == 302 and urlsplit(first.headers["Location"]).path == "/" and me(client) is not None
    replay = client.get(f"{CALLBACK}?code=c1&state={state}")
    assert replay.status_code == 302 and urlsplit(replay.headers["Location"]).path == "/"
    assert len(world.provider.seen) == 2, "and CTFtime was not asked again"


def test_a_state_older_than_ten_minutes_is_refused(world):
    world.provider.profiles["c1"] = profile(1, "A", "a@example.com", MOTH)
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    with client.session_transaction() as sess:
        sess["l3mon_ctftime"] = {**sess["l3mon_ctftime"], "at": sess["l3mon_ctftime"]["at"] - oauth.STATE_SECONDS - 5}
    r = client.get(f"{CALLBACK}?code=c1&state={state}")
    assert_refused(world, client, r, oauth.STATE)
    assert world.provider.seen == []


def test_the_player_cancelling_at_ctftime_ends_quietly(world):
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    r = client.get(f"{CALLBACK}?error=access_denied&state={state}")
    assert_refused(world, client, r, oauth.DENIED)
    assert world.provider.seen == []


def test_a_callback_without_a_code_or_with_a_huge_one_asks_ctftime_nothing(world):
    for query in ("", "&code=", "&code=" + "x" * 2000):
        client = world.app.test_client()
        state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
        assert_refused(world, client, client.get(f"{CALLBACK}?state={state}{query}"), oauth.STATE)
    assert world.provider.seen == []


def test_a_code_that_ctftime_does_not_know_is_a_failure_not_a_crash(world):
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    r = client.get(f"{CALLBACK}?code=unknown&state={state}")
    assert_refused(world, client, r, oauth.UNAVAILABLE)


# ---- when CTFtime (or the Cloudflare in front of it) fails ----------------------------------------------------------------------------

@pytest.mark.parametrize("field,mode", [("token_mode", "403"), ("token_mode", "500"), ("token_mode", "not_json"), ("token_mode", "not_object"), ("token_mode", "no_token"),
                                        ("token_mode", "bad_token"), ("token_mode", "spaced_token"), ("token_mode", "500_with_token"), ("token_mode", "redirect"),
                                        ("user_mode", "403"), ("user_mode", "403_with_profile"), ("user_mode", "redirect"), ("user_mode", "not_json"), ("user_mode", "not_object")])
def test_every_way_ctftime_can_fail_ends_in_the_same_quiet_refusal_and_no_account(world, field, mode, caplog, logins):
    caplog.set_level(logging.WARNING, logger="l3mon")
    setattr(world.provider, field, mode)
    client, r = sign_in(world, "c-secret-code", profile(1, "A", "a@example.com", MOTH))
    assert_refused(world, client, r, oauth.UNAVAILABLE)
    assert Users.query.filter_by(oauth_id=1).first() is None and Teams.query.count() == 0
    assert any("refused" in line for line in logins), "the refusal is in the logins log, so the check below can see that log"
    logged = " ".join(record.getMessage() for record in caplog.records) + " ".join(logins)
    assert "unavailable" in logged and "c-secret-code" not in logged and "tok-" not in logged and world.provider.client_secret not in logged


@pytest.mark.parametrize("field", ["token_mode", "user_mode"])
def test_a_slow_ctftime_cannot_hold_a_worker_for_long(world, monkeypatch, field):
    monkeypatch.setattr(oauth, "TIMEOUT", (1, 0.4))
    setattr(world.provider, field, "slow")
    world.provider.delay = 1.5
    started = time.time()
    client, r = sign_in(world, "c1", profile(1, "A", "a@example.com", MOTH))
    assert time.time() - started < 1.4
    assert_refused(world, client, r, oauth.UNAVAILABLE)


def test_a_provider_that_cannot_be_reached_at_all_is_unavailable_too(world, monkeypatch):
    world.provider.profiles["c1"] = profile(1, "A", "a@example.com", MOTH)
    monkeypatch.setenv("CTFTIME_OAUTH_BASE", "http://127.0.0.1:9")
    client = world.app.test_client()
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    assert_refused(world, client, client.get(f"{CALLBACK}?code=c1&state={state}"), oauth.UNAVAILABLE)


def test_nothing_secret_is_ever_logged_on_the_good_path_either(world, caplog, capsys, logins):
    caplog.set_level(logging.DEBUG)
    sign_in(world, "c-secret-code", profile(1, "A", "a@example.com", MOTH))
    out, err = capsys.readouterr()
    assert any("signed in with CTFtime" in line for line in logins), "the sign-in is in the logins log, so the check below can see that log"
    everything = " ".join(record.getMessage() for record in caplog.records) + " ".join(logins) + out + err
    assert "c-secret-code" not in everything and "tok-c-secret-code" not in everything and world.provider.client_secret not in everything


def test_a_provider_address_that_is_neither_https_nor_this_machine_is_not_used(world, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="l3mon")
    monkeypatch.setenv("CTFTIME_OAUTH_BASE", "http://evil.example")
    assert oauth.base() == oauth.API
    monkeypatch.setenv("CTFTIME_OAUTH_BASE", "ftp://oauth.ctftime.org")
    assert oauth.base() == oauth.API
    monkeypatch.setenv("CTFTIME_OAUTH_BASE", "https://oauth.example.test/")
    assert oauth.base() == "https://oauth.example.test"
    monkeypatch.delenv("CTFTIME_OAUTH_BASE")
    assert oauth.base() == "https://oauth.ctftime.org"


# ---- the callback is not a way around anything ------------------------------------------------------------------------------------------

def test_the_callback_is_limited_to_120_a_minute_for_an_address(world):
    client = world.app.test_client()
    codes = [client.get(f"{CALLBACK}?code=x&state=y").status_code for _ in range(122)]
    assert codes[:120] == [302] * 120 and 429 in codes[120:]


def _lose_the_race(world, racing_row):
    """The callback of a new player whose own insert loses a race: `racing_row` is what another request committed just before it."""
    client = world.app.test_client()
    world.provider.profiles["c1"] = profile(7, "A", "a@example.com", MOTH)
    state = parse_qs(urlsplit(client.get(START).headers["Location"]).query)["state"][0]
    real = db.session.commit
    lost = {"done": False}

    def commit():
        if not lost["done"] and any(isinstance(obj, Users) and obj.oauth_id == 7 for obj in db.session.new):
            lost["done"] = True
            db.session.rollback()
            db.session.add(racing_row)
            real()
            raise IntegrityError("insert", {}, Exception("duplicate"))
        return real()

    with mock.patch.object(db.session, "commit", side_effect=commit):
        return client, client.get(f"{CALLBACK}?code=c1&state={state}")


def test_two_callbacks_for_the_same_new_player_at_once_never_end_in_an_error_page(world):
    """The second request finds the account the first made (the unique id catches the race): it signs in, it never crashes."""
    client, r = _lose_the_race(world, Users(name="A", email="a@example.com", oauth_id=7, verified=False))
    assert r.status_code == 302 and Users.query.filter_by(oauth_id=7).count() == 1
    assert me(client) is not None, "the account that was there first is the one signed in"


def test_a_race_lost_to_another_account_with_the_same_email_never_signs_in_as_that_account(world):
    """The racing row is somebody else's account (a password account with this email): the loser must be refused, not signed in as it."""
    client, r = _lose_the_race(world, Users(name="victim", email="a@example.com", password="password", verified=True))
    assert_refused(world, client, r, oauth.EMAIL_USED)
    assert Users.query.filter_by(email="a@example.com").one().oauth_id is None


