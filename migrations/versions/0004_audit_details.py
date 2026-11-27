"""Optional audit details for native ticket edits.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rm_audit_events", sa.Column("details", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("rm_audit_events", "details")
