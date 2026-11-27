"""Reviewed SLA and explicit financial records; no inferred amounts."""
from alembic import op
import sqlalchemy as sa
from app.models import ExactDecimal

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rm_operational_policies",
        sa.Column("organization",sa.String(80),primary_key=True),
        sa.Column("enabled",sa.Boolean(),nullable=False),
        sa.Column("days",sa.Integer(),nullable=False),
        sa.Column("start_field",sa.String(30),nullable=False),
        sa.Column("timezone",sa.String(80),nullable=False),
        sa.Column("holidays",sa.JSON(),nullable=False),
        sa.Column("reference",sa.String(500),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False))
    op.create_table("rm_operational_policy_audit",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("organization",sa.String(80),nullable=False),
        sa.Column("actor",sa.String(80),nullable=False),
        sa.Column("previous",sa.JSON(),nullable=True),
        sa.Column("current",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_table("rm_financial_reviews",
        sa.Column("occurrence_id",sa.Integer(),sa.ForeignKey("rm_occurrences.id"),primary_key=True),
        sa.Column("approved",ExactDecimal(),nullable=True),
        sa.Column("recovered",ExactDecimal(),nullable=True),
        sa.Column("pending",ExactDecimal(),nullable=True),
        sa.Column("reference",sa.String(500),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False))


def downgrade():
    op.drop_table("rm_financial_reviews")
    op.drop_table("rm_operational_policy_audit")
    op.drop_table("rm_operational_policies")
