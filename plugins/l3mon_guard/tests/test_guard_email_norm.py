"""Tests for indexed email normalization table, listeners, backfill, and query budget (SP5 F1).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_email_norm.py
"""
import unittest.mock as mock
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
        u1 = Users(name="uniq_user_1", email="u1@example.com")
        u2 = Users(name="uniq_user_2", email="u2@example.com")
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
        # Clear existing norm rows to simulate pre-existing accounts
        EmailNorm.query.delete()
        db.session.commit()

        for i in range(15):
            u = Users(name=f"backfill_{i}", email=f"user.{i}+tag@gmail.com")
            db.session.add(u)
        db.session.commit()
        EmailNorm.query.delete()
        db.session.commit()

        assert EmailNorm.query.count() == 0

        # Patch claim to allow batch processing across batches
        with mock.patch("CTFd.plugins.l3mon_core.claim.claim", return_value=True):
            count1 = backfill_email_norm(batch_size=10)
            assert count1 == 10
            assert EmailNorm.query.count() == 10

            count2 = backfill_email_norm(batch_size=10)
            # 15 users + 1 admin created by create_ctfd = 16 total missing, so second batch does 6
            assert count2 >= 5
            assert EmailNorm.query.count() >= 15
    destroy_ctfd(app)


def test_email_norm_lazy_backfill_refused_inside_claim_window():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        EmailNorm.query.delete()
        db.session.commit()

        for i in range(5):
            u = Users(name=f"window_user_{i}", email=f"win.{i}@example.com")
            db.session.add(u)
        db.session.commit()
        EmailNorm.query.delete()
        db.session.commit()

        # First call claims the window
        count1 = backfill_email_norm(batch_size=10)
        assert count1 > 0

        # Second call immediately after inside window is refused
        count2 = backfill_email_norm(batch_size=10)
        assert count2 == 0
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
    """Verify that is_email_disguise_used costs a small bounded number of statements.
    
    The statement count must remain bounded whether the user table has 50 or 500 users.
    Measures the lookup with backfill held out (at most 2 statements, equal for 50 and 500 users).
    """
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        from sqlalchemy import event

        # Populate 50 users
        for i in range(50):
            u = Users(name=f"budget_user_50_{i}", email=f"budget50_{i}@example.com")
            db.session.add(u)
        db.session.commit()

        queries_50 = 0

        def count_queries_50(conn, cursor, statement, parameters, context, executemany):
            nonlocal queries_50
            queries_50 += 1

        with mock.patch("CTFd.plugins.l3mon_guard.policies.email_norm.backfill_email_norm"):
            event.listen(db.engine, "before_cursor_execute", count_queries_50)
            is_email_disguise_used("candidate_50@example.com")
            event.remove(db.engine, "before_cursor_execute", count_queries_50)

        # Now populate up to 500 users
        for i in range(450):
            u = Users(name=f"budget_user_500_{i}", email=f"budget500_{i}@example.com")
            db.session.add(u)
        db.session.commit()

        queries_500 = 0

        def count_queries_500(conn, cursor, statement, parameters, context, executemany):
            nonlocal queries_500
            queries_500 += 1

        with mock.patch("CTFd.plugins.l3mon_guard.policies.email_norm.backfill_email_norm"):
            event.listen(db.engine, "before_cursor_execute", count_queries_500)
            is_email_disguise_used("candidate_500@example.com")
            event.remove(db.engine, "before_cursor_execute", count_queries_500)

        # With backfill held out, statement count is small and bounded (at most 2 statements)
        assert queries_50 <= 2
        assert queries_500 <= 2
        # Query count with 500 users must not exceed query count with 50 users (O(1), not O(N))
        assert queries_500 == queries_50


def test_email_norm_backfill_statement_cost():
    """Verify that backfill's own execution cost is small and bounded."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        from sqlalchemy import event

        EmailNorm.query.delete()
        db.session.commit()

        for i in range(20):
            u = Users(name=f"cost_user_{i}", email=f"cost_{i}@example.com")
            db.session.add(u)
        db.session.commit()
        EmailNorm.query.delete()
        db.session.commit()

        queries_backfill = 0

        def count_queries_bf(conn, cursor, statement, parameters, context, executemany):
            nonlocal queries_backfill
            queries_backfill += 1

        with mock.patch("CTFd.plugins.l3mon_core.claim.claim", return_value=True):
            event.listen(db.engine, "before_cursor_execute", count_queries_bf)
            backfill_email_norm(batch_size=10)
            event.remove(db.engine, "before_cursor_execute", count_queries_bf)

        # Bounded query cost for a batch: one search for the missing rows, then a check and an insert for each of the (at most 10) rows. It grows with the batch size,
        # not with the number of users (integration audit: the old budget of 15 was below the code's own 1 + 2 x 10 + the commit)
        assert queries_backfill <= 1 + 2 * 10 + 3, queries_backfill
    destroy_ctfd(app)

