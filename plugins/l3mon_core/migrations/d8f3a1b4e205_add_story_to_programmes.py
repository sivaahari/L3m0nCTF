"""Add the story column to programmes (l3mon_programme.story)

Revision ID: d8f3a1b4e205
Revises: c5e2a7b9d104
Create Date: 2026-11-03

The programme panel shows its "In the story" section with a story line for the programme.
CTFd has no field for it, so it lives on l3mon_programme as a text column of at most 280 characters.
The column is NOT NULL with a server default of an empty string, so adding it does not rewrite
the table and existing rows stay valid with an empty line.

Safe to run again and safe when two web workers run it at the same moment.
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "d8f3a1b4e205"
down_revision = "c5e2a7b9d104"
branch_labels = None
depends_on = None

TABLE = "l3mon_programme"


def _columns(op):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}


def _add(op, name, column):
    if name not in _columns(op):
        try:
            op.add_column(TABLE, column)
        except (OperationalError, ProgrammingError):
            if name not in _columns(op):
                raise


def upgrade(op=None):
    if TABLE not in set(get_all_tables(op)):
        return
    _add(op, "story", sa.Column("story", sa.String(length=280), nullable=False, server_default=""))


def downgrade(op=None):
    if TABLE not in set(get_all_tables(op)):
        return
    if "story" in _columns(op):
        op.drop_column(TABLE, "story")
