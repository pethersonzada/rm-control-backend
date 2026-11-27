import base64
from datetime import date
from io import BytesIO
from random import Random
from time import monotonic
from zoneinfo import ZoneInfo

import pytest
from openpyxl import Workbook

from app.config import Calendar
from app.services.deadlines import deadline
from app.spreadsheet import parse_file
from conftest import headers

HEADER = ["DATA RECEBIMENTO", "LOJA", "PLU", "N° CHAMADO", "TIPO DA DIVERGÊNCIA",
          "QTD DIVERGENTE", "VALOR DA DIVERGÊNCIA", "VOLUME"]
ROW = ["01/01/2026", "002", "100", "90001", "SOBRA", "1,25", "-12,55", "CX-12"]


def workbook_bytes(*rows, second=False):
    book = Workbook()
    book.active.title = "Sintética"
    for row in (HEADER, *rows):
        book.active.append(row)
    if second:
        other = book.create_sheet("Outra")
        other.append(HEADER)
        other.append(ROW)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_signed_values_and_sheet_selection():
    data = workbook_bytes(ROW, second=True)
    with pytest.raises(ValueError, match="mais de uma aba"):
        parse_file(BytesIO(data))
    result = parse_file(BytesIO(data), "Sintética")
    assert result["records"][0]["items"][0]["quantity"] == "1.25"
    assert result["records"][0]["items"][0]["total"] == "-12.55"
    assert not result["blocked"]


def test_invalid_rows_and_formula_without_cache():
    no_cache = [*ROW]
    no_cache[2], no_cache[3], no_cache[6] = "400", "90002", "=10+5"
    partial = workbook_bytes(ROW, ROW, no_cache)
    assert parse_file(BytesIO(partial))["blocked"]
    result = parse_file(BytesIO(partial), skip_invalid=True)
    assert result["counts"] == {"rows": 3, "invalid_rows": 1, "rows_without_value": 1, "omitted_rows": 2}
    assert result["records"][0]["items"][0]["total"] is None


def test_corrupt_workbook(workspace):
    client, _ = workspace
    rejected = client.post("/imports/workbook", files={"file": ("sintetica.xlsx", b"not-xlsx")}, headers=headers())
    assert rejected.status_code == 422


def test_synthetic_14_to_20_mb(tmp_path, workspace):
    client, _ = workspace
    source = tmp_path / "sintetica.xlsx"
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Sintética")
    sheet.append([*HEADER, "Coluna de volume sintético"])
    random = Random(90001)
    row_count = 9300
    for index in range(row_count):
        sheet.append([
            ROW[0], ROW[1], 10000 + index, 90000 + index // 50, *ROW[4:],
            base64.b64encode(random.randbytes(1750)).decode("ascii"),
        ])
    book.save(source)
    size = source.stat().st_size
    assert 14_000_000 < size < 20_000_000
    start = monotonic()
    with source.open("rb") as stream:
        response = client.post("/imports/workbook", files={"file": (source.name, stream)}, headers=headers())
    elapsed = monotonic() - start
    assert response.status_code == 200
    result = response.json()
    assert not result["blocked"] and result["counts"]["rows"] == row_count
    assert sum(len(record["items"]) for record in result["records"]) == row_count
    persisted = client.post("/imports/confirm", json=result["records"], headers={**headers(), "Idempotency-Key": "historico"})
    assert persisted.status_code == 200 and persisted.json()["saved"] == 186
    print(f"XLSX {size} bytes / {row_count} linhas / prévia HTTP {elapsed:.2f} s")


def test_business_calendar_with_holiday():
    calendar = Calendar(2, "received_at", ZoneInfo("America/Recife"), frozenset({date(2026, 1, 12)}))
    assert deadline({"received_at": "09/01/2026"}, calendar, date(2026, 1, 15)) == {
        "due_at": "2026-01-14", "deadline_passed": True,
    }
    assert deadline({"received_at": None}, calendar) == {}


def test_summary_deadlines_are_opt_in(workspace):
    from dataclasses import replace
    from test_api import RECORD
    client, _ = workspace
    auth = headers()
    assert client.post("/imports/confirm", json=[RECORD], headers=auth).status_code == 200
    assert client.get("/summary", headers=auth).json()["deadline_counts"] is None
    calendar = Calendar(2, "received_at", ZoneInfo("America/Recife"), frozenset())
    client.app.state.settings = replace(client.app.state.settings, calendars={"alpha": calendar})
    # The app's summary uses request state, not a guessed corporate SLA.
    from app.services.operations import totals
    identity = next(p for p in client.app.state.settings.identities.values() if p.subject == "editor")
    counts = totals(client.app.state.engine, identity, calendar)["deadline_counts"]
    assert sum(counts.values()) == 1 and counts["missing_date"] == 0
    assert client.get("/summary", headers=auth).json()["deadline_counts"] is not None
