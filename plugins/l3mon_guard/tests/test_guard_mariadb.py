"""MariaDB migration verification for l3mon_guard (SP5 B4.7, B6, F1).

Verifies on a real MariaDB instance (via tools/run-migration-test.sh):
1. Migration 1a2b3c4d5e6f builds the schema described by Evidence in models.py.
2. Migration 3e4f5a6b7c8d builds the schema described by EmailNorm in models.py.
3. Check constraints on kind and status are enforced by MariaDB itself.
4. Foreign keys with ON DELETE SET NULL (Evidence) and CASCADE (EmailNorm) behave as expected.
5. Unique constraints on user_id and norm are enforced by MariaDB itself.
6. Simultaneous inserts of the same normalized email across concurrent workers yield exactly one row.
7. Migrations are idempotent (can run repeatedly) and support downgrade/re-upgrade.
"""
import concurrent.futures
import importlib.util
import os
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError

from CTFd.models import Challenges, Teams, Users, db
from CTFd.plugins.l3mon_guard import models
from CTFd.plugins.l3mon_guard.models import EmailNorm, Evidence
from CTFd.plugins.migrations import current, upgrade
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_team, gen_user

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

REVISION_EVIDENCE = "1a2b3c4d5e6f"
REVISION_EMAIL_NORM = "3e4f5a6b7c8d"
REVISION_HEAD = "3e4f5a6b7c8d"


def _migration_module():
    here = os.path.dirname(os.path.abspath(models.__file__))
    path = os.path.join(here, "migrations", "1a2b3c4d5e6f_create_the_guard_evidence_table.py")
    spec = importlib.util.spec_from_file_location("l3mon_guard_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _email_norm_migration_module():
    here = os.path.dirname(os.path.abspath(models.__file__))
    path = os.path.join(here, "migrations", "3e4f5a6b7c8d_create_the_guard_email_norm_table.py")
    spec = importlib.util.spec_from_file_location("l3mon_guard_email_norm_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_on_connection(fn):
    with db.engine.begin() as conn:
        fn(Operations(MigrationContext.configure(conn)))


def tables():
    return set(inspect(db.engine).get_table_names())


def test_guard_table_created_and_head_recorded():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert "l3mon_evidence" in tables()
        assert "l3mon_email_norm" in tables()
        assert current("l3mon_guard") == REVISION_HEAD
    destroy_ctfd(app)


def test_guard_table_schema_matches_model():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        insp = inspect(db.engine)
        real_cols = {c["name"]: c for c in insp.get_columns("l3mon_evidence")}
        want_cols = {c.name: c for c in Evidence.__table__.columns}

        assert set(real_cols) == set(want_cols)
        for col, spec in want_cols.items():
            assert bool(real_cols[col]["nullable"]) == bool(spec.nullable), f"{col} nullable"
            assert real_cols[col]["type"].python_type == spec.type.python_type, f"{col} python_type"
            length = getattr(spec.type, "length", None)
            if length:
                assert real_cols[col]["type"].length == length, f"{col} length"

        real_fks = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
            for fk in insp.get_foreign_keys("l3mon_evidence")
        }
        want_fks = {
            (tuple(fk.parent.name for fk in c.elements), c.elements[0].column.table.name, c.ondelete)
            for c in Evidence.__table__.foreign_key_constraints
        }
        assert real_fks == want_fks

        real_checks = {c["name"] for c in insp.get_check_constraints("l3mon_evidence")}
        want_checks = {c.name for c in Evidence.__table__.constraints if c.__class__.__name__ == "CheckConstraint"}
        assert real_checks == want_checks
    destroy_ctfd(app)


def test_guard_mariadb_enforces_check_constraints():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        # Valid insert succeeds
        valid = Evidence(kind="burst", status="new", detail="valid")
        db.session.add(valid)
        db.session.commit()

        # Invalid kind is refused by database check constraint
        bad_kind = Evidence(kind="invalid_kind", status="new", detail="bad")
        db.session.add(bad_kind)
        with pytest.raises(DBAPIError):
            db.session.commit()
        db.session.rollback()

        # Invalid status is refused by database check constraint
        bad_status = Evidence(kind="burst", status="unknown_status", detail="bad")
        db.session.add(bad_status)
        with pytest.raises(DBAPIError):
            db.session.commit()
        db.session.rollback()
    destroy_ctfd(app)


def test_guard_foreign_key_set_null_on_delete():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="To Be Deleted")
        row = Evidence(kind="burst", team_id=team.id, status="new", detail="evidence on team")
        db.session.add(row)
        db.session.commit()
        row_id = row.id

        # Delete the team
        db.session.delete(team)
        db.session.commit()

        # row survives with team_id set to NULL
        evidence = Evidence.query.filter_by(id=row_id).first()
        assert evidence is not None
        assert evidence.team_id is None
    destroy_ctfd(app)


def test_guard_migration_idempotent_and_downgrade():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        mod = _migration_module()

        # Running upgrade again changes nothing and does not error
        run_on_connection(lambda op: mod.upgrade(op=op))
        assert "l3mon_evidence" in tables()

        # Downgrade drops l3mon_evidence
        run_on_connection(lambda op: mod.downgrade(op=op))
        assert "l3mon_evidence" not in tables()

        # Second downgrade is safe
        run_on_connection(lambda op: mod.downgrade(op=op))
        assert "l3mon_evidence" not in tables()

        # Re-upgrade brings the table back
        run_on_connection(lambda op: mod.upgrade(op=op))
        assert "l3mon_evidence" in tables()
    destroy_ctfd(app)


def test_email_norm_table_schema_and_constraints():
    """Verify that migration 3e4f5a6b7c8d creates the foreign key and two unique constraints (SP5 F1)."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        insp = inspect(db.engine)
        assert "l3mon_email_norm" in tables()

        real_cols = {c["name"]: c for c in insp.get_columns("l3mon_email_norm")}
        want_cols = {c.name: c for c in EmailNorm.__table__.columns}
        assert set(real_cols) == set(want_cols)

        real_fks = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
            for fk in insp.get_foreign_keys("l3mon_email_norm")
        }
        want_fks = {
            (tuple(fk.parent.name for fk in c.elements), c.elements[0].column.table.name, c.ondelete)
            for c in EmailNorm.__table__.foreign_key_constraints
        }
        assert real_fks == want_fks

        # Two unique constraints: user_id and norm
        real_unique = {tuple(u["column_names"]) for u in insp.get_unique_constraints("l3mon_email_norm")}
        assert ("user_id",) in real_unique
        assert ("norm",) in real_unique
    destroy_ctfd(app)


def test_email_norm_mariadb_enforces_unique_and_foreign_key():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        u1 = gen_user(db, name="Norm User 1", email="norm1@example.com")
        u2 = gen_user(db, name="Norm User 2", email="norm2@example.com")
        EmailNorm.query.delete()
        db.session.commit()

        # Valid insert
        row1 = EmailNorm(user_id=u1.id, norm="norm1@example.com")
        db.session.add(row1)
        db.session.commit()

        # Duplicate norm must fail with DBAPIError
        dup_norm = EmailNorm(user_id=u2.id, norm="norm1@example.com")
        db.session.add(dup_norm)
        with pytest.raises(DBAPIError):
            db.session.commit()
        db.session.rollback()

        # Duplicate user_id must fail with DBAPIError
        dup_uid = EmailNorm(user_id=u1.id, norm="another.norm@example.com")
        db.session.add(dup_uid)
        with pytest.raises(DBAPIError):
            db.session.commit()
        db.session.rollback()

        # Foreign key cascade on delete
        db.session.delete(u1)
        db.session.commit()
        assert EmailNorm.query.filter_by(user_id=u1.id).first() is None
    destroy_ctfd(app)


def test_email_norm_migration_idempotent_and_downgrade():
    """Verify that migration 3e4f5a6b7c8d runs twice safely and supports downgrade/re-upgrade."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        mod = _email_norm_migration_module()

        # Running upgrade again changes nothing and does not error
        run_on_connection(lambda op: mod.upgrade(op=op))
        assert "l3mon_email_norm" in tables()

        # Downgrade drops l3mon_email_norm
        run_on_connection(lambda op: mod.downgrade(op=op))
        assert "l3mon_email_norm" not in tables()

        # Second downgrade is safe
        run_on_connection(lambda op: mod.downgrade(op=op))
        assert "l3mon_email_norm" not in tables()

        # Re-upgrade brings the table back
        run_on_connection(lambda op: mod.upgrade(op=op))
        assert "l3mon_email_norm" in tables()
    destroy_ctfd(app)


def test_email_norm_concurrent_race_gives_one_row():
    """Verify that two workers inserting the same normalized email at once get exactly one row."""
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        u1 = gen_user(db, name="Race User 1", email="race1@example.com")
        u2 = gen_user(db, name="Race User 2", email="race2@example.com")
        EmailNorm.query.delete()
        db.session.commit()

        target_norm = "shared.race@gmail.com"

        def insert_worker(user_id):
            with app.app_context():
                with db.engine.begin() as conn:
                    try:
                        conn.execute(
                            EmailNorm.__table__.insert().values(user_id=user_id, norm=target_norm)
                        )
                        return True
                    except DBAPIError:
                        return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(insert_worker, u1.id)
            f2 = executor.submit(insert_worker, u2.id)
            res1 = f1.result()
            res2 = f2.result()

        # Exactly one worker must succeed, one must fail
        assert sorted([res1, res2]) == [False, True]

        # In the database, exactly one row exists
        count = db.session.query(EmailNorm).filter_by(norm=target_norm).count()
        assert count == 1
    destroy_ctfd(app)
