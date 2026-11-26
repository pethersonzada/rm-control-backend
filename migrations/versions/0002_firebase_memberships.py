"""Persistent Firebase membership allowlist.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rm_firebase_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("uid", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(80), nullable=False),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("stores", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("project_id", "uid", name="uq_rm_firebase_uid"),
        sa.UniqueConstraint("organization", "subject", name="uq_rm_firebase_subject"),
    )


def downgrade():
    op.drop_table("rm_firebase_memberships")
