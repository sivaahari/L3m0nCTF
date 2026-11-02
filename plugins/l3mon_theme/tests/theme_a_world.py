"""The made-up world of the board plugin's tests (three channels, seven programmes, studios), for the board and notifications page tests of
stream A. Not a test file. The board plugin's own helper is used as it is: it is mounted next to these tests at /l3mon_tests/l3mon_board
in the test container and lives at plugins/l3mon_board/tests in the repository.
"""
import os
import sys

for _candidate in ("/l3mon_tests/l3mon_board", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "l3mon_board", "tests")):
    if os.path.isdir(_candidate) and os.path.abspath(_candidate) not in sys.path:
        sys.path.insert(0, os.path.abspath(_candidate))

import datetime  # noqa: E402
import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402

from board_world import (  # noqa: E402,F401
    T_END, T_LIVE, T_START, clock, join_client, nonce_of, on_air, started, team_client, withhold, world,
)
from CTFd.models import db  # noqa: E402
from CTFd.utils import set_config  # noqa: E402
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user  # noqa: E402,F401

PASSWORD = "a-long-test-password"  # made up, for the throwaway test app only


def make_app():
    """The board plugin's test app (team mode, the day of the round), with the platform's own theme: the pages under test are the theme's."""
    app = create_ctfd(user_mode="teams", enable_plugins=True, ctf_theme="l3mon")
    app.permanent_session_lifetime = datetime.timedelta(days=3650)  # a sign-in made today must still be good on the day of the round
    return app


@pytest.fixture()
def play():
    """The day of the round at 10:00 IST: the world loaded; on air are the street's first two programmes (the third is held back), the snack
    square's first and third (the third needs the first of the street) and the sponsored break; studios alice+abe, bob; the crew."""
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "moth_cipher", "noodle_ledger", "dumpling_gate", "coffee_break")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, abe=abe, bob=bob)
    destroy_ctfd(app)


def statements_of(fn):
    """(the result of fn(), the SQL statements it ran)."""
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return result, seen


def text(response):
    return response.get_data(as_text=True)


def main_of(html):
    """The page's <main> element, which is where the board lives (the header carries the session's token)."""
    m = re.search(r"<main\b.*?</main>", html, re.S)
    assert m, "the page has a main element"
    return m.group(0)


def add_notification(title, content, team_id=None, user_id=None, when=None):
    """A CTFd notification row straight in the database (the crew's route also pushes it to open event streams, which a test does not need)."""
    from CTFd.models import Notifications

    row = Notifications(title=title, content=content, team_id=team_id, user_id=user_id)
    if when is not None:
        row.date = when
    db.session.add(row)
    db.session.commit()
    return row


def clear_news():
    """Remove the public line "New on air" that putting programmes on air really writes, so a test can count exactly what it adds."""
    from CTFd.models import Notifications

    Notifications.query.delete()
    db.session.commit()


__all__ = ["play", "statements_of", "text", "main_of", "add_notification", "clear_news", "set_config", "datetime", "SimpleNamespace"]
