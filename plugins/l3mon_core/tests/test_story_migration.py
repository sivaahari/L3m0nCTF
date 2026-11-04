"""Tests for migration d8f3a1b4e205 (adding story column to l3mon_programme).

Verifies upgrade/downgrade idempotence and schema preservation.
"""
import importlib.util
import os
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from CTFd.models import db
from CTFd.plugins.l3mon_core import models
from tests.helpers import create_ctfd, destroy_ctfd


def _load_migration():
    here = os.path.dirname(os.path.abspath(models.__file__))
    path = os.path.join(here, "migrations", "d8f3a1b4e205_add_story_to_programmes.py")
    spec = importlib.util.spec_from_file_location("migration_d8f3", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_story_migration_metadata():
    mod = _load_migration()
    assert mod.revision == "d8f3a1b4e205"
    assert mod.down_revision == "c5e2a7b9d104"
    assert mod.TABLE == "l3mon_programme"


def test_story_migration_idempotent_upgrade_and_downgrade():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        mod = _load_migration()

        # Table exists and has story column from create_all
        cols = {c["name"] for c in inspect(db.engine).get_columns("l3mon_programme")}
        assert "story" in cols

        # Upgrade is idempotent when column already exists
        with db.engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            mod.upgrade(op=op)
            mod.upgrade(op=op)

        cols_after = {c["name"] for c in inspect(db.engine).get_columns("l3mon_programme")}
        assert "story" in cols_after

        # Downgrade drops the column
        with db.engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            mod.downgrade(op=op)

        cols_down = {c["name"] for c in inspect(db.engine).get_columns("l3mon_programme")}
        assert "story" not in cols_down

        # Second downgrade is harmless
        with db.engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            mod.downgrade(op=op)

        # Upgrade re-adds the column
        with db.engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            mod.upgrade(op=op)

        cols_up = {c["name"] for c in inspect(db.engine).get_columns("l3mon_programme")}
        assert "story" in cols_up
    destroy_ctfd(app)
