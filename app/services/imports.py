import hashlib
import json

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import store_key
from app.models import NativeTicket
from app.services.memberships import lock_organization
from sqlalchemy import select
from app.repositories import occurrences
from app.schemas import Record
from app.security import require_store


def check_preview(principal, result):
    records = []
    warnings = result["warnings"]
    blocked = result.get("blocked", False)
    for index, data in enumerate(result["records"], 1):
        try:
            record = Record.model_validate(data)
        except ValidationError:
            warnings.append(f"Registro {index}: campos fora do formato aceito.")
            blocked = True
            continue
        require_store(principal, record.destination)
        records.append(record)
    return {**result, "records": records, "warnings": warnings, "blocked": blocked}


def confirm(engine, principal, records, key=None):
    pairs = {(store_key(record.destination), record.code) for record in records}
    if not records or len(records) > 10000 or len(pairs) != len(records):
        raise HTTPException(422, "Lote vazio, excessivo ou com chamados duplicados na mesma loja.")
    for record in records:
        require_store(principal, record.destination)
        if record.source == "native":
            raise HTTPException(422, "Use a abertura nativa de chamados.")
        item_keys = {(item.code, item.package) for item in record.items}
        if len(item_keys) != len(record.items):
            raise HTTPException(422, "Itens duplicados no mesmo chamado.")
    ordered = sorted(records, key=lambda record: (store_key(record.destination), record.code))
    payload = json.dumps([record.model_dump(mode="json") for record in ordered], separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with occurrences.transaction(engine) as session:
        lock_organization(session, "tickets", principal.organization)
        # Keys identify retries. Without one, repeat imports remain upserts and can restore an older snapshot.
        if key:
            existing = occurrences.register_batch(session, principal, key, digest, len(records))
            if existing:
                if existing.digest != digest:
                    raise HTTPException(409, "Chave de idempotência já usada em outro lote.")
                return {"saved": existing.count, "replayed": True}
        for record in ordered:
            if session.scalar(select(NativeTicket.occurrence_id).where(
                NativeTicket.organization == principal.organization,
                NativeTicket.store == store_key(record.destination),
                NativeTicket.external_code == record.code,
            ).limit(1)) is not None:
                raise HTTPException(409, "Chamado externo já registrado pelo fluxo nativo.")
            row, created = occurrences.get_or_create(session, principal, record)
            previous = None if created else occurrences.serialize(row)
            if previous and previous.source == "native":
                raise HTTPException(409, "Edite o chamado pela tela de chamados nativos.")
            if previous and previous.source != record.source and previous.items and record.items:
                raise HTTPException(409, "Um chamado tem itens de outra origem. Faça a conferência manual.")
            occurrences.merge(row, record)
            if previous is None or previous != occurrences.serialize(row):
                occurrences.audit(session, principal, row, "import", previous.status if previous else None)
    return {"saved": len(records), "replayed": False}
