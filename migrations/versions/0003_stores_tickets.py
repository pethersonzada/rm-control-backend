"""Stores, access requests and native tickets.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    stores = op.create_table(
        "rm_stores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("code", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("organization", "code", name="uq_rm_store_code"),
    )
    op.create_table(
        "rm_store_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("store_code", sa.String(200), nullable=False),
        sa.Column("previous", sa.JSON(), nullable=True),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "rm_access_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("uid", sa.String(128), nullable=False),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviewer", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "uid", name="uq_rm_access_request_uid"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_rm_access_request_status"),
    )
    op.create_table(
        "rm_native_tickets",
        sa.Column("occurrence_id", sa.Integer(), sa.ForeignKey("rm_occurrences.id"), primary_key=True),
        sa.Column("organization", sa.String(80), nullable=False),
        sa.Column("store", sa.String(200), nullable=False),
        sa.Column("external_code", sa.String(40), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("organization", "store", "external_code", name="uq_rm_native_external"),
    )
    conn = op.get_bind()
    membership = sa.table("rm_firebase_memberships", sa.column("organization", sa.String), sa.column("stores", sa.JSON))
    occurrence = sa.table("rm_occurrences", sa.column("organization", sa.String), sa.column("store", sa.String))
    codes = set(conn.execute(sa.select(occurrence.c.organization, occurrence.c.store).distinct()).all())
    for organization, values in conn.execute(sa.select(membership.c.organization, membership.c.stores)):
        codes.update((organization, code) for code in (values or []) if isinstance(code, str) and code and code != "*")
    if codes:
        conn.execute(stores.insert(), [{"organization": org, "code": code, "name": f"Loja {code}", "active": True} for org, code in sorted(codes)])


def downgrade():
    op.drop_table("rm_native_tickets")
    op.drop_table("rm_access_requests")
    op.drop_table("rm_store_audit")
    op.drop_table("rm_stores")
