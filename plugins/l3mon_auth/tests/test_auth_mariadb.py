"""MariaDB migration verification for l3mon_auth (SP8 B5, B6).

Verifies on a real MariaDB instance (via tools/run-migration-test.sh):
1. Migration 2c3d4e5f6a7b builds the schema described by Identity in models.py.
2. Unique constraint on (provider, subject) is enforced by MariaDB itself.
3. Foreign key on delete CASCADE removes identity rows when user is deleted.
4. Migration is idempotent (runs repeatedly) and supports downgrade/re-upgrade.
"""
import importlib.util
import os
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError

from CTFd.models import Users, db
from CTFd.plugins.l3mon_auth import models
from CTFd.plugins.l3mon_auth.models import Identity
from CTFd.plugins.migrations import current, upgrade
from tests.helpers import create_ctfd, destroy_ctfd, gen_user

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

REVISION = "2c3d4e5f6a7b"


def _migration_module():
    here = os.path.dirname(os.path.abspath(models.__file__))
    path = os.path.join(here, "migrations", "2c3d4e5f6a7b_create_the_auth_identity_table.py")
    spec = importlib.util.spec_from_file_location("l3mon_auth_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_on_connection(fn):
    with db.engine.begin() as conn:
        fn(Operations(MigrationContext.configure(conn)))


def tables():
    return set(inspect(db.engine).get_table_names())


def test_auth_table_created_and_head_recorded():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert "l3mon_identity" in tables()
        assert current("l3mon_auth") == REVISION
    destroy_ctfd(app)


def test_auth_table_schema_matches_model():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        insp = inspect(db.engine)
        real_cols = {c["name"]: c for c in insp.get_columns("l3mon_identity")}
        want_cols = {c.name: c for c in Identity.__table__.columns}

        assert set(real_cols) == set(want_cols)
        for col, spec in want_cols.items():
            assert bool(real_cols[col]["nullable"]) == bool(spec.nullable), f"{col} nullable"
            assert real_cols[col]["type"].python_type == spec.type.python_type, f"{col} python_type"
            length = getattr(spec.type, "length", None)
            if length:
                assert real_cols[col]["type"].length == length, f"{col} length"

        real_fks = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
            for fk in insp.get_foreign_keys("l3mon_identity")
        }
        want_fks = {
            (tuple(fk.parent.name for fk in c.elements), c.elements[0].column.table.name, c.ondelete)
            for c in Identity.__table__.foreign_key_constraints
        }
        assert real_fks == want_fks

        real_unique = {tuple(u["column_names"]) for u in insp.get_unique_constraints("l3mon_identity")}
        want_unique = {
            tuple(c.name for c in u.columns)
            for u in Identity.__table__.constraints
            if u.__class__.__name__ == "UniqueConstraint"
        }
        assert want_unique <= real_unique
    destroy_ctfd(app)


def test_auth_mariadb_enforces_unique_subject_per_provider():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        u1 = gen_user(db, name="User One", email="one@example.com")
        u2 = gen_user(db, name="User Two", email="two@example.com")

        i1 = Identity(provider="google", subject="same-sub-123", user_id=u1.id)
        db.session.add(i1)
        db.session.commit()

        # Second identity with same (provider, subject) must fail
        i2 = Identity(provider="google", subject="same-sub-123", user_id=u2.id)
        db.session.add(i2)
        with pytest.raises(DBAPIError):
            db.session.commit()
        db.session.rollback()
    destroy_ctfd(app)


def test_auth_foreign_key_cascade_on_delete():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        user = gen_user(db, name="Cascade User", email="cascade@example.com")
        ident = Identity(provider="google", subject="cascade-sub-456", user_id=user.id)
        db.session.add(ident)
        db.session.commit()
        ident_id = ident.id

        # Delete user
        db.session.delete(user)
        db.session.commit()

        # Identity is deleted by foreign key CASCADE
        assert Identity.query.filter_by(id=ident_id).first() is None
    destroy_ctfd(app)


def test_auth_migration_idempotent_and_downgrade():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        mod = _migration_module()

        # Running upgrade again changes nothing and does not error
        run_on_connection(lambda op: mod.upgrade(op=op))
        assert "l3mon_identity" in tables()

        # Downgrade drops l3mon_identity
        run_on_connection(lambda op: mod.downgrade(op=op))
        assert "l3mon_identity" not in tables()

        # Second downgrade is safe
        run_on_connection(lambda op: mod.downgrade(op=op))
        assert "l3mon_identity" not in tables()

        # Re-upgrade brings the table back
        run_on_connection(lambda op: mod.upgrade(op=op))
        assert "l3mon_identity" in tables()
    destroy_ctfd(app)
