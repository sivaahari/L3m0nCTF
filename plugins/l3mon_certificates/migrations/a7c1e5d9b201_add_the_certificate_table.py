"""Add the certificate table (l3mon_certificate)

Revision ID: a7c1e5d9b201
Revises:
Create Date: 2026-11-27

One row per player: the verification code of their qualifier certificate (plugins/l3mon_certificates). Loaded by the crew's import command, shown on the
player's own studio page once the crew releases the certificates.

Run by CTFd's plugin migration runner when the plugin loads, on MariaDB (SQLite uses create_all). Like the core plugin's revisions it is safe to run again and
safe when two web workers run it at the same moment: the table is checked first, and "already exists" from a worker that got there between the check and the
create is accepted as long as the table is there afterwards. models.py must say exactly the same (tests/test_certificates_mariadb.py).

Rolling back is manual (the runner only goes forward): run `downgrade` of this file, then delete the setting `l3mon_certificates_alembic_version`.
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "a7c1e5d9b201"
down_revision = None
branch_labels = None
depends_on = None

TABLE = "l3mon_certificate"


def upgrade(op=None):
    if TABLE in set(get_all_tables(op)):
        return
    try:
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("username", sa.String(length=128), nullable=False),
            sa.Column("imported_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_l3mon_certificate_user"),
            sa.UniqueConstraint("code", name="uq_l3mon_certificate_code"),
        )
    except (OperationalError, ProgrammingError):
        if TABLE not in get_all_tables(op):
            raise


def downgrade(op=None):
    if TABLE in set(get_all_tables(op)):
        op.drop_table(TABLE)
