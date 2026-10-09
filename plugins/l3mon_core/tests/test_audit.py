"""The audit trail: every admin action leaves a line saying who, what, which target, why and when.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import datetime

from CTFd.models import Users, db
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.models import Audit
from tests.helpers import create_ctfd, destroy_ctfd


def test_a_line_records_who_what_which_target_why_and_when():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = Users.query.filter_by(name="admin").first()
        row = audit.record("release.set", "programme:alpha", "withheld -> released; reason: the fix is in", actor=admin)
        db.session.commit()
        saved = Audit.query.one()
        assert saved is row
        assert (saved.actor_id, saved.actor_name) == (admin.id, "admin")
        assert (saved.action, saved.target) == ("release.set", "programme:alpha")
        assert saved.detail == "withheld -> released; reason: the fix is in"
        assert abs((datetime.datetime.utcnow() - saved.at).total_seconds()) < 30
    destroy_ctfd(app)


def test_without_a_signed_in_administrator_the_line_says_system():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        audit.record("release.reconcile", "challenges", "2 shown")
        db.session.commit()
        saved = Audit.query.one()
        assert (saved.actor_id, saved.actor_name) == (None, "system")
    destroy_ctfd(app)


def test_system_true_names_the_system_even_when_an_administrator_is_signed_in():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = Users.query.filter_by(name="admin").first()
        audit.record("release.drop", "challenges", "on air: alpha", actor=admin, system=True)
        db.session.commit()
        saved = Audit.query.one()
        assert (saved.actor_id, saved.actor_name) == (None, "system")
    destroy_ctfd(app)


def test_the_line_is_not_committed_by_the_recorder_so_it_lives_and_dies_with_the_action():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        audit.record("release.set", "channel:street", "released")
        db.session.rollback()
        assert Audit.query.count() == 0
    destroy_ctfd(app)


def test_an_administrator_who_is_deleted_later_leaves_their_name_on_the_line():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        gone = Users(name="temp-admin", email="temp@example.com", password="password", type="admin")
        db.session.add(gone)
        db.session.commit()
        audit.record("release.set", "channel:street", "released", actor=gone)
        db.session.commit()
        db.session.delete(gone)
        db.session.commit()
        assert Audit.query.one().actor_name == "temp-admin"
    destroy_ctfd(app)


def test_an_over_long_detail_is_clipped_and_never_refused():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        audit.record("release.set", "x" * 300, "y" * 900)
        db.session.commit()
        saved = Audit.query.one()
        assert len(saved.detail) == 500 and saved.detail.endswith("…")
        assert len(saved.target) == 100 and len(saved.action) <= 40
    destroy_ctfd(app)


def test_recent_lines_come_newest_first_and_no_more_than_asked():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        for i in range(5):
            row = audit.record("release.set", f"programme:p{i}", f"step {i}")
            row.at = datetime.datetime(2026, 11, 28, 3, 30, i)
        db.session.commit()
        lines = audit.recent(limit=3)
        assert [l["detail"] for l in lines] == ["step 4", "step 3", "step 2"]
        assert set(lines[0]) == {"id", "at", "actor", "action", "target", "detail"}
        assert lines[0]["at"] == int(datetime.datetime(2026, 11, 28, 3, 30, 4, tzinfo=datetime.timezone.utc).timestamp())
        assert lines[0]["actor"] == "system"
        assert len(audit.recent()) == 5
    destroy_ctfd(app)
