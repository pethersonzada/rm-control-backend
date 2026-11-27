"""Supplier checks and audit, additive migration."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rm_supplier_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("store", sa.String(200), nullable=False),
        sa.Column("supplier_code", sa.String(80), nullable=True),
        sa.Column("supplier_name", sa.String(200), nullable=False),
        sa.Column("checker", sa.String(200), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization", "created_by", "request_key", name="uq_rm_supplier_check_request"),
    )
    op.create_index("ix_rm_supplier_checks_scope", "rm_supplier_checks", ["organization", "store", "checked_at"])
    op.create_table(
        "rm_supplier_check_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("check_id", sa.Integer(), sa.ForeignKey("rm_supplier_checks.id"), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("previous", sa.JSON(), nullable=True),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rm_supplier_check_audit_check", "rm_supplier_check_audit", ["check_id", "id"])


def downgrade():
    op.drop_table("rm_supplier_check_audit")
    op.drop_table("rm_supplier_checks")
