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
        sa.Column("can_delegate_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("role IN ('viewer', 'importer', 'admin')", name="ck_rm_firebase_role"),
        sa.UniqueConstraint("project_id", "uid", name="uq_rm_firebase_uid"),
        sa.UniqueConstraint("organization", "subject", name="uq_rm_firebase_subject"),
    )

    op.create_index("ix_rm_firebase_org", "rm_firebase_memberships", ["project_id", "organization", "role"])
    op.create_table(
        "rm_membership_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("target_uid", sa.String(128), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("previous", sa.JSON(), nullable=True),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rm_membership_audit_org", "rm_membership_audit", ["project_id", "organization", "id"])


def downgrade():
    op.drop_table("rm_membership_audit")
    op.drop_table("rm_firebase_memberships")
