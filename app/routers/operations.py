from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Identity
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
    calendar = request.app.state.settings.calendars.get(principal.organization)
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
def summary(principal: Principal, engine: Database):
    return operations.totals(engine, principal)


@router.post("/inventory/reconcile")
def reconcile(body: InventoryRequest, principal: Principal, engine: Database):
    return operations.reconcile(engine, principal, body)
