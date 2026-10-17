"""create the auth identity table

Revision ID: 2c3d4e5f6a7b
Revises:
Create Date: 2026-10-17 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "2c3d4e5f6a7b"
down_revision = None
branch_labels = None
depends_on = None


def upgrade(op=op):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "l3mon_identity" not in insp.get_table_names():
        op.create_table(
            "l3mon_identity",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "subject", name="uq_l3mon_identity_provider_subject"),
        )
        op.create_index("ix_l3mon_identity_subject", "l3mon_identity", ["subject"], unique=False)
        op.create_index("ix_l3mon_identity_user_id", "l3mon_identity", ["user_id"], unique=False)


def downgrade(op=op):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "l3mon_identity" in insp.get_table_names():
        op.drop_table("l3mon_identity")
