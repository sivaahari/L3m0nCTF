"""MariaDB migration verification for l3mon_guard (SP5 B4.7, B6).

Verifies on a real MariaDB instance (via tools/run-migration-test.sh):
1. Migration 1a2b3c4d5e6f builds the schema described by Evidence in models.py.
2. Check constraints on kind and status are enforced by MariaDB itself.
3. Foreign keys with ON DELETE SET NULL behave as expected.
4. Migration is idempotent (can run repeatedly) and supports downgrade/re-upgrade.
"""
import importlib.util
import os
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError

from CTFd.models import Challenges, Teams, Users, db
from CTFd.plugins.l3mon_guard import models
from CTFd.plugins.l3mon_guard.models import Evidence
from CTFd.plugins.migrations import current, upgrade
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_team, gen_user

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

REVISION = "1a2b3c4d5e6f"


def _migration_module():
    here = os.path.dirname(os.path.abspath(models.__file__))
    path = os.path.join(here, "migrations", "1a2b3c4d5e6f_create_the_guard_evidence_table.py")
    spec = importlib.util.spec_from_file_location("l3mon_guard_migration", path)
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
        assert current("l3mon_guard") == REVISION
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
