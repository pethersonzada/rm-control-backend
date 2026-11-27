from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import String, cast, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import Identity, store_key
from app.models import AuditEvent, ImportBatch, NativeTicket, Occurrence, OccurrenceInvoice, OccurrenceItem
from app.schemas import Record


def scoped(principal: Identity):
    return select(Occurrence).where(
        Occurrence.organization == principal.organization, Occurrence.store.in_(principal.stores),
    )


def page(session: Session, principal: Identity, limit: int, cursor: int, filters, calendar):
    query = scoped(principal)
    if filters.store:
        query = query.where(Occurrence.store == store_key(filters.store))
    if filters.status:
        query = query.where(Occurrence.status == filters.status)
    if filters.reason:
        query = query.where(func.lower(Occurrence.reason) == filters.reason.lower())
    if not filters.include_tests:
        query = query.where(Occurrence.is_test.is_not(True))
    if filters.from_date or filters.to_date:
        column = Occurrence.received_at if filters.date_field == "received" else Occurrence.included_at
        date_key = cast(func.substr(column, 7, 4), String) + "-" + cast(func.substr(column, 4, 2), String) + "-" + cast(func.substr(column, 1, 2), String)
        if filters.from_date:
            query = query.where(date_key >= filters.from_date.isoformat())
        if filters.to_date:
            query = query.where(date_key <= filters.to_date.isoformat())
    if filters.search:
        search = filters.search.lower()
        query = query.where(or_(
            *(func.lower(field).contains(search, autoescape=True) for field in
              (Occurrence.code, Occurrence.load, Occurrence.reason, Occurrence.destination)),
            Occurrence.id.in_(select(NativeTicket.occurrence_id).where(NativeTicket.external_code.contains(search, autoescape=True))),
            Occurrence.items.any(or_(
                OccurrenceItem.code.contains(search, autoescape=True),
                func.lower(OccurrenceItem.description).contains(search, autoescape=True),
            )),
        ))
    if filters.order == "id":
        query = query.where(Occurrence.id > cursor).order_by(Occurrence.id)
    else:
        sort_fields = (func.length(Occurrence.code), Occurrence.code, Occurrence.id)
        ascending = filters.order == "code_asc"
        query = query.order_by(*(field.asc() if ascending else field.desc() for field in sort_fields))
        if cursor:
            cursor_code = session.scalar(scoped(principal).with_only_columns(Occurrence.code).where(Occurrence.id == cursor))
            if cursor_code is None:
                return {"records": [], "next_cursor": None}
            position = (len(cursor_code), cursor_code, cursor)
            comparator = tuple_(*sort_fields)
            query = query.where(comparator > position if ascending else comparator < position)
    results = session.scalars(query.limit(limit + 1)).all()
    records = [serialize(row).model_dump(mode="json") for row in results[:limit]]
    native = {row.occurrence_id: row for row in session.scalars(select(NativeTicket).where(NativeTicket.occurrence_id.in_([row.id for row in results[:limit]])))}
    for row, record in zip(results[:limit], records):
        if row.id in native:
            record.update(external_code=native[row.id].external_code, revision=native[row.id].revision)
    if calendar:
        from app.services.deadlines import deadline
        for record in records:
            record.update(deadline(record, calendar))
    return {
        "records": records,
        "next_cursor": results[limit - 1].id if len(results) > limit else None,
    }


def serialize(row: Occurrence) -> Record:
    return Record.model_validate(row, from_attributes=True)


@contextmanager
def transaction(engine) -> Iterator[Session]:
    with Session(engine) as session, session.begin():
        if engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        yield session


def insert_for(session: Session):
    return sqlite_insert if session.bind.dialect.name == "sqlite" else postgres_insert


def register_batch(session: Session, principal: Identity, key: str, digest: str, count: int):
    scope = {"organization": principal.organization, "subject": principal.subject, "key": key}
    statement = insert_for(session)(ImportBatch).values(**scope, digest=digest, count=count)
    batch_id = session.scalar(statement.on_conflict_do_nothing(index_elements=list(scope)).returning(ImportBatch.id))
    if batch_id is not None:
        return None
    return session.scalar(select(ImportBatch).filter_by(**scope))


def locked(session: Session, principal: Identity, store: str, code: str):
    return session.scalar(scoped(principal).where(Occurrence.store == store, Occurrence.code == code).with_for_update())


def get_or_create(session: Session, principal: Identity, record: Record):
    store = store_key(record.destination)
    data = record.model_dump(exclude={"items", "invoices"})
    statement = insert_for(session)(Occurrence).values(**data, organization=principal.organization, store=store)
    created = session.scalar(statement.on_conflict_do_nothing(
        index_elements=["organization", "store", "code"],
    ).returning(Occurrence.id))
    return locked(session, principal, store, record.code), created is not None


def merge(row: Occurrence, record: Record):
    metadata = record.model_dump(exclude={"items", "invoices", "code", "source"})
    for key, value in metadata.items():
        if value is None or (key in {"origin", "load", "reason"} and not value):
            continue
        if key == "status" and value == "DESCONHECIDO":
            continue
        setattr(row, key, value)
    if record.items:
        row.source = record.source
    previous = {(item.code, item.package): item for item in row.items}
    for item in record.items:
        key = (item.code, item.package)
        current = previous.get(key)
        data = item.model_dump()
        if current is None:
            row.items.append(OccurrenceItem(**data))
            continue
        if current.quantity == item.quantity:
            for field in ("total", "unit_value"):
                if data[field] is None:
                    data[field] = getattr(current, field)
        if item.category is None:
            data["category"] = current.category
        for field, value in data.items():
            setattr(current, field, value)

    invoices = {(item.number, item.series) for item in row.invoices}
    for invoice in record.invoices:
        key = (invoice.number, invoice.series)
        if key not in invoices:
            row.invoices.append(OccurrenceInvoice(**invoice.model_dump()))
            invoices.add(key)


def audit(session: Session, principal: Identity, row: Occurrence, action: str, previous_status: str | None, details=None):
    session.add(AuditEvent(
        organization=principal.organization, store=row.store, subject=principal.subject,
        action=action, occurrence_id=row.id, previous_status=previous_status, next_status=row.status, details=details,
    ))
