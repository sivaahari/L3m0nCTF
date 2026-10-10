"""Who may ask the player endpoints, and what each refusal looks like (the contract's gate order: signed in, banned, verified, has a studio).

A refusal is the contract's JSON envelope, never a redirect and never a page, so the pages' script can tell what happened. The crew
(administrators) pass without a studio. Every player endpoint is checked: the board, the tick, the Guide and its grid, the scoreboard and its rows, the bell.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import re

import pytest

from CTFd.models import Users, db
from CTFd.utils import set_config
from board_world import BOARD, TICKS, make_app, team_client
from tests.helpers import destroy_ctfd, login_as_user, register_user

REQUEST_ID = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")
GUIDE, EPG = "/api/v1/l3mon/guide", "/api/v1/l3mon/guide/epg"
SCOREBOARD, ROWS = "/api/v1/l3mon/scoreboard", "/api/v1/l3mon/scoreboard/rows"
BELL = "/api/v1/notifications"
PATHS = [BOARD, TICKS, GUIDE, EPG, SCOREBOARD, ROWS, BELL]  # every player endpoint of SP3 parts 3.4 and 3.5 meets the same gate
FRAGMENTS = (EPG, ROWS)  # a let-in answer to these is markup, not the JSON envelope (their refusals are the envelope all the same)
READ_ONLY = [BOARD, TICKS, GUIDE, EPG, SCOREBOARD, ROWS]  # the bell's address is also CTFd's own write route, which only the crew may use


@pytest.fixture()
def app():
    app = make_app()
    yield app
    destroy_ctfd(app)


@pytest.mark.parametrize("path", PATHS)
def test_a_visitor_gets_401_auth_required_in_json_and_not_a_redirect(app, path):
    with app.app_context():
        r = app.test_client().get(path)
        assert r.status_code == 401 and r.mimetype == "application/json"
        body = r.get_json()
        assert body["success"] is False and body["error"] == "auth_required" and REQUEST_ID.match(body["request_id"])
        assert r.headers["X-Request-Id"] == body["request_id"] and r.headers["Cache-Control"] == "no-store"
        assert "data" not in body


@pytest.mark.parametrize("path", PATHS)
def test_a_player_with_a_studio_is_let_in(app, path):
    with app.app_context():
        alice = team_client(app, "alice", "studio-a")
        r = alice.get(path)
        assert r.status_code == 200 and REQUEST_ID.match(r.headers["X-Request-Id"])
        assert r.mimetype == "text/html" if path in FRAGMENTS else r.get_json()["success"] is True


@pytest.mark.parametrize("path", PATHS)
def test_a_player_with_no_studio_yet_gets_403_no_team(app, path):
    with app.app_context():
        register_user(app, name="carol", email="carol@example.com")
        carol = login_as_user(app, "carol")
        r = carol.get(path)
        assert r.status_code == 403 and r.get_json()["error"] == "no_team" and "data" not in r.get_json()


@pytest.mark.parametrize("path", PATHS)
def test_an_unverified_player_gets_403_unverified_when_verification_is_required_and_that_comes_before_the_studio_check(app, path):
    with app.app_context():
        alice = team_client(app, "alice", "studio-a")
        set_config("verify_emails", True)
        user = Users.query.filter_by(name="alice").first()
        user.verified = False
        db.session.commit()
        r = alice.get(path)
        assert r.status_code == 403 and r.get_json()["error"] == "unverified"
        # the order is the contract's: unverified is told before the missing studio is
        register_user(app, name="dave", email="dave@example.com")
        dave = login_as_user(app, "dave")
        Users.query.filter_by(name="dave").first().verified = False
        db.session.commit()
        assert dave.get(path).get_json()["error"] == "unverified"
        # a verified player passes again
        user = Users.query.filter_by(name="alice").first()
        user.verified = True
        db.session.commit()
        assert alice.get(path).status_code == 200


@pytest.mark.parametrize("path", PATHS)
def test_a_banned_player_gets_403_banned_in_json(app, path):
    with app.app_context():
        alice = team_client(app, "alice", "studio-a")
        Users.query.filter_by(name="alice").first().banned = True
        db.session.commit()
        r = alice.get(path)
        assert r.status_code == 403 and r.mimetype == "application/json" and r.get_json()["error"] == "banned" and "data" not in r.get_json()


@pytest.mark.parametrize("path", PATHS)
def test_the_crew_is_let_in_without_a_studio(app, path):
    with app.app_context():
        admin = login_as_user(app, "admin")
        r = admin.get(path)
        assert r.status_code == 200
        assert r.mimetype == "text/html" if path in FRAGMENTS else r.get_json()["success"] is True


def test_only_get_is_allowed_and_nothing_here_writes(app):
    with app.app_context():
        alice = team_client(app, "alice", "studio-a")
        for path in READ_ONLY:
            for method in ("post", "put", "patch", "delete"):
                r = getattr(alice, method)(path, json={})
                assert r.status_code in (404, 405), (method, path, r.status_code)
