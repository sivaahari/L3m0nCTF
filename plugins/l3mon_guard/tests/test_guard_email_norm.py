"""Tests for indexed email normalization table, listeners, backfill, and query budget (SP5 F1).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_email_norm.py
"""
from unittest import mock

import pytest
from sqlalchemy.exc import IntegrityError
from tests.helpers import create_ctfd, destroy_ctfd, register_user

from CTFd.models import Users, db
from CTFd.plugins.l3mon_guard.models import EmailNorm
from CTFd.plugins.l3mon_guard.policies.email_norm import (
    backfill_email_norm,
    is_email_disguise_used,
    normalize_email,
)


def test_email_norm_model_and_listener_on_insert():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        user = Users(name="insert_test", email="alice.smith+ctf@googlemail.com")
        db.session.add(user)
        db.session.commit()

        norm_row = EmailNorm.query.filter_by(user_id=user.id).first()
        assert norm_row is not None
        assert norm_row.norm == "alicesmith@gmail.com"
        assert norm_row.user_id == user.id
    destroy_ctfd(app)


def test_email_norm_listener_on_update():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        user = Users(name="update_test", email="initial@example.com")
        db.session.add(user)
        db.session.commit()

        norm_row = EmailNorm.query.filter_by(user_id=user.id).first()
        assert norm_row is not None
        assert norm_row.norm == "initial@example.com"

        # Update user's email
        user.email = "bob+new@example.com"
        db.session.commit()

        norm_row2 = EmailNorm.query.filter_by(user_id=user.id).first()
        assert norm_row2 is not None
        assert norm_row2.norm == "bob@example.com"
    destroy_ctfd(app)


def test_email_norm_listener_on_delete():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        user = Users(name="delete_test", email="del@example.com")
        db.session.add(user)
        db.session.commit()
        uid = user.id

        assert EmailNorm.query.filter_by(user_id=uid).first() is not None

        db.session.delete(user)
        db.session.commit()

        assert EmailNorm.query.filter_by(user_id=uid).first() is None
    destroy_ctfd(app)


def test_email_norm_unique_constraint():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        # two real users: the table has a foreign key to users, and the listener already gave each user a row of its own
        u1 = Users(name="collide_1", email="first@example.com")
        u2 = Users(name="collide_2", email="second@example.com")
        db.session.add_all([u1, u2])
        db.session.commit()
        EmailNorm.query.delete()
        db.session.commit()
        row1 = EmailNorm(user_id=u1.id, norm="collision@example.com")
        row2 = EmailNorm(user_id=u2.id, norm="collision@example.com")
        db.session.add(row1)
        db.session.commit()

        db.session.add(row2)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
    destroy_ctfd(app)


def test_email_norm_lazy_backfill():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        # Clear out any existing norm rows to simulate pre-existing accounts
        EmailNorm.query.delete()
        db.session.commit()

        # Create multiple users directly without listener inserting to norm table
        # by temporarily adding users and clearing norm
        for i in range(15):
            u = Users(name=f"backfill_{i}", email=f"user.{i}+tag@gmail.com")
            db.session.add(u)
        db.session.commit()
        EmailNorm.query.delete()
        db.session.commit()

        assert EmailNorm.query.count() == 0

        # Trigger lazy backfill (one batch at a time: the real function takes a ten-second claim, so a second batch inside that window is refused)
        with mock.patch("CTFd.plugins.l3mon_core.claim.claim", return_value=True):
            count = backfill_email_norm(batch_size=10)
            assert count == 10
            assert EmailNorm.query.count() == 10

            # Second batch backfills remainder
            total = Users.query.filter(Users.email.isnot(None), Users.email != "").count()  # the 15 above and the administrator CTFd made
            count2 = backfill_email_norm(batch_size=10)
            assert count2 == total - 10
            assert EmailNorm.query.count() == total

        # and inside the claim window the function does nothing (one batch per ten seconds across all workers)
        EmailNorm.query.delete()
        db.session.commit()
        with mock.patch("CTFd.plugins.l3mon_core.claim.claim", return_value=False):
            assert backfill_email_norm(batch_size=10) == 0
            assert EmailNorm.query.count() == 0
    destroy_ctfd(app)


def test_is_email_disguise_used_indexed():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        register_user(app, name="hero", email="hero.student@gmail.com")

        # Disguised forms detected via indexed table
        assert is_email_disguise_used("h.e.r.o.student+alias@googlemail.com") is True
        assert is_email_disguise_used("herostudent@gmail.com") is True

        # Unrelated address is not used
        assert is_email_disguise_used("villain@gmail.com") is False

        # Exclude self user ID allows update
        hero_user = Users.query.filter_by(name="hero").first()
        assert is_email_disguise_used("hero.student@gmail.com", exclude_user_id=hero_user.id) is False
    destroy_ctfd(app)


def test_email_disguise_query_budget_bounded():
    """is_email_disguise_used costs a small bounded number of statements, whether the user table has 50 or 500 users.

    The lookup is measured with the lazy backfill held out (it takes a ten-second claim, so a second call inside the window would cost less and the two
    measurements would not be comparable); the backfill's own statements are measured separately, when there is nothing to fill.
    """
    from sqlalchemy import event

    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        def statements(call):
            counter = {"n": 0}

            def count(conn, cursor, statement, parameters, context, executemany):
                counter["n"] += 1

            event.listen(db.engine, "before_cursor_execute", count)
            try:
                call()
            finally:
                event.remove(db.engine, "before_cursor_execute", count)
            return counter["n"]

        def lookup():
            with mock.patch("CTFd.plugins.l3mon_guard.policies.email_norm.backfill_email_norm", return_value=0):
                is_email_disguise_used("candidate@example.com")

        for i in range(50):
            db.session.add(Users(name=f"budget_user_50_{i}", email=f"budget50_{i}@example.com"))
        db.session.commit()
        queries_50 = statements(lookup)

        for i in range(450):
            db.session.add(Users(name=f"budget_user_500_{i}", email=f"budget500_{i}@example.com"))
        db.session.commit()
        queries_500 = statements(lookup)

        assert queries_50 <= 2 and queries_500 <= 2, (queries_50, queries_500)
        assert queries_500 == queries_50, "the cost must not grow with the number of users"

        # with every account already filled in, the backfill is a claim and one search: bounded as well, and the same at 50 and at 500
        with mock.patch("CTFd.plugins.l3mon_core.claim.claim", return_value=True):
            backfill_cost = statements(lambda: backfill_email_norm())
        assert backfill_cost <= 3, backfill_cost
    destroy_ctfd(app)
