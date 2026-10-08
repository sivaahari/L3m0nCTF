"""Create the five l3mon tables (channel, programme, void, bonus, note)

Revision ID: 7a41c0d2b1e3
Revises:
Create Date: 2026-10-09

Run by CTFd's plugin migration runner (CTFd.plugins.migrations.upgrade) when the plugin loads, on MariaDB; on SQLite CTFd uses
create_all from models.py instead. Safe to run again, and safe when two web workers run it at the same moment: each table is
checked first, and if another worker creates it between the check and the create ("table already exists", MariaDB 1050), that
is accepted as long as the table is there afterwards (tests/test_migration_mariadb.py races two workers five times).
models.py must say exactly the same; the same test file compares them against a real MariaDB.

Rolling back is manual, because CTFd's runner only ever goes forward from the revision it has recorded: run `downgrade` of this
file, then delete the setting `l3mon_core_alembic_version` (Configs table); the next start builds the tables again.
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "7a41c0d2b1e3"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("l3mon_channel", "l3mon_programme", "l3mon_void", "l3mon_bonus", "l3mon_note")


def _create(op, name, *parts):
    """Create a table; if another worker made it a moment ago, that is fine as long as it exists now."""
    try:
        op.create_table(name, *parts)
    except (OperationalError, ProgrammingError):
        if name not in get_all_tables(op):
            raise


def upgrade(op=None):
    have = set(get_all_tables(op))

    if "l3mon_channel" not in have:
        _create(
            op,
            "l3mon_channel",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("slug", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("synopsis", sa.String(length=200), nullable=True),
            sa.Column("accent", sa.String(length=32), nullable=True),
            sa.Column("picture_key", sa.String(length=64), nullable=True),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("sponsor_name", sa.String(length=80), nullable=True),
            sa.Column("sponsor_logo", sa.String(length=128), nullable=True),
            sa.Column("release_state", sa.String(length=16), nullable=False),
            sa.Column("release_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("kind in ('standard', 'sponsored')", name="ck_l3mon_channel_kind"),
            sa.CheckConstraint("release_state in ('released', 'withheld', 'scheduled')", name="ck_l3mon_channel_release"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug"),
        )

    if "l3mon_programme" not in have:
        _create(
            op,
            "l3mon_programme",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("channel_id", sa.Integer(), nullable=False),
            sa.Column("cell", sa.Integer(), nullable=False),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("slug", sa.String(length=48), nullable=False),
            sa.Column("release_state", sa.String(length=16), nullable=False),
            sa.Column("release_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("release_state in ('released', 'withheld', 'scheduled')", name="ck_l3mon_programme_release"),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["channel_id"], ["l3mon_channel.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("challenge_id"),
            sa.UniqueConstraint("number"),
            sa.UniqueConstraint("slug"),
            sa.UniqueConstraint("channel_id", "cell", name="uq_l3mon_programme_cell"),
        )

    if "l3mon_void" not in have:
        _create(
            op,
            "l3mon_void",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("submission_id", sa.Integer(), nullable=True),
            sa.Column("solved_at", sa.DateTime(), nullable=True),
            sa.Column("voided_at", sa.DateTime(), nullable=False),
            sa.Column("voided_by", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column("outcome", sa.String(length=16), nullable=False),
            sa.Column("restored_at", sa.DateTime(), nullable=True),
            sa.Column("restored_by", sa.Integer(), nullable=True),
            sa.CheckConstraint("outcome in ('open', 'restored', 'skipped', 'superseded')", name="ck_l3mon_void_outcome"),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["voided_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["restored_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "l3mon_bonus" not in have:
        _create(
            op,
            "l3mon_bonus",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("award_id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("scope", sa.String(length=8), nullable=False),
            sa.Column("message", sa.String(length=200), nullable=False),
            sa.Column("given_by", sa.Integer(), nullable=True),
            sa.Column("given_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint("scope in ('team', 'member')", name="ck_l3mon_bonus_scope"),
            sa.ForeignKeyConstraint(["award_id"], ["awards.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["given_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("award_id"),
        )

    if "l3mon_note" not in have:
        _create(
            op,
            "l3mon_note",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=100), nullable=False),
            sa.Column("text", sa.String(length=500), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade(op=None):
    have = set(get_all_tables(op))
    for table in reversed(TABLES):  # the ones that point at others go first
        if table in have:
            op.drop_table(table)
