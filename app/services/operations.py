import unicodedata
from collections import defaultdict
from decimal import Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import store_key
from app.models import CatalogProduct, FinancialReview, Occurrence, OccurrenceItem, UnitReference
from app.parser import STATUSES
from app.repositories import occurrences
from app.security import require_store

PENDING = {"AGU. ANALISE", "AGU. RECONFERENCIA", "EM ANALISE", "PENDENTE", "FORA DO PRAZO"}


def normalized(value):
    return " ".join("".join(
        char for char in unicodedata.normalize("NFKD", value.upper()) if not unicodedata.combining(char)
    ).split())


def totals(engine, principal, calendar=None):
    columns = (OccurrenceItem.total, Occurrence.reason)
    scoped = (
        Occurrence.organization == principal.organization, Occurrence.store.in_(principal.stores),
        or_(Occurrence.is_test.is_(None), Occurrence.is_test.is_(False)),
    )
    result = {reason: Decimal("0") for reason in ("FALTA", "SOBRA", "AVARIA")}
    missing = 0
    signed = False
    unknown_reason = 0
    deadlines = None
    financial = {key: None for key in ("approved", "recovered", "pending")}
    financial_counts = {key: 0 for key in financial}
    with Session(engine) as session:
        query = select(*columns).join(Occurrence).where(*scoped).execution_options(yield_per=500)
        for value, reason in session.execute(query):
            if value is None:
                missing += 1
            elif normalized(reason) in result:
                result[normalized(reason)] += value
                signed |= value < 0
            else:
                unknown_reason += 1
        status_counts = dict(session.execute(
            select(Occurrence.status, func.count()).where(*scoped).group_by(Occurrence.status)
        ).all())
        count = sum(status_counts.values())
        for review in session.scalars(select(FinancialReview).join(Occurrence).where(*scoped).execution_options(yield_per=500)):
            for key in financial:
                value = getattr(review,key)
                if value is not None:
                    financial[key] = (financial[key] or Decimal("0")) + value
                    financial_counts[key] += 1
        if calendar:
            from app.services.deadlines import deadline
            from datetime import datetime
            today = datetime.now(calendar.timezone).date()
            deadlines = {"within": 0, "overdue": 0, "missing_date": 0}
            field = getattr(Occurrence, calendar.start_field)
            for status, value in session.execute(select(Occurrence.status, field).where(*scoped).execution_options(yield_per=500)):
                if normalized(status) not in PENDING:
                    continue
                due = deadline({calendar.start_field: value}, calendar, today)
                key = "missing_date" if not due else "overdue" if due["deadline_passed"] else "within"
                deadlines[key] += 1
    return {
        "occurrences": count, "items_without_value": missing, "status_counts": status_counts,
        "items_unknown_reason": unknown_reason, "deadline_counts": deadlines,
        "totals_by_reason": {reason: format(value, "f") for reason, value in result.items()},
        "net_reported": None if signed or unknown_reason else format(result["SOBRA"] - result["FALTA"] - result["AVARIA"], "f"),
        **{key: format(value,"f") if value is not None else None for key,value in financial.items()},
        "financial_counts": financial_counts,
        "note": ("Saldo indisponível enquanto houver valores informados com sinais que precisam de validação. " if signed else "")
                + ("Há valores com motivo não classificado; o saldo não foi calculado. " if unknown_reason else "")
                + "Aprovado, recuperado e pendente somam apenas os valores explicitamente conferidos, sem estimar dados ausentes.",
    }


def reconcile(engine, principal, body):
    require_store(principal, body.destination)
    codes = {item.code for item in body.items}
    if len(codes) != len(body.items):
        raise HTTPException(422, "PLUs repetidos no inventário; consolide os registros antes da análise.")
    selected = defaultdict(list)
    references = {}
    def converted(code, package, quantity):
        ref = references.get((code, package))
        if ref is None:
            return None
        with localcontext() as context:
            context.prec = 64
            value = quantity * ref.factor
        return {"quantity":format(value,"f"),"unit":ref.base_unit,"reference":ref.reference}

    query = select(
        Occurrence.code, Occurrence.reason, Occurrence.status, OccurrenceItem,
    ).join(Occurrence).where(
        Occurrence.organization == principal.organization,
        Occurrence.store == store_key(body.destination),
        Occurrence.is_test.is_not(True),
        OccurrenceItem.code.in_(codes),
    ).order_by(Occurrence.id, OccurrenceItem.id).limit(20001)
    with Session(engine) as session:
        if any(item.package for item in body.items):
            for offset in range(0,len(codes),200):
                chunk=sorted(codes)[offset:offset+200]
                rows=session.scalars(select(UnitReference).join(CatalogProduct,(CatalogProduct.organization==UnitReference.organization)&(CatalogProduct.code==UnitReference.code)).where(UnitReference.organization==principal.organization,UnitReference.active.is_(True),CatalogProduct.active.is_(True),UnitReference.code.in_(chunk)))
                for ref in rows:
                    if len(references)>=50000:
                        raise HTTPException(422,"Reduza os PLUs para normalização.")
                    references[(ref.code,ref.package)]=ref
        for index, (code, reason, status, item) in enumerate(session.execute(query)):
            if index == 20000:
                raise HTTPException(422, "Resultado excede o limite. Confira um conjunto menor de PLUs.")
            if normalized(status) in PENDING:
                selected[item.code].append({
                    "occurrence": code, "reason": reason, "quantity": format(item.quantity, "f"),
                    "package": item.package, "total": format(item.total, "f") if item.total is not None else None,
                    "normalized": converted(item.code,item.package,item.quantity),
                })
    return {"items": [{
        "code": item.code, "inventory_quantity": format(item.quantity, "f"),
        "inventory_value": format(item.value, "f") if item.value is not None else None,
        "pending": selected[item.code],
        "inventory_normalized": converted(item.code,item.package,item.quantity) if item.package else None,
    } for item in body.items],
        "note": "Cruzamento por loja e PLU. A unidade original é preservada; normalização informativa somente com fator conferido e embalagem explícita. Nenhum ajuste contábil é aplicado."}


def change_status(engine, principal, store, code, body):
    require_store(principal, store)
    status = normalized(body.status)
    if status not in STATUSES:
        raise HTTPException(422, "Status de destino desconhecido.")
    with occurrences.transaction(engine) as session:
        row = occurrences.locked(session, principal, store_key(store), code)
        if row is None:
            raise HTTPException(404, "Chamado não encontrado.")
        if row.status != body.expected_status:
            raise HTTPException(409, "O status mudou. Atualize a ocorrência antes de continuar.")
        if status != row.status:
            previous = row.status
            row.status = status
            occurrences.audit(session, principal, row, "status", previous)
        return occurrences.serialize(row)
