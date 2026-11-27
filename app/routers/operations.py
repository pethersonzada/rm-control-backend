from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import AuditEvent, Occurrence

from app.config import Identity, store_key
from app.database import database
from app.repositories import occurrences
from app.schemas import InventoryRequest, StatusUpdate
from app.security import current_identity, require_store, writer
from app.services import operations

router = APIRouter()
Principal = Annotated[Identity, Depends(current_identity)]
Writer = Annotated[Identity, Depends(writer)]
Database = Annotated[object, Depends(database)]


class Filters(BaseModel):
    search: str = Field(default="", max_length=100)
    status: str = Field(default="", max_length=80)
    store: str = Field(default="", max_length=200)
    reason: str = Field(default="", max_length=200)
    date_field: Literal["received", "included"] = "included"
    from_date: date | None = None
    to_date: date | None = None
    include_tests: bool = False
    order: Literal["id", "code_asc", "code_desc"] = "id"


@router.get("/occurrences")
def list_occurrences(
    principal: Principal, engine: Database, request: Request,
    filters: Annotated[Filters, Depends()],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: Annotated[int, Query(ge=0)] = 0,
):
    if filters.store:
        require_store(principal, filters.store)
    if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
        raise HTTPException(422, "A data inicial precisa ser anterior ou igual à final.")
    from app.routers.policies import effective_calendar
    calendar = effective_calendar(engine, principal.organization, request.app.state.settings.calendars.get(principal.organization))
    with Session(engine) as session:
        return occurrences.page(session, principal, limit, cursor, filters, calendar)


@router.get("/me")
def me(principal: Principal):
    return {"subject": principal.subject, "organization": principal.organization,
            "role": principal.role, "stores": sorted(principal.stores),
            "can_delegate_admin": principal.can_delegate_admin}


@router.patch("/occurrences/{code}/status")
def change_status(
    code: Annotated[str, Path(pattern=r"^[0-9]{1,40}$")],
    store: Annotated[str, Query(min_length=1, max_length=200)],
    body: StatusUpdate, principal: Writer, engine: Database,
):
    return operations.change_status(engine, principal, store, code, body)


@router.get("/summary")
def summary(principal: Principal, engine: Database, request: Request):
    from app.routers.policies import effective_calendar
    return operations.totals(engine, principal, effective_calendar(engine, principal.organization, request.app.state.settings.calendars.get(principal.organization)))


@router.post("/inventory/reconcile")
def reconcile(body: InventoryRequest, principal: Principal, engine: Database):
    return operations.reconcile(engine, principal, body)


@router.get("/occurrences/{code}/history")
def history(
    code: Annotated[str, Path(pattern=r"^[0-9]{1,40}$")],
    store: Annotated[str, Query(min_length=1, max_length=200)],
    principal: Principal, engine: Database,
    cursor: Annotated[int, Query(ge=0)] = 0,
):
    require_store(principal, store)
    with Session(engine) as session:
        row = session.scalar(occurrences.scoped(principal).where(
            Occurrence.store == store_key(store), Occurrence.code == code,
        ))
        if row is None:
            raise HTTPException(404, "Chamado não encontrado.")
        query = select(AuditEvent).where(
            AuditEvent.organization == principal.organization,
            AuditEvent.store == row.store, AuditEvent.occurrence_id == row.id,
        )
        if cursor:
            query = query.where(AuditEvent.id < cursor)
        events = session.scalars(query.order_by(AuditEvent.id.desc()).limit(101)).all()
        return {"events": [{"action": e.action, "subject": e.subject, "previous_status": e.previous_status,
                            "next_status": e.next_status, "created_at": e.created_at.isoformat(),
                            "details": e.details} for e in events[:100]],
                "next_cursor": events[99].id if len(events) > 100 else None}
