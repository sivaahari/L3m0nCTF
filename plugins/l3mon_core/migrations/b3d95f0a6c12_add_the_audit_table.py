"""Add the audit table (l3mon_audit)

Revision ID: b3d95f0a6c12
Revises: 7a41c0d2b1e3
Create Date: 2026-10-09

The audit trail of 3.2: one line for every admin action. Run by CTFd's plugin migration runner when the plugin loads, on MariaDB
(SQLite uses create_all). Like the first revision it is safe to run again and safe when two web workers run it at the same
moment: the table is checked first, and "already exists" from a worker that got there between the check and the create is
accepted as long as the table is there afterwards. models.py must say exactly the same (tests/test_migration_mariadb.py).

Rolling back is manual (the runner only goes forward): run `downgrade` of this file, then set the setting
`l3mon_core_alembic_version` back to the previous revision (7a41c0d2b1e3).
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "b3d95f0a6c12"
down_revision = "7a41c0d2b1e3"
branch_labels = None
depends_on = None


def upgrade(op=None):
    if "l3mon_audit" in set(get_all_tables(op)):
        return
    try:
        op.create_table(
            "l3mon_audit",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("actor_name", sa.String(length=80), nullable=False),
            sa.Column("action", sa.String(length=40), nullable=False),
            sa.Column("target", sa.String(length=100), nullable=False),
            sa.Column("detail", sa.String(length=500), nullable=False),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    except (OperationalError, ProgrammingError):
        if "l3mon_audit" not in get_all_tables(op):
            raise


def downgrade(op=None):
    if "l3mon_audit" in set(get_all_tables(op)):
        op.drop_table("l3mon_audit")
