import os
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
app = FastAPI(title="RM Control API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","), allow_methods=["GET","POST"], allow_headers=["Content-Type"])

class Item(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    description: str
    quantity: Decimal = Field(ge=0)
    package: str
    total: Decimal | None = Field(default=None, ge=0)

class Record(BaseModel):
    code: str = Field(pattern=r"^\d+$", max_length=40)
    origin: str
    destination: str
    load: str
    reason: str
    included_at: str | None = None
    status: str
    items: list[Item] = Field(default_factory=list, max_length=10000)

class Paste(BaseModel):
    text: str = Field(min_length=1, max_length=2000000)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/imports/preview")
def preview(body: Paste):
    return parse(body.text)

@app.get("/occurrences")
def occurrences():
    with Session(engine) as session:
        return [Record.model_validate_json(row.payload) for row in session.scalars(select(Occurrence).order_by(Occurrence.code)).all()]

@app.post("/imports/confirm")
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
                merged = {(i.code, i.package): i for i in previous.items}
                for item in record.items:
                    key = (item.code, item.package)
                    if item.total is None and key in merged and item.quantity == merged[key].quantity:
                        item.total = merged[key].total
                    merged[key] = item
                record.items = list(merged.values())
                if record.status == "DESCONHECIDO":
                    record.status = previous.status
                old.payload = record.model_dump_json()
            else:
                session.add(Occurrence(code=record.code, payload=record.model_dump_json()))
    return {"saved": len(records)}
