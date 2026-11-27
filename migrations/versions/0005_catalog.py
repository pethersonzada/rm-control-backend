"""Organization product catalog, with explicitly reviewed reference data.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rm_catalog_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("package", sa.String(80), nullable=True),
        sa.Column("reference", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("organization", "code", name="uq_rm_catalog_product"),
    )
    op.create_table(
        "rm_catalog_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("previous", sa.JSON(), nullable=True),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("rm_catalog_audit")
    op.drop_table("rm_catalog_products")
