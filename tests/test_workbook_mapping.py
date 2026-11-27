from io import BytesIO

from openpyxl import Workbook
from app.spreadsheet import parse_file
from conftest import headers


def file():
    wb = Workbook()
    ws = wb.active
    ws.title = "Controle"
    ws.append(["Recebido", "Unidade", "Produto", "Ticket", "Motivo", "Quantidade", "Valor"])
    ws.append(["2026-11-25", "2", "000123", "00042", "FALTA", 1.5, -12.34])
    ws.append(["2026-11-25", "3", "000123", "00042", "SOBRA", 2, None])
    data = BytesIO()
    wb.save(data)
    wb.close()
    data.seek(0)
    return data


MAPPING = {"DATA RECEBIMENTO": "Recebido", "LOJA": "Unidade", "PLU": "Produto",
           "N° CHAMADO": "Ticket", "TIPO DA DIVERGENCIA": "Motivo",
           "QTD DIVERGENTE": "Quantidade", "VALOR DA DIVERGENCIA": "Valor"}


def test_custom_headers_and_store_scope():
    result = parse_file(file(), "Controle", False, MAPPING)
    assert not result["blocked"] and len(result["records"]) == 2
    assert result["records"][0]["items"][0]["total"] == "-12.34"
    assert result["records"][1]["items"][0]["total"] is None


def test_multipart_mapping(workspace):
    import json
    client, _ = workspace
    result = client.post("/imports/workbook", headers=headers(),
                         files={"file": ("controle.xlsx", file(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                         data={"sheet": "Controle", "mapping": json.dumps(MAPPING)})
    assert result.status_code == 200, result.text
    assert result.json()["comparison"]["new"] == 2
    assert client.get("/occurrences", headers=headers()).json()["records"] == []
