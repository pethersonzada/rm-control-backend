"""Native ticket creation/update and a bounded, filtered XLSX export."""
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import hashlib
import json
import uuid
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import database
from app.models import AuditEvent, NativeTicket, Occurrence, OccurrenceInvoice, OccurrenceItem
from app.repositories import occurrences
from app.routers.operations import Filters, Principal, Writer
from app.schemas import Item, Invoice, Record
from app.security import require_store
from app.services.memberships import lock_organization
from app.config import store_key

router = APIRouter()


class TicketInput(BaseModel):
    destination: str = Field(min_length=1, max_length=200)
    origin: str = Field(default="", max_length=200)
    load: str = Field(default="", max_length=80)
    bdv: str | None = Field(default=None, max_length=40)
    external_code: str | None = Field(default=None, pattern=r"^[0-9]{1,40}$")
    reason: str = Field(min_length=1, max_length=200)
    received_at: date
    items: list[Item] = Field(min_length=1, max_length=500)
    invoices: list[Invoice] = Field(default_factory=list, max_length=100)


class TicketEdit(TicketInput):
    revision: int = Field(ge=1)


def checked(data):
    if not data.reason.strip() or any(not i.description.strip() or not i.package.strip() for i in data.items):
        raise HTTPException(422, "Informe motivo, descrição e embalagem dos itens.")
    if len({(i.code, i.package) for i in data.items}) != len(data.items):
        raise HTTPException(422, "PLU e embalagem repetidos.")
    if len({(i.number, i.series) for i in data.invoices}) != len(data.invoices):
        raise HTTPException(422, "Nota fiscal repetida.")


def external_conflict(session, principal, store, external, exclude=None):
    if external is None:
        return
    query = select(NativeTicket.occurrence_id).where(
        NativeTicket.organization == principal.organization, NativeTicket.store == store,
        NativeTicket.external_code == external,
    )
    imported = occurrences.scoped(principal).where(Occurrence.store == store, Occurrence.code == external)
    if exclude is not None:
        query = query.where(NativeTicket.occurrence_id != exclude)
        imported = imported.where(Occurrence.id != exclude)
    if session.scalar(query.limit(1)) is not None or session.scalar(imported.limit(1)) is not None:
        raise HTTPException(409, "Número externo já registrado nesta loja.")


def native_record(row, meta):
    return {**occurrences.serialize(row).model_dump(mode="json"), "external_code": meta.external_code,
            "revision": meta.revision}


def make_record(data, code, status, included):
    return Record(
        code=code, destination=data.destination, origin=data.origin, load=data.load, bdv=data.bdv,
        reason=data.reason.strip().upper(), received_at=data.received_at.strftime("%d/%m/%Y"),
        included_at=included, status=status, items=data.items, invoices=data.invoices, source="native",
    )


@router.post("/tickets")
def create_ticket(data: TicketInput, principal: Writer, engine=Depends(database),
                  idempotency_key: Annotated[str, Header(pattern=r"^[a-zA-Z0-9_-]{1,48}$")] = ""):
    if not idempotency_key:
        raise HTTPException(422, "Chave de idempotência obrigatória.")
    checked(data)
    require_store(principal, data.destination)
    store = store_key(data.destination)
    digest = hashlib.sha256(json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    # Stable numeric identifier derived from the authenticated request key, not an official external number.
    code = "9" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{principal.organization}/{principal.subject}/{idempotency_key}").int).zfill(39)
    with occurrences.transaction(engine) as session:
        lock_organization(session, "tickets", principal.organization)
        existing = occurrences.register_batch(session, principal, "ticket_" + idempotency_key, digest, 1)
        if existing:
            if existing.digest != digest:
                raise HTTPException(409, "Chave de abertura já utilizada com outros dados.")
            row = occurrences.locked(session, principal, store, code)
            if row is None:
                raise HTTPException(409, "Registro não encontrado para esta tentativa.")
            meta = session.get(NativeTicket, row.id)
            if not meta:
                raise HTTPException(409, "Identificador em conflito.")
            return native_record(row, meta)
        external_conflict(session, principal, store, data.external_code)
        record = make_record(data, code, "PENDENTE", datetime.now(ZoneInfo("America/Recife")).strftime("%d/%m/%Y %H:%M:%S"))
        row, created = occurrences.get_or_create(session, principal, record)
        if not created:
            raise HTTPException(409, "Identificador em conflito.")
        occurrences.merge(row, record)
        meta = NativeTicket(occurrence_id=row.id, organization=principal.organization, store=store,
                            external_code=data.external_code, revision=1)
        session.add(meta)
        occurrences.audit(session, principal, row, "ticket.create", None, {"revision": 1, "fields": ["creation"], "items": len(data.items), "invoices": len(data.invoices)})
        return native_record(row, meta)


@router.put("/tickets/{code}")
def edit_ticket(code: str, data: TicketEdit, principal: Writer, engine=Depends(database)):
    checked(data)
    require_store(principal, data.destination)
    store = store_key(data.destination)
    with occurrences.transaction(engine) as session:
        lock_organization(session, "tickets", principal.organization)
        row = occurrences.locked(session, principal, store, code)
        meta = session.get(NativeTicket, row.id) if row else None
        if not meta:
            raise HTTPException(404, "Chamado nativo não encontrado.")
        if meta.revision != data.revision:
            raise HTTPException(409, "Chamado alterado. Atualize a tela.")
        external_conflict(session, principal, store, data.external_code, row.id)
        previous = occurrences.serialize(row).model_dump(mode="json")
        previous_external = meta.external_code
        record = make_record(data, code, row.status, row.included_at)
        # Keep the current status and lifecycle dates; edits replace only the explicit form fields.
        row.origin, row.load, row.bdv, row.reason, row.received_at = record.origin, record.load, record.bdv, record.reason, record.received_at
        row.items.clear()
        row.invoices.clear()
        session.flush()
        row.items.extend(OccurrenceItem(**item.model_dump()) for item in record.items)
        row.invoices.extend(OccurrenceInvoice(**invoice.model_dump()) for invoice in record.invoices)
        meta.external_code, meta.revision = data.external_code, meta.revision + 1
        current = occurrences.serialize(row).model_dump(mode="json")
        changed = [field for field in current if previous[field] != current[field]]
        if previous_external != meta.external_code:
            changed.append("external_code")
        occurrences.audit(session, principal, row, "ticket.edit", row.status, {"revision": meta.revision, "fields": changed, "items": len(row.items), "invoices": len(row.invoices)})
        return native_record(row, meta)


def cell(sheet, value, number_format=None):
    # Excel only retains 15 significant digits. Preserve longer decimals as text.
    if isinstance(value, Decimal):
        value = value if len(value.normalize().as_tuple().digits) <= 15 else format(value, "f")
    item = WriteOnlyCell(sheet, value=value)
    if isinstance(value, str):
        item.data_type = "s"
    if number_format:
        item.number_format = number_format
    return item


def report_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.split()[0], "%d/%m/%Y").date()
    except ValueError:
        return value


@router.get("/reports/chamados.xlsx")
def export_workbook(principal: Principal, request: Request, filters: Annotated[Filters, Depends()], engine=Depends(database)):
    if filters.store:
        require_store(principal, filters.store)
    if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
        raise HTTPException(422, "Período inválido.")
    with request.app.state.limiter.import_slot(workbook=True):
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Chamados")
        sheet.freeze_panes = "A2"
        for letter, width in {"A": 17, "B": 25, "C": 18, "D": 16, "E": 40, "F": 22, "G": 23, "H": 18, "I": 20, "J": 23, "K": 23, "L": 25, "M": 20, "N": 25, "O": 44, "P": 30, "Q": 23}.items():
            sheet.column_dimensions[letter].width = width
        headers = ["DATA RECEBIMENTO", "LOJA", "N° BDV", "PLU", "DESCRIÇÃO PRODUTO", "CATEGORIA",
                   "TIPO DA DIVERGÊNCIA", "VOLUME", "QTD DIVERGENTE", "VALOR DA DIVERGÊNCIA",
                   "N° CHAMADO", "SITUAÇÃO DO CHAMADO", "DATA RETORNO CD", "CD ORIGEM",
                   "ID RM CONTROL", "NOTAS FISCAIS", "DATA INCLUSÃO"]
        first = [cell(sheet, title) for title in headers]
        for item in first:
            item.font = Font(bold=True)
        sheet.append(first)
        cursor, count = 0, 0
        try:
            with Session(engine) as session:
                if engine.dialect.name == "postgresql":
                    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                while cursor is not None:
                    page = occurrences.page(session, principal, 20, cursor, filters, None)
                    for record in page["records"]:
                        notes = "; ".join(f"{n['number']}/{n['series']}" for n in record["invoices"])
                        for item in record["items"] or [{}]:
                            count += 1
                            if count > 20000:
                                raise HTTPException(422, "Exportação limitada a 20 mil linhas. Aplique filtros.")
                            row = [report_date(record["received_at"]), record["destination"], record["bdv"], item.get("code"),
                                   item.get("description"), item.get("category"), record["reason"], item.get("package"),
                                   Decimal(item["quantity"]) if item.get("quantity") is not None else None,
                                   Decimal(item["total"]) if item.get("total") is not None else None,
                                   record.get("external_code") if record["source"] == "native" else record["code"],
                                   record["status"], report_date(record["returned_at"]), record["origin"], record["code"], notes,
                                   record["included_at"]]
                            sheet.append([cell(sheet, value, {0: "dd/mm/yyyy", 8: "0.########", 9: '"R$" #,##0.00######', 12: "dd/mm/yyyy"}.get(i)) for i, value in enumerate(row)])
                    cursor = page["next_cursor"]
            sheet.auto_filter.ref = f"A1:Q{count + 1}"
            output = BytesIO()
            workbook.save(output)
            return Response(output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": 'attachment; filename="rm-control-chamados.xlsx"'})
        finally:
            if not sheet.closed:
                sheet.close()
            workbook.close()
