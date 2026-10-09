"""Releasing a savepoint is not a commit.

SQLAlchemy 1.4 runs `after_commit` listeners when a nested transaction (a savepoint) is released, before the real commit. Restore
uses savepoints. Two of our hooks must therefore wait for the real commit: clearing CTFd's caches (an earlier clear would let another
request cache the old numbers again) and moving the tick (a client that saw the new number early would fetch old data). Found by the
independent audit: harmless today because of the order the code happens to run in, but one reordering from a stale read.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
from unittest import mock

import pytest

from CTFd.models import db
from CTFd.plugins.l3mon_core.tick import tick
from CTFd.plugins.l3mon_scoring import notes, triggers, values
from scoring_world import make_app, studio
from tests.helpers import destroy_ctfd


@pytest.fixture()
def app():
    app = make_app()
    with app.app_context():
        yield app
    destroy_ctfd(app)


def test_the_caches_are_cleared_by_the_real_commit_not_by_a_savepoint(app):
    team = studio("alpha")
    values.mark_changed()
    with mock.patch.object(triggers, "clear_challenges") as challenges, mock.patch.object(triggers, "clear_standings") as standings:
        with db.session.begin_nested():
            notes.tell(team.id, "inside", "a savepoint")
        challenges.assert_not_called()
        standings.assert_not_called()
        assert db.session.info.get(values.CLEAR_FLAG), "the request to clear is still waiting"
        db.session.commit()
        challenges.assert_called_once()
        standings.assert_called_once()
        assert not db.session.info.get(values.CLEAR_FLAG)


def test_the_tick_moves_once_at_the_real_commit_not_when_a_savepoint_is_released(app):
    team = studio("alpha")
    before = tick.value()
    with db.session.begin_nested():
        notes.tell(team.id, "inside", "a savepoint")
    assert tick.value() == before, "a savepoint released: nothing is committed, so nothing a client can see has changed"
    db.session.commit()
    assert tick.value() == before + 1


# ---- the same rule for the triggers: a flush only notes a change, the request end recalculates ----------------------------------------

def test_a_flush_only_notes_what_changed_and_the_end_of_the_request_recalculates(app):
    """Recalculating inside the transaction that added a solve deadlocked simultaneous solves (see test_solves_mariadb.py): the value is
    recalculated after the cause has committed, in a fresh transaction."""
    import datetime

    from CTFd.models import Solves
    from scoring_world import dynamic

    team = studio("alpha")
    cid, team_id, user_id = dynamic("dyn").id, team.id, team.captain_id
    calls = []
    real = values.recalculate
    with app.test_request_context("/api/v1/some/crew/action"), mock.patch.object(values, "recalculate", side_effect=lambda ids=None, lock=False: calls.append(ids) or real(ids, lock)):
        db.session.add(Solves(user_id=user_id, team_id=team_id, challenge_id=cid, ip="127.0.0.1", provided="x", date=datetime.datetime(2026, 11, 28, 4, 0)))
        db.session.commit()
        assert calls == [], "nothing is recalculated inside the transaction that added the solve"
        app.process_response(app.response_class())  # the request ends
        assert calls == [{cid}], "and then only the solved challenge is"


def test_a_players_own_flag_is_left_to_ctfd_and_a_ban_recalculates_everything(app):
    import datetime

    from CTFd.models import Solves, Teams
    from scoring_world import dynamic

    team = studio("alpha")
    cid, team_id, user_id = dynamic("dyn").id, team.id, team.captain_id
    calls = []
    real = values.recalculate
    spy = mock.patch.object(values, "recalculate", side_effect=lambda ids=None, lock=False: calls.append(ids) or real(ids, lock))
    with app.test_request_context(triggers.ATTEMPT, method="POST"), spy:
        db.session.add(Solves(user_id=user_id, team_id=team_id, challenge_id=cid, ip="127.0.0.1", provided="x", date=datetime.datetime(2026, 11, 28, 4, 0)))
        db.session.commit()
        app.process_response(app.response_class())
        assert calls == [], "CTFd values the challenge itself right after a player's flag"
    with app.test_request_context("/api/v1/teams/1", method="PATCH"), spy:
        Teams.query.filter_by(id=team_id).first().banned = True
        db.session.commit()
        assert calls == []
        app.process_response(app.response_class())
        assert calls == [None], "None means every curve-valued challenge"


def test_outside_a_request_nothing_is_noted_and_nothing_recalculated(app):
    """A script or a test that writes to the database directly is the minute check's business."""
    import datetime

    from CTFd.models import Solves
    from scoring_world import dynamic

    team = studio("alpha")
    cid, team_id, user_id = dynamic("dyn").id, team.id, team.captain_id
    calls = []
    with mock.patch.object(values, "recalculate", side_effect=lambda ids=None, lock=False: calls.append(ids)):
        db.session.add(Solves(user_id=user_id, team_id=team_id, challenge_id=cid, ip="127.0.0.1", provided="x", date=datetime.datetime(2026, 11, 28, 4, 0)))
        db.session.commit()
    assert calls == []
