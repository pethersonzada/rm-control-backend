import os
import secrets
from fastapi import Depends, Header
from decimal import Decimal
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from app.parser import parse

class Base(DeclarativeBase):
    pass

class Occurrence(Base):
    __tablename__ = "occurrences"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./rm-control.db"))
Base.metadata.create_all(engine)
app = FastAPI(title="RM Control API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","), allow_methods=["GET","POST"], allow_headers=["Content-Type","X-RM-Token"])

def authorize(x_rm_token: str | None = Header(default=None)):
    expected = os.getenv("RM_ACCESS_TOKEN")
    if expected and not secrets.compare_digest(expected.encode(), (x_rm_token or "").encode()):
        raise HTTPException(401, "Token de acesso inválido.")

class Item(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    description: str
    quantity: Decimal = Field(ge=0)
    package: str
    total: Decimal | None = Field(default=None, ge=0)
    unit_value: Decimal | None = Field(default=None, ge=0)
    category: str | None = None

class Invoice(BaseModel):
    number: str = Field(pattern=r"^\d+$", max_length=20)
    series: str = Field(pattern=r"^\d+$", max_length=10)

class Record(BaseModel):
    code: str = Field(pattern=r"^\d+$", max_length=40)
    origin: str
    destination: str
    load: str
    reason: str
    included_at: str | None = None
    status: str
    approved_at: str | None = None
    received_at: str | None = None
    returned_at: str | None = None
    bdv: str | None = None
    is_test: bool | None = None
    invoices: list[Invoice] = Field(default_factory=list)
    source: str = "clipboard"
    items: list[Item] = Field(default_factory=list, max_length=10000)

class Paste(BaseModel):
    text: str = Field(min_length=1, max_length=2000000)
    target_code: str | None = None

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/imports/preview", dependencies=[Depends(authorize)])
def preview(body: Paste):
    return parse(body.text, body.target_code)

@app.get("/occurrences", dependencies=[Depends(authorize)])
def occurrences():
    with Session(engine) as session:
        return [Record.model_validate_json(row.payload) for row in session.scalars(select(Occurrence).order_by(Occurrence.code)).all()]

@app.post("/imports/confirm", dependencies=[Depends(authorize)])
def confirm(records: list[Record]):
    if not records or len(records) > 10000 or len({r.code for r in records}) != len(records):
        raise HTTPException(422, "Lote vazio, excessivo ou com chamados duplicados.")
    with Session(engine) as session, session.begin():
        for record in records:
            keys = [(i.code, i.package) for i in record.items]
            if len(keys) != len(set(keys)):
                raise HTTPException(422, f"Itens duplicados no chamado {record.code}.")
            old = session.get(Occurrence, record.code)
            if old:
                previous = Record.model_validate_json(old.payload)
                if previous.source != record.source and previous.items and record.items:
                    raise HTTPException(409, f"Chamado {record.code}: origem de importação diferente; conciliação manual necessária.")
                merged = {(i.code, i.package): i for i in previous.items}
                for item in record.items:
                    key = (item.code, item.package)
                    if item.total is None and key in merged and item.quantity == merged[key].quantity:
                        item.total = merged[key].total
                        item.unit_value = merged[key].unit_value
                    if key in merged and item.category is None:
                        item.category = merged[key].category
                    merged[key] = item
                record.items = list(merged.values())
                if record.status == "DESCONHECIDO":
                    record.status = previous.status
                for field in ("bdv", "received_at", "returned_at", "included_at", "approved_at", "is_test"):
                    if getattr(record, field) is None:
                        setattr(record, field, getattr(previous, field))
                record.invoices = list({(i.number, i.series): i for i in previous.invoices + record.invoices}.values())
                if not record.reason:
                    record.reason = previous.reason
                old.payload = record.model_dump_json()
            else:
                session.add(Occurrence(code=record.code, payload=record.model_dump_json()))
    return {"saved": len(records)}


@app.get("/summary", dependencies=[Depends(authorize)])
def summary():
    records = [r for r in occurrences() if not r.is_test]
    totals = {"FALTA": Decimal(0), "SOBRA": Decimal(0), "AVARIA": Decimal(0)}
    missing = 0
    for record in records:
        for item in record.items:
            if item.total is None:
                missing += 1
            elif record.reason.upper() in totals:
                totals[record.reason.upper()] += item.total
    return {
        "occurrences": len(records),
        "items_without_value": missing,
        "totals_by_reason": {k: str(v) for k, v in totals.items()},
        "net_reported": str(totals["SOBRA"] - totals["FALTA"] - totals["AVARIA"]),
        "note": "Valores informados, não necessariamente aprovados ou recuperados."
    }

class Spreadsheet(BaseModel):
    content_base64: str = Field(min_length=1, max_length=24000000)
    sheet: str | None = None
    skip_invalid: bool = False

@app.post("/imports/spreadsheet", dependencies=[Depends(authorize)])
def spreadsheet(body: Spreadsheet):
    from app.spreadsheet import parse_workbook
    try:
        return parse_workbook(body.content_base64, body.sheet, body.skip_invalid)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


class InventoryItem(BaseModel):
    code: str = Field(pattern=r"^\d+$", max_length=40)
    quantity: Decimal
    value: Decimal | None = None

class InventoryRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=200)
    items: list[InventoryItem] = Field(min_length=1, max_length=10000)

@app.post("/inventory/reconcile", dependencies=[Depends(authorize)])
def reconcile(body: InventoryRequest):
    if len({i.code for i in body.items}) != len(body.items):
        raise HTTPException(422, "PLUs repetidos no inventário; consolide os registros antes da análise.")
    pending = {"AGU. ANALISE", "AGU. RECONFERENCIA", "EM ANALISE", "PENDENTE", "FORA DO PRAZO"}
    import unicodedata
    def norm(value):
        return "".join(c for c in unicodedata.normalize("NFKD", value.upper()) if not unicodedata.combining(c))
    def store(value):
        import re
        match = re.match(r"^0*(\d+)(?:\s|-|$)", value)
        return match.group(1) if match else value.strip().upper()
    by_code = {}
    for record in occurrences():
        if record.is_test or store(record.destination) != store(body.destination) or norm(record.status) not in pending:
            continue
        for item in record.items:
            by_code.setdefault(item.code, []).append({"occurrence":record.code, "reason":record.reason, "quantity":str(item.quantity), "package":item.package, "total":str(item.total) if item.total is not None else None})
    return {"items":[{"code":i.code,"inventory_quantity":str(i.quantity),"inventory_value":str(i.value) if i.value is not None else None,"pending":by_code.get(i.code, [])} for i in body.items],
            "note":"Cruzamento por loja e PLU. Quantidades de embalagens diferentes não são somadas. Nenhum ajuste contábil é aplicado."}
