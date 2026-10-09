"""Add the difficulty and the delivery of a programme (l3mon_programme.difficulty, .delivery)

Revision ID: c5e2a7b9d104
Revises: b3d95f0a6c12
Create Date: 2026-10-09

The board's tiles and filters show a programme's difficulty, and a live programme (one with an instance) is told apart from a
static one by its delivery. CTFd has no field for either, so they live here, with the same words the author kit uses and a
CHECK that refuses any other. Both columns are NOT NULL with a server default (`medium`, `static_shared`), so a row made before
this revision, or by a plan that leaves them out, is valid: adding such a column does not rewrite the table.

Run by CTFd's plugin migration runner when the plugin loads, on MariaDB (SQLite uses create_all). Safe to run again and safe when
two web workers run it at the same moment: the columns are looked up first, and "duplicate column" from a worker that got there
between the look and the change is accepted as long as the column is there afterwards. models.py must say exactly the same
(tests/test_migration_mariadb.py compares them against a real MariaDB).

Rolling back is manual (the runner only goes forward): run `downgrade` of this file, then set the setting
`l3mon_core_alembic_version` back to the previous revision (b3d95f0a6c12).
"""
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError

from CTFd.plugins.migrations import get_all_tables

revision = "c5e2a7b9d104"
down_revision = "b3d95f0a6c12"
branch_labels = None
depends_on = None

TABLE = "l3mon_programme"
DIFFICULTY_CHECK = "ck_l3mon_programme_difficulty"
DELIVERY_CHECK = "ck_l3mon_programme_delivery"
DIFFICULTIES = "'warmup', 'easy', 'medium', 'hard', 'insane'"
DELIVERIES = "'static_per_participant', 'static_shared', 'live_single', 'live_multi'"


def _columns(op):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}


def _checks(op):
    return {check["name"] for check in sa.inspect(op.get_bind()).get_check_constraints(TABLE)}


def _add(op, name, column, check_name, check_sql):
    if name in _columns(op):
        return
    try:
        op.add_column(TABLE, column)
    except (OperationalError, ProgrammingError):
        if name not in _columns(op):
            raise
    if check_name not in _checks(op):
        try:
            op.create_check_constraint(check_name, TABLE, check_sql)
        except (OperationalError, ProgrammingError):
            if check_name not in _checks(op):
                raise


def upgrade(op=None):
    if TABLE not in set(get_all_tables(op)):
        return  # the first revision has not made the table (it always runs before this one)
    _add(op, "difficulty", sa.Column("difficulty", sa.String(length=16), nullable=False, server_default="medium"), DIFFICULTY_CHECK, f"difficulty in ({DIFFICULTIES})")
    _add(op, "delivery", sa.Column("delivery", sa.String(length=24), nullable=False, server_default="static_shared"), DELIVERY_CHECK, f"delivery in ({DELIVERIES})")


def downgrade(op=None):
    if TABLE not in set(get_all_tables(op)):
        return
    for name, check_name in (("delivery", DELIVERY_CHECK), ("difficulty", DIFFICULTY_CHECK)):
        if check_name in _checks(op):
            op.drop_constraint(check_name, TABLE, type_="check")
        if name in _columns(op):
            op.drop_column(TABLE, name)
