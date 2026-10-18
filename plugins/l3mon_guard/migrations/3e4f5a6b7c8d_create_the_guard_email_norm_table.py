"""Create the guard email norm table (l3mon_email_norm)

Revision ID: 3e4f5a6b7c8d
Revises: 1a2b3c4d5e6f
Create Date: 2026-10-18

Stores normalized email representations indexed for O(1) collision and disguise lookups (SP5 F1).
Safe to run multiple times and safe under multi-worker concurrency.
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "3e4f5a6b7c8d"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade(op=None):
    if "l3mon_email_norm" in set(get_all_tables(op)):
        return
    try:
        op.create_table(
            "l3mon_email_norm",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("norm", sa.String(length=255), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_l3mon_email_norm_user_id"),
            sa.UniqueConstraint("norm", name="uq_l3mon_email_norm_norm"),
        )
        op.create_index("ix_l3mon_email_norm_norm", "l3mon_email_norm", ["norm"])
        op.create_index("ix_l3mon_email_norm_user_id", "l3mon_email_norm", ["user_id"])
    except (OperationalError, ProgrammingError):
        if "l3mon_email_norm" not in get_all_tables(op):
            raise


def downgrade(op=None):
    if "l3mon_email_norm" in set(get_all_tables(op)):
        op.drop_table("l3mon_email_norm")
