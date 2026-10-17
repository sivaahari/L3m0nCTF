"""Create the guard evidence table (l3mon_evidence)

Revision ID: 1a2b3c4d5e6f
Revises: None
Create Date: 2026-10-17

The evidence table for l3mon_guard (SP5): stores abuse indicators such as flag reuse, bursts,
suspicious shared addresses, failed CAPTCHA verification, and registration anomalies.
Safe to run multiple times and safe under multi-worker concurrency.
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "1a2b3c4d5e6f"
down_revision = None
branch_labels = None
depends_on = None


def upgrade(op=None):
    if "l3mon_evidence" in set(get_all_tables(op)):
        return
    try:
        op.create_table(
            "l3mon_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=True),
            sa.Column("other_team_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("challenge_id", sa.Integer(), nullable=True),
            sa.Column("detail", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("address", sa.String(length=45), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
            sa.Column("reviewer_id", sa.Integer(), nullable=True),
            sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
            sa.CheckConstraint(
                "kind in ('flag_reuse', 'burst', 'shared_address', 'registration_burst', 'captcha_unverified', 'disposable_email_blocked', 'name_policy_blocked')",
                name="ck_l3mon_evidence_kind",
            ),
            sa.CheckConstraint(
                "status in ('new', 'reviewed', 'dismissed', 'actioned')",
                name="ck_l3mon_evidence_status",
            ),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["other_team_id"], ["teams.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_l3mon_evidence_kind", "l3mon_evidence", ["kind"])
        op.create_index("ix_l3mon_evidence_status", "l3mon_evidence", ["status"])
        op.create_index("ix_l3mon_evidence_team_id", "l3mon_evidence", ["team_id"])
        op.create_index("ix_l3mon_evidence_user_id", "l3mon_evidence", ["user_id"])
    except (OperationalError, ProgrammingError):
        if "l3mon_evidence" not in get_all_tables(op):
            raise


def downgrade(op=None):
    if "l3mon_evidence" in set(get_all_tables(op)):
        op.drop_table("l3mon_evidence")
