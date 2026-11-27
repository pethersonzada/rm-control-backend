"""Verified package conversion references."""
from alembic import op
import sqlalchemy as sa
from app.models import ExactDecimal

revision="0008"
down_revision="0007"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("rm_unit_references",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("organization",sa.String(80),nullable=False),
        sa.Column("code",sa.String(40),nullable=False),
        sa.Column("package",sa.String(80),nullable=False),
        sa.Column("base_unit",sa.String(80),nullable=False),
        sa.Column("factor",ExactDecimal(),nullable=False),
        sa.Column("reference",sa.String(500),nullable=False),
        sa.Column("active",sa.Boolean(),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),
        sa.UniqueConstraint("organization","code","package",name="uq_rm_unit_reference"))
    op.create_table("rm_unit_reference_audit",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("organization",sa.String(80),nullable=False),
        sa.Column("actor",sa.String(80),nullable=False),
        sa.Column("code",sa.String(40),nullable=False),
        sa.Column("package",sa.String(80),nullable=False),
        sa.Column("previous",sa.JSON(),nullable=True),
        sa.Column("current",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))


def downgrade():
    op.drop_table("rm_unit_reference_audit")
    op.drop_table("rm_unit_references")
