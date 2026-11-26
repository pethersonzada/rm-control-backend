from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile

from app import parser, spreadsheet
from app.config import Identity
from app.database import database
from app.schemas import Paste, Record, Spreadsheet
from app.security import writer
from app.services import imports

router = APIRouter(prefix="/imports")
Writer = Annotated[Identity, Depends(writer)]


@router.post("/preview")
def preview(body: Paste, principal: Writer, request: Request):
    with request.app.state.limiter.import_slot():
        return imports.check_preview(principal, parser.parse(body.text, body.target_code))


@router.post("/spreadsheet")
def spreadsheet_legacy(body: Spreadsheet, principal: Writer, request: Request):
    with request.app.state.limiter.import_slot():
        try:
            result = spreadsheet.parse_workbook(body.content_base64, body.sheet, body.skip_invalid)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
    return imports.check_preview(principal, result)


@router.post("/workbook")
def workbook(
    principal: Writer, request: Request, file: Annotated[UploadFile, File()],
    sheet: Annotated[str | None, Form(max_length=31)] = None,
    skip_invalid: Annotated[bool, Form()] = False,
):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(422, "Selecione um arquivo XLSX.")
    if not file.size or file.size > spreadsheet.MAX_FILE:
        raise HTTPException(413, "O arquivo deve ter até 21 MB.")
    with request.app.state.limiter.import_slot():
        try:
            result = spreadsheet.parse_file(file.file, sheet or None, skip_invalid)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
    return imports.check_preview(principal, result)


@router.post("/confirm")
def confirm(
    records: list[Record], principal: Writer, request: Request, engine=Depends(database),
    idempotency_key: Annotated[str | None, Header(pattern=r"^[a-zA-Z0-9_-]{1,64}$")] = None,
):
    with request.app.state.limiter.import_slot():
        return imports.confirm(engine, principal, records, idempotency_key)
