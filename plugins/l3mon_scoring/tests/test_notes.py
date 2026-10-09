"""A studio's private lines: written by Revoke, Restore and Bonus; read (in 3.5) by that studio only.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import datetime

import pytest
from freezegun import freeze_time

from CTFd.models import db
from CTFd.plugins.l3mon_core.models import Note
from CTFd.plugins.l3mon_scoring import notes
from scoring_world import make_app, studio
from tests.helpers import destroy_ctfd


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


def test_a_line_is_added_to_the_session_and_not_committed(app):
    team = studio("t")
    line = notes.tell(team.id, "Solve voided", "the checker was broken")
    assert line in db.session and line.team_id == team.id
    db.session.rollback()
    assert Note.query.count() == 0, "tell() leaves the commit to the action it belongs to"


def test_lines_come_back_newest_first_with_a_limit_and_only_for_their_studio(app):
    a, b = studio("a"), studio("b")
    for minute, (title, text) in enumerate([("one", "first"), ("two", "second"), ("three", "third")]):
        with freeze_time(datetime.datetime(2026, 11, 28, 4, minute)):
            notes.tell(a.id, title, text)
            db.session.commit()
    notes.tell(b.id, "other", "not for a")
    db.session.commit()
    got = notes.latest(a.id, limit=2)
    assert [(n["title"], n["content"]) for n in got] == [("three", "third"), ("two", "second")]
    assert all(set(n) == {"title", "content", "at"} for n in got), "a line carries a title, its words and its time, nothing else"
    assert [n["title"] for n in notes.latest(a.id)] == ["three", "two", "one"]
    assert [n["title"] for n in notes.latest(b.id)] == ["other"]
    assert notes.latest(999) == []


def test_long_words_are_clipped_never_refused(app):
    team = studio("t")
    line = notes.tell(team.id, "t" * 300, "x" * 900)
    db.session.commit()
    assert len(line.title) == 100 and len(line.text) == 500 and line.title.endswith("…") and line.text.endswith("…")


def test_the_time_is_a_utc_epoch_second(app):
    team = studio("t")
    with freeze_time(datetime.datetime(2026, 11, 28, 4, 30, 0)):
        notes.tell(team.id, "x", "y")
        db.session.commit()
    assert notes.latest(team.id)[0]["at"] == 1795840200  # 10:00 IST is 04:30 UTC
