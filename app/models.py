from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, UniqueConstraint, false, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class ExactDecimal(TypeDecorator):
    impl = Numeric(24, 8)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(40) if dialect.name == "sqlite" else self.impl)

    def process_bind_param(self, value, dialect):
        if value is not None and dialect.name == "sqlite":
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        amount = Decimal(value)
        if dialect.name == "sqlite":
            return amount
        text = format(amount, "f")
        return Decimal(text.rstrip("0").rstrip(".") if "." in text else text)


class Base(DeclarativeBase):
    pass


class Occurrence(Base):
    __tablename__ = "rm_occurrences"
    __table_args__ = (
        UniqueConstraint("organization", "store", "code", name="uq_rm_occurrence_scope"),
        Index("ix_rm_occurrence_status", "organization", "store", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization: Mapped[str] = mapped_column(String(80))
    store: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str] = mapped_column(String(200))
    load: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(80))
    included_at: Mapped[str | None] = mapped_column(String(30))
    approved_at: Mapped[str | None] = mapped_column(String(30))
    received_at: Mapped[str | None] = mapped_column(String(30))
    returned_at: Mapped[str | None] = mapped_column(String(30))
    bdv: Mapped[str | None] = mapped_column(String(40))
    is_test: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(20))
    items: Mapped[list["OccurrenceItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="OccurrenceItem.id")
    invoices: Mapped[list["OccurrenceInvoice"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="OccurrenceInvoice.id")


class OccurrenceItem(Base):
    __tablename__ = "rm_occurrence_items"
    __table_args__ = (
        UniqueConstraint("occurrence_id", "code", "package", name="uq_rm_occurrence_item"),
        Index("ix_rm_items_product", "code", "occurrence_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(ForeignKey("rm_occurrences.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal())
    package: Mapped[str] = mapped_column(String(80))
    total: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    unit_value: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    category: Mapped[str | None] = mapped_column(String(200))


class OccurrenceInvoice(Base):
    __tablename__ = "rm_occurrence_invoices"
    __table_args__ = (UniqueConstraint("occurrence_id", "number", "series", name="uq_rm_occurrence_invoice"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(ForeignKey("rm_occurrences.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(String(20))
    series: Mapped[str] = mapped_column(String(10))


class ImportBatch(Base):
    __tablename__ = "rm_import_batches"
    __table_args__ = (UniqueConstraint("organization", "subject", "key", name="uq_rm_import_batch"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    organization: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "rm_audit_events"
    __table_args__ = (Index("ix_rm_audit_scope", "organization", "store", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    organization: Mapped[str] = mapped_column(String(80))
    store: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(40))
    occurrence_id: Mapped[int] = mapped_column(ForeignKey("rm_occurrences.id"))
    previous_status: Mapped[str | None] = mapped_column(String(80))
    next_status: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FirebaseMembership(Base):
    __tablename__ = "rm_firebase_memberships"
    __table_args__ = (
        UniqueConstraint("project_id", "uid", name="uq_rm_firebase_uid"),
        UniqueConstraint("organization", "subject", name="uq_rm_firebase_subject"),
        CheckConstraint("role IN ('viewer', 'importer', 'admin')", name="ck_rm_firebase_role"),
        Index("ix_rm_firebase_org", "project_id", "organization", "role"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100))
    uid: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(80))
    organization: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))
    stores: Mapped[list[str]] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean)


    can_delegate_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class MembershipAudit(Base):
    __tablename__ = "rm_membership_audit"
    __table_args__ = (Index("ix_rm_membership_audit_org", "project_id", "organization", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100))
    organization: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(80))
    target_uid: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(40))
    previous: Mapped[dict | None] = mapped_column(JSON)
    current: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
