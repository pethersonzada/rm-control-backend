"""Record completed supplier checks; a repeated delivery is a separate event."""
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import store_key
from app.database import database
from app.models import SupplierCheck, SupplierCheckAudit
from app.repositories.occurrences import transaction
from app.routers.operations import Principal, Writer
from app.routers.tickets import cell
from app.security import require_store
from app.services.memberships import lock_organization

router = APIRouter()
LOCAL = ZoneInfo("America/Recife")


class CheckInput(BaseModel):
    store: str = Field(min_length=1, max_length=200)
    supplier_code: str | None = Field(default=None, max_length=80)
    supplier_name: str = Field(min_length=1, max_length=200)
    checker: str = Field(min_length=1, max_length=200)
    checked_at: AwareDatetime
    reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("supplier_name", "checker", "store")
    @classmethod
    def required_text(cls, value):
        if not value.strip():
            raise ValueError("Campo obrigatório.")
        return value.strip()

    @field_validator("supplier_code", "reference", "notes")
    @classmethod
    def optional_text(cls, value):
        return value.strip() or None if value is not None else None

    @field_validator("checked_at")
    @classmethod
    def completed(cls, value):
        if value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("Registre somente conferências realizadas.")
        return value.astimezone(timezone.utc)


class CheckEdit(CheckInput):
    revision: int = Field(ge=1)
    active: bool = True
    change_reason: str = Field(min_length=1, max_length=500)

    @field_validator("change_reason")
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError("Informe o motivo.")
        return value.strip()


class CheckFilters(BaseModel):
    store: str = Field(default="", max_length=200)
    search: str = Field(default="", max_length=100)
    checker: str = Field(default="", max_length=200)
    from_date: date | None = None
    to_date: date | None = None
    include_cancelled: bool = False


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def snapshot(row):
    return {"id": row.id, "store": row.store, "supplier_code": row.supplier_code,
            "supplier_name": row.supplier_name, "checker": row.checker, "checked_at": utc(row.checked_at).isoformat(),
            "reference": row.reference, "notes": row.notes, "active": row.active,
            "revision": row.revision, "created_by": row.created_by}


def scope(principal):
    return (SupplierCheck.organization == principal.organization, SupplierCheck.store.in_(principal.stores))


def filtered(principal, filters):
    query = select(SupplierCheck).where(*scope(principal))
    if filters.store:
        require_store(principal, filters.store)
        query = query.where(SupplierCheck.store == store_key(filters.store))
    if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
        raise HTTPException(422, "Período inválido.")
    if filters.from_date:
        query = query.where(SupplierCheck.checked_at >= datetime.combine(filters.from_date, time.min, LOCAL).astimezone(timezone.utc))
    if filters.to_date:
        query = query.where(SupplierCheck.checked_at <= datetime.combine(filters.to_date, time.max, LOCAL).astimezone(timezone.utc))
    if not filters.include_cancelled:
        query = query.where(SupplierCheck.active.is_(True))
    if filters.search.strip():
        q = filters.search.strip().lower()
        query = query.where(or_(*(func.lower(field).contains(q, autoescape=True) for field in
                                  (SupplierCheck.supplier_name, SupplierCheck.supplier_code, SupplierCheck.reference))))
    if filters.checker.strip():
        query = query.where(func.lower(SupplierCheck.checker).contains(filters.checker.strip().lower(), autoescape=True))
    return query


@router.get("/supplier-checks")
def list_checks(principal: Principal, filters: Annotated[CheckFilters, Depends()], engine=Depends(database),
                cursor: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    query = filtered(principal, filters)
    with Session(engine) as session:
        counts = dict(session.execute(query.with_only_columns(SupplierCheck.active, func.count()).group_by(SupplierCheck.active)).all())
        if cursor:
            query = query.where(SupplierCheck.id < cursor)
        rows = session.scalars(query.order_by(SupplierCheck.id.desc()).limit(limit+1)).all()
        return {"records": [snapshot(row) for row in rows[:limit]], "next_cursor": rows[limit-1].id if len(rows)>limit else None,
                "completed": counts.get(True, 0), "cancelled": counts.get(False, 0)}


@router.post("/supplier-checks")
def create(data: CheckInput, principal: Writer, engine=Depends(database),
           idempotency_key: Annotated[str, Header(pattern=r"^[a-zA-Z0-9_-]{1,64}$")] = ""):
    if not idempotency_key:
        raise HTTPException(422, "Chave de idempotência obrigatória.")
    require_store(principal, data.store)
    payload = data.model_dump()
    payload["store"] = store_key(data.store)
    digest = sha256(json.dumps({**payload, "checked_at": data.checked_at.isoformat()}, sort_keys=True).encode()).hexdigest()
    with transaction(engine) as session:
        lock_organization(session, "supplier-checks", principal.organization)
        row = session.scalar(select(SupplierCheck).filter_by(organization=principal.organization, created_by=principal.subject, request_key=idempotency_key))
        if row:
            if row.digest != digest:
                raise HTTPException(409, "Chave já utilizada com outros dados.")
            return snapshot(row)
        row = SupplierCheck(organization=principal.organization, created_by=principal.subject,
                            request_key=idempotency_key, digest=digest, active=True, revision=1, **payload)
        session.add(row)
        session.flush()
        current = snapshot(row)
        session.add(SupplierCheckAudit(check_id=row.id, actor=principal.subject, action="create", previous=None, current=current))
        return current


@router.put("/supplier-checks/{check_id}")
def update(check_id: Annotated[int, Path(ge=1)], data: CheckEdit, principal: Writer, engine=Depends(database)):
    require_store(principal, data.store)
    with transaction(engine) as session:
        row = session.scalar(select(SupplierCheck).where(*scope(principal), SupplierCheck.id == check_id).with_for_update())
        if row is None:
            raise HTTPException(404, "Conferência não encontrada.")
        if row.store != store_key(data.store):
            raise HTTPException(422, "A loja de uma conferência não pode ser alterada.")
        if principal.role != "admin" and row.created_by != principal.subject:
            raise HTTPException(403, "Somente quem registrou ou um administrador pode corrigir.")
        if not row.active:
            raise HTTPException(409, "Conferência cancelada; histórico preservado.")
        if row.revision != data.revision:
            raise HTTPException(409, "Conferência alterada. Atualize a tela.")
        previous = snapshot(row)
        for key, value in data.model_dump(exclude={"store","revision","change_reason"}).items():
            setattr(row,key,value)
        row.revision += 1
        current = snapshot(row)
        session.add(SupplierCheckAudit(check_id=row.id, actor=principal.subject, action="edit" if row.active else "cancel",
                                      previous=previous, current=current, reason=data.change_reason))
        return current


@router.get("/supplier-checks/{check_id}/history")
def history(check_id: Annotated[int, Path(ge=1)], principal: Principal, engine=Depends(database),
            cursor: Annotated[int, Query(ge=0)] = 0):
    with Session(engine) as session:
        if session.scalar(select(SupplierCheck.id).where(*scope(principal), SupplierCheck.id == check_id)) is None:
            raise HTTPException(404, "Conferência não encontrada.")
        q = select(SupplierCheckAudit).where(SupplierCheckAudit.check_id == check_id)
        if cursor:
            q = q.where(SupplierCheckAudit.id < cursor)
        rows = session.scalars(q.order_by(SupplierCheckAudit.id.desc()).limit(101)).all()
        return {"events":[{"action":row.action,"actor":row.actor,"reason":row.reason,"previous":row.previous,
                           "current":row.current,"created_at":utc(row.created_at).isoformat()} for row in rows[:100]],
                "next_cursor":rows[99].id if len(rows)>100 else None}


@router.get("/reports/fornecedores.xlsx")
def export(principal: Principal, request: Request, filters: Annotated[CheckFilters, Depends()], engine=Depends(database)):
    query = filtered(principal, filters)
    with request.app.state.limiter.import_slot(workbook=True), Session(engine) as session:
        if engine.dialect.name == "postgresql":
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        count = session.scalar(select(func.count()).select_from(query.subquery()))
        if count > 20000:
            raise HTTPException(422, "Limite de 20 mil conferências. Aplique filtros.")
        book = Workbook(write_only=True)
        sheet = book.create_sheet("Conferências")
        sheet.freeze_panes = "A2"
        for column, width in {"A":14,"B":16,"C":32,"D":28,"E":23,"F":22,"G":40,"H":18,"I":12}.items():
            sheet.column_dimensions[column].width = width
        header = [cell(sheet, title) for title in ["LOJA","CÓDIGO FORNECEDOR","FORNECEDOR","CONFERENTE","DATA/HORA (RECIFE)",
                                                  "REFERÊNCIA","OBSERVAÇÃO","SITUAÇÃO","ID RM"]]
        for item in header:
            item.font = Font(bold=True)
        sheet.append(header)
        totals = {}
        try:
            for row in session.scalars(query.order_by(SupplierCheck.id.desc()).execution_options(yield_per=200)):
                local = utc(row.checked_at).astimezone(LOCAL).replace(tzinfo=None)
                values = [row.store,row.supplier_code,row.supplier_name,row.checker,local,row.reference,row.notes,
                          "CONFERIDO" if row.active else "CANCELADO",row.id]
                sheet.append([cell(sheet,v,"dd/mm/yyyy hh:mm" if i==4 else None) for i,v in enumerate(values)])
                if row.active:
                    key = (row.store,row.checker)
                    totals[key] = totals.get(key,0)+1
            sheet.auto_filter.ref = f"A1:I{count+1}"
            summary = book.create_sheet("Por conferente")
            summary.append(["LOJA","CONFERENTE","CONFERÊNCIAS REALIZADAS"])
            for (store,checker),total in sorted(totals.items()):
                summary.append([cell(summary,store),cell(summary,checker),total])
            buffer = BytesIO()
            book.save(buffer)
            return Response(buffer.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition":'attachment; filename="rm-control-fornecedores.xlsx"'})
        finally:
            if not sheet.closed:
                sheet.close()
            book.close()
