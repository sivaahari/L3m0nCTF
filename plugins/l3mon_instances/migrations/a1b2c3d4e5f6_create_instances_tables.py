"""Create the l3mon_instance and l3mon_instance_log tables

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("l3mon_instance", "l3mon_instance_log")


def _create(op, name, *parts):
    try:
        op.create_table(name, *parts)
    except (OperationalError, ProgrammingError):
        if name not in get_all_tables(op):
            raise


def upgrade(op=None):
    have = set(get_all_tables(op))

    if "l3mon_instance" not in have:
        _create(
            op,
            "l3mon_instance",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("instance_id", sa.String(length=32), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("slug", sa.String(length=48), nullable=False),
            sa.Column("protocol", sa.String(length=8), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("host_name", sa.String(length=64), nullable=False),
            sa.Column("url", sa.String(length=255), nullable=True),
            sa.Column("host", sa.String(length=128), nullable=True),
            sa.Column("port", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("stopped_at", sa.DateTime(), nullable=True),
            sa.Column("last_renewed_at", sa.DateTime(), nullable=True),
            sa.Column("renewals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active_key", sa.String(length=64), nullable=True),
            sa.CheckConstraint(
                "state in ('starting', 'running', 'stopping', 'expired', 'error')",
                name="ck_l3mon_instance_state",
            ),
            sa.CheckConstraint(
                "protocol in ('http', 'tcp')",
                name="ck_l3mon_instance_protocol",
            ),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("instance_id"),
            sa.UniqueConstraint("active_key"),
        )
        op.create_index("ix_l3mon_instance_instance_id", "l3mon_instance", ["instance_id"])
        op.create_index("ix_l3mon_instance_team_id", "l3mon_instance", ["team_id"])
        op.create_index("ix_l3mon_instance_challenge_id", "l3mon_instance", ["challenge_id"])

    if "l3mon_instance_log" not in have:
        _create(
            op,
            "l3mon_instance_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("team_id", "challenge_id", name="uq_l3mon_instance_log_team_chal"),
        )
        op.create_index("ix_l3mon_instance_log_team_id", "l3mon_instance_log", ["team_id"])
        op.create_index("ix_l3mon_instance_log_challenge_id", "l3mon_instance_log", ["challenge_id"])


def downgrade(op=None):
    for name in reversed(TABLES):
        try:
            op.drop_table(name)
        except Exception:
            pass
