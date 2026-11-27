"""Read-only import comparison and optional lookup for a standalone detail copy."""
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import store_key
from app.models import NativeTicket, Occurrence
from app.repositories.occurrences import scoped, serialize
from app.schemas import Record
from app.security import require_store


def detail_seed(engine, principal, code, store=None):
    if not code:
        return None
    if store:
        require_store(principal, store)
    with Session(engine) as session:
        query = scoped(principal).where(Occurrence.code == code)
        if store:
            query = query.where(Occurrence.store == store_key(store))
        rows = session.scalars(query.limit(2)).all()
        if len(rows) > 1:
            raise HTTPException(409, "Código presente em mais de uma loja. Selecione a loja explicitamente.")
        if not rows:
            return None
        row = rows[0]
        if row.source == "native":
            raise HTTPException(409, "Edite este chamado pelo formulário nativo.")
        # The stored metadata identifies the target; it is not evidence of new status or quantities.
        return dict(code=row.code, destination=row.destination, origin=row.origin, load=row.load,
                    reason=row.reason, status="DESCONHECIDO", items=[], invoices=[])


def changes(old, record):
    current = old.model_dump()
    incoming = record.model_dump()
    for key, value in incoming.items():
        if key in {"code", "source", "items", "invoices"} or value is None:
            continue
        if key in {"origin", "load", "reason"} and not value:
            continue
        if key == "status" and value == "DESCONHECIDO":
            continue
        if current[key] != value:
            return True
    previous = {(item.code, item.package): item for item in old.items}
    for item in record.items:
        known = previous.get((item.code, item.package))
        if known is None:
            return True
        proposed = item.model_dump()
        if item.quantity == known.quantity:
            for field in ("total", "unit_value"):
                if proposed[field] is None:
                    proposed[field] = getattr(known, field)
        if proposed["category"] is None:
            proposed["category"] = known.category
        if proposed != known.model_dump():
            return True
    return not {(item.number, item.series) for item in record.invoices} <= {
        (item.number, item.series) for item in old.invoices
    }


def compare_preview(engine, principal, result):
    if engine is None:
        raise HTTPException(503, "Banco indisponível.")
    records = result["records"]
    counts = dict(new=0, updated=0, unchanged=0, conflicts=0)
    classifications = []
    warnings = result["warnings"]
    grouped = defaultdict(set)
    for record in records:
        grouped[store_key(record.destination)].add(record.code)
    existing, external = {}, set()
    with Session(engine) as session:
        for store, codes in grouped.items():
            codes = sorted(codes)
            for start in range(0, len(codes), 200):
                part = codes[start:start+200]
                for row in session.scalars(scoped(principal).where(Occurrence.store == store, Occurrence.code.in_(part))):
                    existing[(store, row.code)] = serialize(row)
                external.update((store, code) for code in session.scalars(select(NativeTicket.external_code).where(
                    NativeTicket.organization == principal.organization, NativeTicket.store == store,
                    NativeTicket.external_code.in_(part),
                )))
    seen = set()
    for record in records:
        key = (store_key(record.destination), record.code)
        old = existing.get(key)
        item_keys = {(item.code, item.package) for item in record.items}
        conflict = (key in seen or key in external or len(item_keys) != len(record.items)
                    or record.source == "native" or (old and (old.source == "native" or
                    (old.source != record.source and old.items and record.items))))
        seen.add(key)
        if conflict:
            state = "conflicts"
            result["blocked"] = True
            if len(warnings) < 100:
                warnings.append(f"Chamado {record.code}: conflito com registro existente ou duplicidade no lote.")
        elif old is None:
            state = "new"
        elif changes(old, record):
            state = "updated"
        else:
            state = "unchanged"
        counts[state] += 1
        classifications.append({"code": record.code, "destination": record.destination, "state": state})
    return {**result, "comparison": counts, "classifications": classifications}
