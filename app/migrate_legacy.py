"""Copy a validated, single-organization legacy dataset after rehearsal."""

import argparse
import hashlib
import os

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Identity, store_key
from app.database import connect
from app.schemas import Record
from app.services.imports import confirm


def main():
    parser = argparse.ArgumentParser(description="Valida e copia chamados do protótipo para uma organização.")
    parser.add_argument("--organization", required=True)
    parser.add_argument("--stores", nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= len(args.organization) <= 80 or not args.organization.replace("_", "").replace("-", "").isalnum():
        parser.error("Identificador da organização inválido.")
    source_url, target_url = os.getenv("LEGACY_DATABASE_URL"), os.getenv("DATABASE_URL")
    if not source_url or not target_url:
        parser.error("Defina LEGACY_DATABASE_URL e DATABASE_URL.")
    stores = frozenset(map(store_key, args.stores))
    source = connect(source_url)
    target = connect(target_url)
    records = []
    rejected = 0
    try:
        with source.connect() as connection:
            for payload in connection.scalars(text("SELECT payload FROM occurrences ORDER BY code LIMIT 10001")):
                try:
                    record = Record.model_validate_json(payload)
                    if store_key(record.destination) not in stores:
                        rejected += 1
                        continue
                    records.append(record)
                except ValidationError:
                    rejected += 1
        print(f"Registros válidos: {len(records)}. Rejeitados: {rejected}.")
        if rejected or not 0 < len(records) <= 10000:
            parser.exit(1, "Revise as rejeições ou reduza o lote antes de gravar.\n")
        if args.apply:
            user = Identity("legacy-migration", args.organization, "admin", stores)
            digest = hashlib.sha256()
            for record in records:
                digest.update(record.model_dump_json().encode())
            saved = confirm(target, user, records, digest.hexdigest())
            print(f"Chamados processados: {saved['saved']}. Repetição: {saved['replayed']}.")
    except (SQLAlchemyError, HTTPException):
        parser.exit(1, "Migração interrompida. Verifique permissões e conflitos.\n")
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    main()
